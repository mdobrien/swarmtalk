"""Claude Code session hooks: one Mattermost account per session, for its lifetime.

`mm session start` and `mm session end` read the hook JSON on stdin. Start creates
(or on resume, reuses) an account and returns additionalContext so the session
knows its name and tells the user. End retires it. Both are silent no-ops when the
server is down, so a stopped stack never breaks a session.
"""
import json
import os
import sys
from pathlib import Path

from . import core, state
from .api import Api

SESSIONS_DIR = state.STATE_DIR / "sessions"


def _load(session_id: str) -> dict | None:
    p = SESSIONS_DIR / f"{session_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _save(session_id: str, data: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.write_private(SESSIONS_DIR / f"{session_id}.json", json.dumps(data) + "\n")


def session_model() -> str:
    """Hooks receive no model, so: $MM_MODEL, else the `model` in ~/.claude/settings.json."""
    if os.environ.get("MM_MODEL"):
        return core.model_family(os.environ["MM_MODEL"])
    try:
        cfg = json.loads((Path.home() / ".claude" / "settings.json").read_text())
        return core.model_family(cfg.get("model"))
    except (OSError, ValueError):
        return "claude"


DOWN_CONTEXT = (
    "agent-chat: the local Mattermost is not running, so this session has no account yet. "
    "If you need to talk to other agents or spin up a group, run `mm up` (starts the docker "
    "stack, ~30s), then `mm bootstrap`, then `mm agent new --model {model} --role "
    "\"Claude session ({project}) in {cwd}\"` and tell the user the printed name. "
    "Otherwise ignore this.")


SUBAGENT_CONTEXT = (
    "agent-chat: this is a subagent session, so it has no Mattermost account of its own. "
    "If your prompt says \"You are @name\", that account exists and is yours: pass `-a name` "
    "on every `mm` command. Only if no name was given, create one with `mm agent new`.")


def parent_command() -> str:
    """argv of the process that ran this hook: the Claude Code session itself."""
    import subprocess
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(os.getppid())],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def is_teammate(parent_cmd: str) -> bool:
    """Named Agent-tool workers run as teammates: separate top-level sessions whose hook JSON
    has no agent_type. The lead launches them with --agent-id/--parent-session-id."""
    return "--parent-session-id" in parent_cmd or "--agent-id" in parent_cmd


def start(hook: dict, parent_cmd: str | None = None) -> dict | None:
    """Return the hook output dict, or None to stay silent."""
    # Subagents and teammates fire SessionStart too (subagents with agent_type set,
    # teammates only visible through their launch flags). The orchestrator already made
    # their account, so creating another here would give them two identities.
    if parent_cmd is None:
        parent_cmd = parent_command()
    if hook.get("agent_type") or is_teammate(parent_cmd):
        return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": SUBAGENT_CONTEXT}}
    sid = hook.get("session_id", "")
    cwd = hook.get("cwd", "")
    project = Path(cwd).name or "session"
    if not Api().ping() or not state.load_admin():
        ctx = DOWN_CONTEXT.format(model=session_model(), project=project, cwd=cwd)
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    existing = _load(sid)
    if existing and (acct := state.load_agent(existing["agent"])) and not acct.get("retired_at"):
        name, fresh = existing["agent"], False
    else:
        name = core.new_agent(session_model(), role=f"Claude session ({project}) in {cwd}")["name"]
        _save(sid, {"agent": name, "cwd": cwd})
        fresh = True
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": (
        f"Your Mattermost account for this session is @{name} ({'new' if fresh else 'resumed'}). "
        f"Use `mm -a {name} ...` (agent-chat skill). In your first reply, tell the user this "
        f"name in one line so they can point other sessions at you. If you orchestrate "
        f"a group, use this account as <me> instead of creating another.")}}


def end(hook: dict) -> None:
    if not Api().ping():
        return
    existing = _load(hook.get("session_id", ""))
    if not existing:
        return
    acct = state.load_agent(existing["agent"])
    if acct and not acct.get("retired_at"):
        core.retire(existing["agent"], summary=f"session ended ({hook.get('reason', '')})")


def main(cmd: str) -> int:
    try:
        hook = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except ValueError:
        hook = {}
    try:
        if cmd == "start":
            out = start(hook)
            if out:
                print(json.dumps(out))
        elif cmd == "end":
            end(hook)
    except Exception as e:  # never break a session over chat plumbing
        print(f"mm session {cmd}: {e}", file=sys.stderr)
    return 0
