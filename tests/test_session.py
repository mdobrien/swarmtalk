"""Session hooks create, reuse, and retire a per-session account."""
import json
import subprocess

import pytest

from mm import core, session, state


@pytest.fixture(autouse=True, scope="module")
def _end_sessions():
    yield
    for sid in ("sess-test-1", "sess-test-2", "sess-test-3", "sess-test-4"):
        session.end({"session_id": sid, "reason": "test teardown"})
    core.cleanup()


def _run(cmd, hook):
    r = subprocess.run(["uv", "run", "mm", "session", cmd], input=json.dumps(hook),
                       capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def test_start_creates_and_announces(monkeypatch):
    monkeypatch.setenv("MM_MODEL", "fable[1m]")
    hook = {"session_id": "sess-test-1", "cwd": "/tmp/My.Project", "source": "startup"}
    out = session.start(hook)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    name = ctx.split("@")[1].split()[0]
    assert name.startswith("fable-") and len(name) == 12 and "(new)" in ctx
    assert core.agent(name)["role"] == "Claude session (My.Project) in /tmp/My.Project"
    assert any(f"@{name} joined" in m["message"] for m in core.history("lobby", n=20))


def test_session_model_from_settings(monkeypatch):
    monkeypatch.delenv("MM_MODEL", raising=False)
    assert session.session_model() in ("fable", "mythos", "opus", "sonnet", "haiku", "claude")


def test_resume_reuses_account():
    hook = {"session_id": "sess-test-2", "cwd": "/tmp/proj", "source": "startup"}
    first = session.start(hook)["hookSpecificOutput"]["additionalContext"]
    again = session.start({**hook, "source": "resume"})["hookSpecificOutput"]["additionalContext"]
    assert first.split("@")[1].split()[0] == again.split("@")[1].split()[0]
    assert "(resumed)" in again


def test_end_retires_and_next_start_is_fresh():
    hook = {"session_id": "sess-test-3", "cwd": "/tmp/proj", "source": "startup"}
    name = session.start(hook)["hookSpecificOutput"]["additionalContext"].split("@")[1].split()[0]
    session.end({"session_id": "sess-test-3", "reason": "exit"})
    assert core.agent(name)["retired_at"]
    session.end({"session_id": "sess-test-3", "reason": "exit"})  # idempotent
    new = session.start({**hook, "source": "resume"})["hookSpecificOutput"]["additionalContext"]
    assert new.split("@")[1].split()[0] != name and "(new)" in new


def test_cli_roundtrip_via_stdin():
    out = _run("start", {"session_id": "sess-test-4", "cwd": "/tmp/cli", "source": "startup"})
    assert "@" in out["hookSpecificOutput"]["additionalContext"]
    assert _run("end", {"session_id": "sess-test-4", "reason": "exit"}) is None


def test_start_when_server_down_tells_how_to_bring_it_up(monkeypatch):
    from mm.api import Api
    monkeypatch.setattr(Api, "ping", lambda self: False)
    monkeypatch.setenv("MM_MODEL", "fable")
    out = session.start({"session_id": "sess-down", "cwd": "/tmp/down", "source": "startup"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "not running" in ctx and "`mm up`" in ctx and "--model fable" in ctx
    assert session.end({"session_id": "sess-down", "reason": "exit"}) is None


def test_unknown_session_end_is_silent():
    assert session.end({"session_id": "never-started"}) is None


def test_subagent_start_creates_no_account():
    """Subagent hooks carry agent_type. They get guidance, not an account: the
    orchestrator's prompt names the account they must use."""
    before = {a["name"] for a in core.list_agents()}
    hook = {"session_id": "sess-sub-1", "cwd": "/tmp/proj", "source": "startup",
            "agent_type": "general-purpose", "model": "claude-haiku-4-5-20251001"}
    out = session.start(hook)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "subagent" in ctx and "You are @" in ctx
    assert "Your Mattermost account" not in ctx
    assert {a["name"] for a in core.list_agents()} == before
    assert not (session.SESSIONS_DIR / "sess-sub-1.json").exists()
    assert session.end({"session_id": "sess-sub-1", "reason": "exit"}) is None


TEAMMATE_PARENT = ("/Users/x/.claude/local/claude.exe --agent-id w1@session-abc --agent-name w1 "
                   "--team-name session-abc --parent-session-id abc --permission-mode auto --model haiku")


def test_is_teammate_from_parent_argv():
    assert session.is_teammate(TEAMMATE_PARENT)
    assert not session.is_teammate("/Users/x/.local/bin/claude -p hello --model haiku")
    assert not session.is_teammate("")


def test_teammate_gets_no_account():
    before = {a["name"] for a in core.list_agents()}
    hook = {"session_id": "sess-test-teammate", "cwd": "/tmp/proj", "source": "startup"}
    out = session.start(hook, parent_cmd=TEAMMATE_PARENT)
    assert "subagent" in out["hookSpecificOutput"]["additionalContext"]
    assert {a["name"] for a in core.list_agents()} == before
