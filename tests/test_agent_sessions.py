"""Sessions that used to lose their account, and the Codex listener's message handling and lifetime."""
import importlib.util
import os
import subprocess
import sys

import pytest

from mm import core, session, state


@pytest.fixture
def hook(tmp_path, monkeypatch):
    path = state.REPO / "hooks" / "codex" / "mm_session.py"
    spec = importlib.util.spec_from_file_location("mm_session", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "start_listener", lambda *a: None)
    return mod


# ---- claude --agent NAME is a main session, not a subagent ----

@pytest.mark.parametrize("cmd", ["claude --agent reviewer -p hi", "claude --agent=reviewer"])
def test_agent_flag_session_gets_an_account(cmd):
    out = session.start({"session_id": f"sess-{abs(hash(cmd))}", "cwd": "/tmp/p", "agent_type": "reviewer"}, cmd)
    assert "Your Mattermost account" in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("cmd", [
    "claude -p hi", "claude --agent reviewer-two", "claude --agent lead",
    "claude.exe --agent-id w1@s --agent-name reviewer --parent-session-id abc --agent reviewer"])
def test_subagents_and_teammates_still_get_none(cmd):
    before = len(core.list_agents())
    out = session.start({"session_id": "sess-none", "cwd": "/tmp/p", "agent_type": "reviewer"}, cmd)
    assert "subagent" in out["hookSpecificOutput"]["additionalContext"]
    assert len(core.list_agents()) == before


def test_claude_hook_example_covers_clear():
    """/clear ends the session (retiring its account) and starts another with source=clear."""
    assert "clear" in (state.REPO / "hooks/claude/settings.example.json").read_text()


# ---- Codex listener: whole messages ----

ID1, ID2 = "a" * 26, "b" * 26


def test_multiline_messages_are_kept_whole(hook):
    out = f"[{ID1}] @x (dm): one\ntwo\n\n[not an id] three\n[{ID2}] @y (dm): four"
    assert hook.split_messages(out) == [f"[{ID1}] @x (dm): one\ntwo\n\n[not an id] three",
                                        f"[{ID2}] @y (dm): four"]


def test_empty_wait_has_no_messages(hook):
    assert hook.split_messages("(no messages within 30s)") == []


# ---- Codex listener: ends with its session ----

def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", ""])
    p.wait()
    return p.pid


def test_owner_is_this_live_process(hook):
    owner = hook.process_identity(os.getpid())
    assert owner["pid"] == os.getpid() and owner["started"]
    assert hook.owner_alive(owner)


def test_dead_or_reused_pid_is_not_alive(hook):
    assert not hook.owner_alive({"pid": dead_pid(), "started": "x"})
    assert not hook.owner_alive({"pid": os.getpid(), "started": "some other time"})


def test_unknown_owner_counts_as_alive(hook):
    assert hook.owner_alive(None)


def test_unreadable_process_table_counts_as_alive(hook, monkeypatch):
    def boom(*a, **k):
        raise OSError
    monkeypatch.setattr(hook.subprocess, "run", boom)
    assert hook.owner_alive({"pid": 1, "started": "x"})


def test_listener_retires_the_account_when_codex_died(hook, monkeypatch):
    calls = []
    monkeypatch.setattr(hook, "run_mm", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(hook, "active_account", lambda name: True)
    path = hook.state_path("s1")
    hook.write_state(path, {"agent": "gpt-1", "cwd": "/tmp", "retired": False,
                            "owner": {"pid": dead_pid(), "started": "x"}})
    hook.listen("s1", path)
    assert [c[:4] for c in calls] == [("agent", "retire", "-a", "gpt-1")]
    assert hook.read_state(path)["retired"] is True


def test_resume_records_the_new_owner(hook, monkeypatch):
    monkeypatch.setattr(hook, "run_mm", lambda *a, **k: "(inbox empty)")
    monkeypatch.setattr(hook, "active_account", lambda name: True)
    monkeypatch.setattr(hook, "codex_owner", lambda: {"pid": 2, "started": "new"})
    path = hook.state_path("s2")
    hook.write_state(path, {"agent": "gpt-2", "cwd": "/tmp", "retired": False, "owner": {"pid": 1, "started": "old"}})
    hook.start({"session_id": "s2"}, path)
    assert hook.read_state(path)["owner"] == {"pid": 2, "started": "new"}


def test_repeated_failure_is_logged_once(hook, capsys):
    log = hook.ChangeLog()
    for text in ("down", "down", "down", "other"):
        log.write(text)
    assert capsys.readouterr().err.splitlines() == ["down", "other"]


# ---- documented limits ----

def test_cli_documents_history_cap_and_file_body():
    def help_for(cmd):
        return " ".join(subprocess.run(["uv", "run", "mm", cmd, "--help"], capture_output=True, text=True,
                                       cwd=state.REPO).stdout.split())
    assert "200" in help_for("history")
    assert "needs a message" in help_for("post") and "needs a message" in help_for("dm")
