"""Tests for cmux_notify.py sidebar state machine.

Mocks subprocess.run to capture the exact sequence of cmux commands
emitted for each event handler and the notification path, preventing
regressions where duplicate sidebar messages appear.
"""

import json
import io
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import cmux_notify


FAKE_CMUX = "/usr/local/bin/cmux"


class TestHeadlessCopilot(unittest.TestCase):
    @patch("cmux_notify.os.getppid", return_value=30)
    @patch("cmux_notify.subprocess.run")
    def test_sdk_server_through_shell_wrapper(self, run_mock, _ppid):
        run_mock.side_effect = [
            subprocess.CompletedProcess([], 0, "20 /bin/sh -c python3 hook.py\n"),
            subprocess.CompletedProcess([], 0, "10 /usr/local/bin/copilot --server --stdio\n"),
        ]
        self.assertTrue(cmux_notify.is_headless_copilot())
        self.assertEqual(run_mock.call_args_list[1].args[0][2], "20")

    @patch("cmux_notify.os.getppid", return_value=30)
    @patch("cmux_notify.subprocess.run")
    def test_interactive_session_is_not_suppressed(self, run_mock, _ppid):
        for command in [
            "/usr/local/bin/copilot --resume abc",
            'copilot -p "Explain --server"',
            "copilot -p Explain --server",
            "copilot -p --server",
            "copilot Explain --server",
            "copilot -- --server",
        ]:
            with self.subTest(command=command):
                run_mock.return_value = subprocess.CompletedProcess([], 0, f"20 {command}\n")
                self.assertFalse(cmux_notify.is_headless_copilot())

    @patch("cmux_notify.os.getppid", return_value=30)
    @patch("cmux_notify.subprocess.run")
    def test_unrelated_server_and_exited_parent(self, run_mock, _ppid):
        run_mock.side_effect = [
            subprocess.CompletedProcess([], 0, "20 other-tool --server\n"),
            subprocess.CompletedProcess([], 1, ""),
        ]
        self.assertFalse(cmux_notify.is_headless_copilot())

    @patch("cmux_notify.os.getppid", return_value=30)
    @patch("cmux_notify.subprocess.run", side_effect=OSError("ps unavailable"))
    @patch("sys.stderr", new_callable=io.StringIO)
    def test_inspection_failure_preserves_notifications(self, stderr, _run, _ppid):
        self.assertFalse(cmux_notify.is_headless_copilot())
        self.assertIn("cannot inspect hook ancestry", stderr.getvalue())

    @patch("cmux_notify.is_headless_copilot", return_value=True)
    @patch("cmux_notify.parse_hook_payload")
    @patch("cmux_notify.subprocess.run")
    def test_sdk_events_have_no_notification_or_sidebar_side_effects(self, run_mock, parse, _sdk):
        for event in ("sessionStart", "preToolUse", "sessionEnd"):
            with self.subTest(event=event), patch.object(sys, "argv", ["cmux_notify.py", event]):
                self.assertEqual(cmux_notify.main(), 0)
        parse.assert_not_called()
        run_mock.assert_not_called()

    @patch("cmux_notify.is_headless_copilot", return_value=False)
    @patch("cmux_notify.parse_hook_payload", return_value={"reason": "complete"})
    @patch("cmux_notify.handle_session_end")
    @patch("cmux_notify.is_same_cmux_surface_active", return_value=False)
    @patch("cmux_notify.notify")
    def test_interactive_session_still_notifies(self, notify, _active, end, _parse, _sdk):
        with patch.object(sys, "argv", ["cmux_notify.py", "sessionEnd"]):
            self.assertEqual(cmux_notify.main(), 0)
        end.assert_called_once()
        notify.assert_called_once()


def make_run_mock():
    """Create a subprocess.run mock that records calls and returns success."""
    mock = MagicMock()
    mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return mock


def cmux_commands(run_mock) -> list[list[str]]:
    """Extract the cmux command args from all subprocess.run calls."""
    return [call.args[0] for call in run_mock.call_args_list]


def cmux_command_summaries(run_mock) -> list[str]:
    """Summarize cmux commands as 'subcommand arg1 arg2 ...' strings."""
    result = []
    for cmd in cmux_commands(run_mock):
        # Skip the cmux binary path itself
        result.append(" ".join(cmd[1:]))
    return result


