#!/usr/bin/env python3
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

MAX_BODY_LENGTH = 180
INTERACTIVE_TOOL_NAMES = {"ask_user", "exit_plan_mode"}


def is_headless_copilot() -> bool:
    pid = os.getppid()
    for _ in range(16):
        if pid <= 1:
            break
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,args="],
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
            if result.returncode != 0:
                break
            parent, command = result.stdout.strip().split(maxsplit=1)
            pid = int(parent)
            argv = shlex.split(command)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            print(f"cmux-notify: cannot inspect hook ancestry: {error}", file=sys.stderr)
            break
        if argv and os.path.basename(argv[0]) in ("copilot", "copilot.exe"):
            return argv[1:2] == ["--server"]
    return False


def parse_hook_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"cmux-notify: invalid hook payload: {error}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def normalize_body(text: str) -> str:
    return " ".join(text.split())[:MAX_BODY_LENGTH]


def find_first_string(payload: dict, keys: tuple[str, ...]) -> str:
    stack = [payload]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue

        for key in keys:
            value = current.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value

        for value in current.values():
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        stack.append(item)
    return ""


def extract_session_title(payload: dict) -> str:
    return find_first_string(
        payload,
        ("sessionTitle", "session_title", "sessionName", "session_name"),
    )


def extract_working_directory(payload: dict) -> str:
    directory = find_first_string(
        payload,
        (
            "cwd",
            "workingDirectory",
            "working_directory",
            "projectPath",
            "project_path",
            "workspacePath",
            "workspace_path",
            "directory",
        ),
    )
    if directory:
        return directory

    env_pwd = os.environ.get("PWD")
    if isinstance(env_pwd, str) and env_pwd.strip():
        return env_pwd.strip()
    try:
        return os.getcwd()
    except OSError:
        return ""


def extract_project_name(payload: dict) -> str:
    directory = extract_working_directory(payload)
    if not directory:
        return ""
    normalized = os.path.normpath(directory)
    project_name = os.path.basename(normalized)
    return project_name or normalized


def build_context_subtitle(payload: dict) -> str:
    session_title = extract_session_title(payload)
    project_name = extract_project_name(payload)
    if session_title and project_name:
        return normalize_body(f"{session_title} — {project_name}")
    return normalize_body(session_title or project_name)


def resolve_cmux_binary():
    preferred_cmux = "/Applications/cmux.app/Contents/Resources/bin/cmux"
    if os.path.isfile(preferred_cmux) and os.access(preferred_cmux, os.X_OK):
        return preferred_cmux
    return shutil.which("cmux")


def _safe_filename(text: str) -> str:
    return text.replace("/", "_").replace(":", "_").replace(" ", "_")


def get_workspace_ref() -> str:
    for key in ("CMUX_WORKSPACE_ID", "CMUX_WORKSPACE_REF"):
        value = os.environ.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def state_file_path() -> str:
    ref = get_workspace_ref()
    if not ref:
        return ""
    safe = _safe_filename(ref)
    return os.path.join(tempfile.gettempdir(), f"cmux-copilot-{safe}.json")


