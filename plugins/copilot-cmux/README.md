# copilot-cmux Copilot CLI plugin

This plugin sends Copilot CLI hook notifications to `cmux notify` with a macOS `osascript` fallback.

## Triggers

- **Workspace status**: `preToolUse` events for `report_intent` update the cmux sidebar silently:
  - **Title** → `"project — session goal"` (via `cmux rename-workspace`, updates when session title changes)
  - **Sidebar status** → current intent text (via `cmux set-status intent`, updates on every intent change)
  - **Status indicator** → `⚡️ running` (via `cmux claude-hook session-start` on first intent)
  - Clears any previous attention indicator when the agent resumes working
- **Needs input/action**: `preToolUse` events for interaction-required prompts (e.g., `ask_user`, `exit_plan_mode`):
  - Sets a `🔔 attention` sidebar status (via `cmux set-status attention --icon bell.fill`)
  - Sends a `cmux notify` popup notification (unless the caller surface is already focused)
- **Finished / stopped**: `sessionEnd` events (`complete`, `error`, `abort`, `timeout`, `user_exit`). Updates the sidebar status and signals `cmux claude-hook stop`.

## Files

- `plugin.json` — plugin manifest.
- `hooks.json` — hook bindings.
- `scripts/cmux_notify.py` — notification handler.

## Install

```bash
copilot plugin install copilot-cmux@ekroon-copilot-catalog
copilot plugin list
```

During local development:

```bash
copilot plugin install /absolute/path/to/copilot-catalog/plugins/copilot-cmux
```

Update to the latest marketplace version:

```bash
copilot plugin uninstall copilot-cmux
copilot plugin install copilot-cmux@ekroon-copilot-catalog
```

Uninstall:

```bash
copilot plugin uninstall copilot-cmux
```

## Behavior

Headless Copilot servers (`copilot --server`, including SDK clients) do not update the sidebar or send notifications. The handler checks its parent process chain before handling any event, so per-request SDK sessions cannot overwrite an interactive session's status or send completion alerts. Applications using the SDK remain responsible for their own completion notifications.

The check recognises the leading `--server` argument used by SDK launches. Later occurrences are not treated as server mode because `ps` can include unquoted prompt text.

If process inspection is unavailable, the handler reports the problem on stderr and preserves normal notification behaviour.

1. On every `report_intent` tool call, the plugin updates the cmux workspace sidebar silently:
   - Renames the workspace to `"<project> — <session title>"` (only when the title changes).
   - Sets the sidebar status to the current intent (e.g., "Fixing auth bug", "Running tests") via `cmux set-status`.
   - On the first intent of a session, signals `cmux claude-hook session-start` to show the `⚡️ running` indicator.
   - Clears any previous attention indicator when the agent resumes working.
2. For interaction-required `preToolUse` prompts (`ask_user`, `exit_plan_mode`):
   - Sets a `🔔 attention` indicator in the sidebar via `cmux set-status attention --icon bell.fill`.
   - Sends a `cmux notify` popup notification (skipped if the caller surface is already focused).
3. Sidebar status prefers context in the form `"<session title> — <project>"`; if context is unavailable it falls back to event labels.
4. On `sessionEnd`, the sidebar status is updated to the end reason and `cmux claude-hook stop` is signaled.
5. If `cmux` is unavailable or fails and `osascript` exists, it sends a macOS notification for actionable events.
6. If neither tool exists, it exits without failing the hook.
