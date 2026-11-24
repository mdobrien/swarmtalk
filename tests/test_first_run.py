"""What a new user or their agent hits first: skill metadata, a stopped server, bad names."""
import os
import re
import subprocess

import pytest

from mm import cli, core, session, state

DOWN = {**os.environ, "MM_URL": "http://localhost:1"}


@pytest.mark.parametrize("skill", ["agent-chat", "agent-chat-orchestrator"])
def test_skill_description_is_quoted(skill):
    """Unquoted, YAML reads ` #channel` as a comment and drops the rest of the description."""
    head = (state.REPO / "skills" / skill / "SKILL.md").read_text().split("---")[1]
    value = re.search(r"^description: (.*)$", head, re.M).group(1)
    assert value.startswith("'") and value.endswith("'")
    assert "'" not in value[1:-1].replace("''", "")


def test_server_down_is_one_line_telling_you_what_to_run(pytestconfig):
    r = subprocess.run(["uv", "run", "mm", "who"], capture_output=True, text=True, cwd=state.REPO, env=DOWN)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert r.stderr.startswith("error: ") and "mm up" in r.stderr


def test_wait_with_server_down_is_one_line():
    name = core.new_agent("t", announce=False)["name"]
    r = subprocess.run(["uv", "run", "mm", "wait", "-a", name, "--timeout", "2"], capture_output=True,
                       text=True, cwd=state.REPO, env=DOWN)
    assert r.returncode == 1 and "Traceback" not in r.stderr and "mm up" in r.stderr


def test_bad_channel_name_is_one_line(capsys):
    assert cli.main(["channel", "new", "Bad Slug"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_history_after_returns_the_next_posts(room):
    a = core.new_agent("t", announce=False)
    core.join(a["name"], room)
    ids = [core.post(a["name"], room, f"p{i}")["id"] for i in range(5)]
    assert [m["id"] for m in core.history(room, 2, after=ids[0])] == ids[1:3]
    assert [m["id"] for m in core.history(room, 2)] == ids[3:], "without --after: the newest"


def test_channel_done_spares_codex_and_claude_sessions():
    room = core.new_channel("done", unique=True)["name"]
    roles = {"Codex session in /tmp/x": False, "Claude session (x) in /tmp/x": False, "worker": True}
    made = {r: core.new_agent("t", role=r, announce=False)["name"] for r in roles}
    for name in made.values():
        core.join(name, room)
    assert core.channel_done(room)["retired"] == [made["worker"]]
    assert state.load_agent(made["Codex session in /tmp/x"])["name"]


def test_hook_context_does_not_claim_every_command_takes_an_account():
    assert "every `mm` command that accepts it" in session.SUBAGENT_CONTEXT


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.test.yml"])
def test_amd64_only_image_declares_its_platform(name):
    block = (state.REPO / name).read_text().split("  mattermost:\n")[1]
    assert "platform: linux/amd64" in block.split("environment:")[0]