def read_state() -> dict:
    path = state_file_path()
    if not path:
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict) -> None:
    path = state_file_path()
    if not path:
        return
    try:
        with open(path, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def remove_state() -> None:
    path = state_file_path()
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def update_workspace_title(cmux: str, title: str) -> bool:
    try:
        result = subprocess.run(
            [cmux, "rename-workspace", title],
            check=False,
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def update_workspace_subtitle(cmux: str, message: str) -> bool:
    """Update sidebar intent status silently via set-status."""
    cmd = [cmux, "set-status", "intent", message]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def clear_workspace_subtitle(cmux: str) -> bool:
    """Clear intent status from sidebar."""
    try:
        result = subprocess.run(
            [cmux, "clear-status", "intent"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_attention_status(cmux: str, message: str) -> bool:
    """Show attention indicator in sidebar with bell icon."""
    cmd = [cmux, "set-status", "attention", message, "--icon", "bell.fill"]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def clear_attention_status(cmux: str) -> bool:
    """Clear attention indicator from sidebar."""
    try:
        result = subprocess.run(
            [cmux, "clear-status", "attention"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_running_status(cmux: str) -> bool:
    """Show green running indicator in sidebar."""
    cmd = [cmux, "set-status", "running", "Running", "--color", "#34c759", "--icon", "bolt.fill"]
    try:
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def clear_running_status(cmux: str) -> bool:
    """Clear running indicator from sidebar."""
    try:
        result = subprocess.run(
            [cmux, "clear-status", "running"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def signal_session_start(cmux: str) -> bool:
    try:
        result = subprocess.run(
            [cmux, "claude-hook", "session-start"],
            input="{}",
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def signal_session_stop(cmux: str) -> bool:
    try:
        result = subprocess.run(
            [cmux, "claude-hook", "stop"],
            input="{}",
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_workspace_title(payload: dict) -> str:
    session_title = extract_session_title(payload)
    project_name = extract_project_name(payload)
    if session_title and project_name:
        return f"{project_name} — {session_title}"
    return project_name or session_title or ""


# ── Sidebar state machine ──────────────────────────────────────────────
# Event            │ intent (subtitle)     │ attention  │ running   │ claude-hook
# ─────────────────┼───────────────────────┼────────────┼───────────┼────────────
# sessionStart     │ clear                 │ clear      │ SET green │ stop (reset)
# report_intent    │ SET <intent text>     │ clear      │ SET green │ stop if !started
# interactive tool │ (unchanged)           │ SET <msg>  │ clear     │ —
# other tool       │ (unchanged)           │ clear      │ SET green │ —
# sessionEnd       │ clear                 │ clear      │ clear     │ stop
# ───────────────────────────────────────────────────────────────────────
# Notification popup (cmux notify) is sent independently by main() after
# the handler runs; it must NOT duplicate sidebar status text.


def handle_session_start(payload: dict) -> None:
    cmux = resolve_cmux_binary()
    if not cmux:
        return

    remove_state()
    clear_attention_status(cmux)
    clear_workspace_subtitle(cmux)
    signal_session_stop(cmux)
    set_running_status(cmux)

    state = {}
    state["started"] = True

    title = build_workspace_title(payload)
    if title:
        update_workspace_title(cmux, title)
        state["title"] = title

    write_state(state)


def handle_report_intent(payload: dict) -> None:
    cmux = resolve_cmux_binary()
    if not cmux:
        return

    tool_args = extract_tool_args(payload)
    intent = tool_args.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return

    state = read_state()

    if not state.get("started"):
        signal_session_stop(cmux)
        set_running_status(cmux)
        state["started"] = True

    title = build_workspace_title(payload)
    if title and title != state.get("title"):
        update_workspace_title(cmux, title)
        state["title"] = title

    clear_attention_status(cmux)
    set_running_status(cmux)
    write_state(state)
    update_workspace_subtitle(cmux, intent.strip())


def handle_session_end(payload: dict) -> None:
    cmux = resolve_cmux_binary()
    if not cmux:
        return

    clear_workspace_subtitle(cmux)
    clear_attention_status(cmux)
    clear_running_status(cmux)
    signal_session_stop(cmux)
    remove_state()


def is_caller_surface_focused(cmux: str) -> bool:
    try:
        result = subprocess.run(
            [cmux, "identify", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0 or not result.stdout.strip():
        return False

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False

    if not isinstance(data, dict):
        return False

    focused = data.get("focused")
    caller = data.get("caller")
    if not isinstance(focused, dict) or not isinstance(caller, dict):
        return False

    focused_surface = focused.get("surface_ref")
    caller_surface = caller.get("surface_ref")
    focused_workspace = focused.get("workspace_ref")
    caller_workspace = caller.get("workspace_ref")
    return (
        isinstance(focused_surface, str)
        and isinstance(caller_surface, str)
        and focused_surface == caller_surface
        and isinstance(focused_workspace, str)
        and isinstance(caller_workspace, str)
        and focused_workspace == caller_workspace
    )


def is_cmux_frontmost() -> bool:
    osascript = shutil.which("osascript")
    if not osascript:
        return False

    expected_bundle = (os.environ.get("CMUX_BUNDLE_ID") or "com.cmuxterm.app").strip()
    if not expected_bundle:
        return False

    script = 'tell application "System Events" to get bundle identifier of first process whose frontmost is true'
    try:
        result = subprocess.run(
            [osascript, "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0 and result.stdout.strip() == expected_bundle


def is_same_cmux_surface_active() -> bool:
    cmux = resolve_cmux_binary()
    return bool(cmux and is_cmux_frontmost() and is_caller_surface_focused(cmux))


def extract_tool_name(payload: dict) -> str:
    for key in ("toolName", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_tool_args(payload: dict) -> dict:
    for key in ("toolArgs", "tool_args", "arguments"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def has_interaction_markers(tool_args: dict) -> bool:
    question = tool_args.get("question")
    if isinstance(question, str) and question.strip():
        return True

    for key in ("choices", "actions"):
        value = tool_args.get(key)
        if isinstance(value, list) and value:
            return True

    for key in ("recommendedAction", "recommended_action"):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return True

    return False


def extract_summary_hint(tool_args: dict) -> str:
    summary = tool_args.get("summary")
    if not isinstance(summary, str):
        return ""

    for line in summary.splitlines():
        candidate = line.strip().lstrip("-* ").strip()
        if candidate:
            return normalize_body(candidate)
    return ""


def is_interactive_tool_use(tool_name: str, tool_args: dict) -> bool:
    return tool_name in INTERACTIVE_TOOL_NAMES or has_interaction_markers(tool_args)


def default_interaction_subtitle(tool_name: str) -> str:
    if tool_name == "ask_user":
        return "Needs answer"
    if tool_name == "exit_plan_mode":
        return "Needs approval"
    return "Needs input"


def build_interaction_body(tool_name: str, tool_args: dict) -> str:
    question = tool_args.get("question")
    if isinstance(question, str) and question.strip():
        question = normalize_body(question)
    else:
        question = ""

    if tool_name == "ask_user":
        return question or "Copilot needs your input."

    summary_hint = extract_summary_hint(tool_args)
    if tool_name == "exit_plan_mode":
        if summary_hint:
            return normalize_body(f"Plan is ready for approval: {summary_hint}")
        return "Plan is ready for your approval."

    if question:
        return question
    if summary_hint:
        return normalize_body(f"Action needed: {summary_hint}")
    if isinstance(tool_args.get("actions"), list) and tool_args.get("actions"):
        return "Copilot is waiting for your action."
    return "Copilot needs your input."


def notification_from_tool_use(payload: dict):
    tool_name = extract_tool_name(payload)
    tool_args = extract_tool_args(payload)
    if not is_interactive_tool_use(tool_name, tool_args):
        return None

    cmux = resolve_cmux_binary()
    if cmux:
        clear_running_status(cmux)
        set_attention_status(cmux, default_interaction_subtitle(tool_name))

    if is_same_cmux_surface_active():
        return None

    body = build_interaction_body(tool_name, tool_args)
    subtitle = build_context_subtitle(payload) or default_interaction_subtitle(tool_name)
    return ("Copilot CLI", subtitle, body)


def notification_from_session_end(payload: dict):
    if is_same_cmux_surface_active():
        return None

    reason = str(payload.get("reason") or "unknown")
    if reason == "complete":
        body = "Task finished."
    else:
        body = f"Task stopped ({reason})."
    subtitle = build_context_subtitle(payload) or "Session ended"
    return ("Copilot CLI", subtitle, body)


def build_notification(event_name: str, payload: dict):
    if event_name in ("preToolUse", "postToolUse"):
        return notification_from_tool_use(payload)
    if event_name == "sessionEnd":
        return notification_from_session_end(payload)
    return None


def notify(title: str, subtitle: str, body: str) -> None:
    cmux = resolve_cmux_binary()
    if cmux:
        command = [cmux, "notify", "--title", title]
        if subtitle:
            command.extend(["--subtitle", subtitle])
        if body:
            command.extend(["--body", body])
        try:
            result = subprocess.run(command, check=False, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0:
            return

    osascript = shutil.which("osascript")
    if osascript:
        script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
        if subtitle:
            script += f" subtitle {json.dumps(subtitle)}"
        subprocess.run([osascript, "-e", script], check=False)


def main() -> int:
    if is_headless_copilot():
        return 0
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = parse_hook_payload()

    if event_name in ("preToolUse", "postToolUse"):
        tool_name = extract_tool_name(payload)
        tool_args = extract_tool_args(payload)
        if tool_name == "report_intent":
            handle_report_intent(payload)
        elif not is_interactive_tool_use(tool_name, tool_args):
            cmux = resolve_cmux_binary()
            if cmux:
                clear_attention_status(cmux)
                set_running_status(cmux)
    elif event_name == "sessionStart":
        handle_session_start(payload)
    elif event_name == "sessionEnd":
        handle_session_end(payload)

    notification = build_notification(event_name, payload)
    if not notification:
        return 0
    notify(*notification)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
