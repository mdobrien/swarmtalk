"""Safe defaults: generated secrets, private files, loopback-only ports, the right project."""
import importlib.util
import re
import stat

import pytest

from mm import cli, core, infra, state

EXAMPLE = state.REPO / ".env.example"


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_example_env_ships_no_passwords():
    cfg = state.parse_env(EXAMPLE)
    assert cfg["MM_DB_PASSWORD"] == "" and cfg["MM_ADMIN_PASSWORD"] == ""


def test_init_writes_private_random_secrets(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env.example").write_text(EXAMPLE.read_text())
    monkeypatch.setattr(state, "REPO", tmp_path)
    assert cli.main(["init"]) == 0
    cfg = state.env()
    assert len(cfg["MM_DB_PASSWORD"]) >= 20 and len(cfg["MM_ADMIN_PASSWORD"]) >= 20
    assert cfg["MM_DB_PASSWORD"] != cfg["MM_ADMIN_PASSWORD"]
    assert cfg["MM_ADMIN_USER"] == "admin"
    assert _mode(tmp_path / ".env") == 0o600
    assert cfg["MM_ADMIN_PASSWORD"] not in capsys.readouterr().out, "never print a secret"


def test_init_never_overwrites_an_existing_env(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env.example").write_text(EXAMPLE.read_text())
    (tmp_path / ".env").write_text("MM_DB_PASSWORD=keep\n")
    monkeypatch.setattr(state, "REPO", tmp_path)
    assert cli.main(["init"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / ".env").read_text() == "MM_DB_PASSWORD=keep\n"


@pytest.mark.parametrize("cmd", ["up", "bootstrap"])
@pytest.mark.parametrize("env", [None, "MM_DB_PASSWORD=\nMM_ADMIN_PASSWORD=x\n",
                                 "MM_DB_PASSWORD=x\nMM_ADMIN_PASSWORD=\n"])
def test_blank_secrets_are_refused_before_anything_runs(tmp_path, monkeypatch, capsys, cmd, env):
    if env is not None:
        (tmp_path / ".env").write_text(env)
    monkeypatch.setattr(state, "REPO", tmp_path)
    monkeypatch.setattr(infra, "compose", lambda *a: pytest.fail("compose must not run"))
    assert cli.main([cmd]) == 1
    assert "mm init" in capsys.readouterr().err


def test_state_files_are_private(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state.save_agent("x", {"token": "t"})
    assert _mode(tmp_path / "agents" / "x.json") == 0o600
    (tmp_path / "agents" / "x.json").chmod(0o644)  # a file written by an older version
    state.save_agent("x", {"token": "t2"})
    assert _mode(tmp_path / "agents" / "x.json") == 0o600


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.test.yml"])
def test_ports_are_loopback_only_and_images_pinned(name):
    text = (state.REPO / name).read_text()
    ports = re.findall(r'^\s*- "(\S*:\d+)"', text, re.M)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports)
    assert ":latest" not in text


def test_compose_project_is_fixed_not_the_folder_name(monkeypatch):
    monkeypatch.delenv("MM_COMPOSE_PROJECT")
    monkeypatch.delenv("MM_COMPOSE_FILE")
    assert infra.compose_cmd() == ["docker", "compose", "-p", "swarmtalk"]
    monkeypatch.setenv("MM_COMPOSE_PROJECT", "other")
    assert infra.compose_cmd()[2:4] == ["-p", "other"]


def test_reset_keeps_the_configured_admin(monkeypatch):
    """The admin's username is a setting, so reset must go by id, not by the name 'admin'."""
    admin_id = core.admin()["user_id"]
    users = [{"id": admin_id, "username": "boss"}, {"id": "x" * 26, "username": "opus-123abc"}]
    assert core.reset_victims(users, admin_id) == {"x" * 26: "opus-123abc"}


def test_codex_hook_has_no_machine_specific_paths(monkeypatch):
    path = state.REPO / "hooks" / "codex" / "mm_session.py"
    assert "/usr/local/bin" not in path.read_text()
    monkeypatch.setenv("MM_URL", "http://localhost:9999/")
    spec = importlib.util.spec_from_file_location("codex_hook", path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    assert hook.PING_URL == "http://localhost:9999/api/v4/system/ping"