class TestSessionEnd(unittest.TestCase):
    """sessionEnd must clear sidebar state, never set intent text."""

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_end_clears_intent(self, run_mock, _remove, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_end({"reason": "complete"})

        summaries = cmux_command_summaries(run_mock)
        # Must clear intent, not set it
        assert "clear-status intent" in summaries, (
            f"Expected 'clear-status intent' but got: {summaries}"
        )
        for s in summaries:
            assert not s.startswith("set-status intent"), (
                f"sessionEnd must NOT set intent status, but found: {s}"
            )

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_end_clears_running(self, run_mock, _remove, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_end({"reason": "complete"})

        summaries = cmux_command_summaries(run_mock)
        assert "clear-status running" in summaries

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_end_signals_stop(self, run_mock, _remove, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_end({"reason": "complete"})

        summaries = cmux_command_summaries(run_mock)
        assert "claude-hook stop" in summaries

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_end_clears_attention(self, run_mock, _remove, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_end({"reason": "error"})

        summaries = cmux_command_summaries(run_mock)
        assert "clear-status attention" in summaries


class TestSessionStart(unittest.TestCase):
    """sessionStart must clear all statuses and set running."""

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.write_state")
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_start_clears_intent(self, run_mock, _remove, _write, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_start({})

        summaries = cmux_command_summaries(run_mock)
        assert "clear-status intent" in summaries
        for s in summaries:
            assert not s.startswith("set-status intent"), (
                f"sessionStart must clear intent, not set it, but found: {s}"
            )

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.write_state")
    @patch("cmux_notify.remove_state")
    @patch("subprocess.run")
    def test_session_start_sets_running(self, run_mock, _remove, _write, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cmux_notify.handle_session_start({})

        summaries = cmux_command_summaries(run_mock)
        running_cmds = [s for s in summaries if s.startswith("set-status running")]
        assert len(running_cmds) == 1, f"Expected exactly one set-status running, got: {summaries}"


class TestReportIntent(unittest.TestCase):
    """report_intent must set intent text and running indicator."""

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.write_state")
    @patch("cmux_notify.read_state", return_value={"started": True})
    @patch("subprocess.run")
    def test_report_intent_sets_subtitle(self, run_mock, _read, _write, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        payload = {
            "toolName": "report_intent",
            "toolArgs": {"intent": "Fixing bug"},
        }
        cmux_notify.handle_report_intent(payload)

        summaries = cmux_command_summaries(run_mock)
        assert "set-status intent Fixing bug" in summaries

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.write_state")
    @patch("cmux_notify.read_state", return_value={"started": True})
    @patch("subprocess.run")
    def test_report_intent_clears_attention(self, run_mock, _read, _write, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        payload = {
            "toolName": "report_intent",
            "toolArgs": {"intent": "Working"},
        }
        cmux_notify.handle_report_intent(payload)

        summaries = cmux_command_summaries(run_mock)
        assert "clear-status attention" in summaries

    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("cmux_notify.write_state")
    @patch("cmux_notify.read_state", return_value={"started": True})
    @patch("subprocess.run")
    def test_report_intent_sets_running(self, run_mock, _read, _write, _cmux):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        payload = {
            "toolName": "report_intent",
            "toolArgs": {"intent": "Working"},
        }
        cmux_notify.handle_report_intent(payload)

        summaries = cmux_command_summaries(run_mock)
        running_cmds = [s for s in summaries if s.startswith("set-status running")]
        assert len(running_cmds) == 1


class TestInteractiveTool(unittest.TestCase):
    """Interactive tools (ask_user, exit_plan_mode) must set attention and clear running."""

    @patch("cmux_notify.is_same_cmux_surface_active", return_value=True)
    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("subprocess.run")
    def test_ask_user_sets_attention(self, run_mock, _cmux, _active):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        payload = {
            "toolName": "ask_user",
            "toolArgs": {"question": "Which database?"},
        }
        cmux_notify.notification_from_tool_use(payload)

        summaries = cmux_command_summaries(run_mock)
        attention_cmds = [s for s in summaries if "set-status attention" in s]
        assert len(attention_cmds) == 1, f"Expected attention status, got: {summaries}"

    @patch("cmux_notify.is_same_cmux_surface_active", return_value=True)
    @patch("cmux_notify.resolve_cmux_binary", return_value=FAKE_CMUX)
    @patch("subprocess.run")
    def test_ask_user_clears_running(self, run_mock, _cmux, _active):
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        payload = {
            "toolName": "ask_user",
            "toolArgs": {"question": "Which database?"},
        }
        cmux_notify.notification_from_tool_use(payload)

        summaries = cmux_command_summaries(run_mock)
        assert "clear-status running" in summaries


class TestNonInteractiveTool(unittest.TestCase):
    """Non-interactive tools must clear attention and restore running."""

    def test_non_interactive_clears_attention(self):
        """Verify main() dispatches non-interactive tools to clear attention."""
        payload = {
            "toolName": "bash",
            "toolArgs": {"command": "ls"},
        }
        tool_name = cmux_notify.extract_tool_name(payload)
        tool_args = cmux_notify.extract_tool_args(payload)
        assert not cmux_notify.is_interactive_tool_use(tool_name, tool_args)


class TestNotificationContent(unittest.TestCase):
    """Notification popup content for sessionEnd."""

    @patch("cmux_notify.is_same_cmux_surface_active", return_value=False)
    def test_session_end_notification_complete(self, _active):
        result = cmux_notify.notification_from_session_end({"reason": "complete"})
        assert result is not None
        title, subtitle, body = result
        assert body == "Task finished."

    @patch("cmux_notify.is_same_cmux_surface_active", return_value=False)
    def test_session_end_notification_error(self, _active):
        result = cmux_notify.notification_from_session_end({"reason": "error"})
        assert result is not None
        _, _, body = result
        assert "error" in body

    @patch("cmux_notify.is_same_cmux_surface_active", return_value=True)
    def test_session_end_no_notification_when_focused(self, _active):
        result = cmux_notify.notification_from_session_end({"reason": "complete"})
        assert result is None


if __name__ == "__main__":
    unittest.main()
