"""CLI behavior agents depend on when posting, replying, fetching and waiting.

- `--thread` accepts any post in a thread, not just the root (Mattermost only takes a root id).
- `--body-file PATH` / `-` (stdin) so a body never goes through shell quoting.
- ids are case-normalized, and a 403/404 on an id-taking command says to check the id.
- `fetch` prints a clean path.
- `wait --mine`: DMs, @mentions, and replies in threads I started or posted in.
"""
import io
import subprocess
import sys
import threading
import time

import pytest

from mm import cli, core, state, ws
from mm.api import Api

# every character that has broken a shell-quoted body in practice
NASTY = "it's \"quoted\" $HOME `whoami` (S2, S5)\n\nline two\n  indented\ntrailing newline\n"


@pytest.fixture(scope="module")
def pair(room):
    a, b = core.new_agent("t", role="test agent"), core.new_agent("t", role="test agent")
    core.join(a["name"], room); core.join(b["name"], room)
    return a, b


def _message(post_id):
    return core.admin_api().get(f"/posts/{post_id}")["message"]


def _stdin(monkeypatch, data: str):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data.encode())))


# ---- #1 thread root resolution ---------------------------------------------

def test_thread_reply_to_a_reply_lands_in_root_thread(pair, room):
    a, b = pair
    root = core.post(a["name"], room, "root")
    r1 = core.post(b["name"], room, "r1", thread_id=root["id"])
    r2 = core.post(a["name"], room, "r2", thread_id=r1["id"])
    assert r2["root_id"] == root["id"]


def test_thread_unknown_id_is_one_error_line_and_posts_nothing(pair, room):
    a, _ = pair
    before = len(core.history(room, 50))
    r = subprocess.run(["uv", "run", "mm", "post", "-a", a["name"], "-c", room,
                        "--thread", "z" * 26, "orphan"], capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 1 and r.stderr.startswith("error: ") and "Traceback" not in r.stderr
    assert "z" * 26 in r.stderr and "lowercase" in r.stderr
    assert len(core.history(room, 50)) == before


# ---- #2 --body-file / stdin -----------------------------------------------

def test_post_body_file_round_trips_byte_exact(pair, room, tmp_path, capsys):
    a, _ = pair
    f = tmp_path / "plan.md"
    f.write_text(NASTY)
    assert cli.main(["post", "-a", a["name"], "-c", room, "--body-file", str(f)]) == 0
    pid = capsys.readouterr().out.strip()
    assert _message(pid) == NASTY
    assert [m["message"] for m in core.history(room, 50) if m["id"] == pid] == [NASTY]


def test_post_stdin_round_trips_byte_exact(pair, room, monkeypatch, capsys):
    a, _ = pair
    _stdin(monkeypatch, NASTY)
    assert cli.main(["post", "-a", a["name"], "-c", room, "-", "--pin"]) == 0
    pid = capsys.readouterr().out.strip()
    assert _message(pid) == NASTY and pid in [m["id"] for m in core.pins(room)]


def test_stdin_through_a_real_shell_heredoc(pair, room):
    """The case that failed in the field: a heredoc body never touches argv quoting."""
    a, _ = pair
    script = f"uv run mm post -a {a['name']} -c {room} - <<'EOF'\n{NASTY}EOF\n"
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 0, r.stderr
    assert _message(r.stdout.strip()) == NASTY


def test_dm_body_file(pair, tmp_path, capsys):
    a, b = pair
    f = tmp_path / "dm.txt"
    f.write_text(NASTY)
    assert cli.main(["dm", "-a", a["name"], "--to", b["name"], "--body-file", str(f)]) == 0
    assert _message(capsys.readouterr().out.strip()) == NASTY


def test_edit_body_from_stdin_and_file(pair, room, monkeypatch, tmp_path, capsys):
    a, _ = pair
    p = core.post(a["name"], room, "draft")
    _stdin(monkeypatch, NASTY)
    assert cli.main(["edit", "-a", a["name"], p["id"], "-"]) == 0
    assert _message(p["id"]) == NASTY
    f = tmp_path / "v3.md"
    f.write_text("v3 isn't quoted\n")
    assert cli.main(["edit", "-a", a["name"], p["id"], "--body-file", str(f)]) == 0
    assert _message(p["id"]) == "v3 isn't quoted\n"
    capsys.readouterr()


@pytest.mark.parametrize("extra", [[], ["inline", "--body-file", "BODY"], ["-", "--body-file", "BODY"]])
def test_exactly_one_body_source(pair, room, tmp_path, capsys, extra):
    a, _ = pair
    f = tmp_path / "b.txt"
    f.write_text("x")
    before = len(core.history(room, 50))
    argv = ["post", "-a", a["name"], "-c", room] + [str(f) if x == "BODY" else x for x in extra]
    assert cli.main(argv) == 1
    assert "exactly one" in capsys.readouterr().err
    assert len(core.history(room, 50)) == before


def test_missing_body_file_fails_before_posting(pair, room, capsys):
    a, _ = pair
    before = len(core.history(room, 50))
    assert cli.main(["post", "-a", a["name"], "-c", room, "--body-file", "/nope/body.md"]) == 1
    assert "/nope/body.md" in capsys.readouterr().err
    assert len(core.history(room, 50)) == before


def test_positional_message_unchanged(pair, room, capsys):
    a, _ = pair
    assert cli.main(["post", "-a", a["name"], "-c", room, "plain inline"]) == 0
    assert _message(capsys.readouterr().out.strip()) == "plain inline"


# ---- #6 id case + clearer 403/404 ------------------------------------------

def test_upper_cased_ids_work_everywhere(pair, room, tmp_path, capsys):
    a, b = pair
    plan = core.post(a["name"], room, "PLAN v1", pin=True)
    up = plan["id"][0].upper() + plan["id"][1:]
    assert cli.main(["edit", "-a", a["name"], up, "PLAN v2"]) == 0
    assert _message(plan["id"]) == "PLAN v2"
    reply = core.post(b["name"], room, "reply", thread_id=plan["id"].upper())
    assert reply["root_id"] == plan["id"]
    assert cli.main(["unpin", "-a", a["name"], plan["id"].upper()]) == 0
    assert plan["id"] not in [m["id"] for m in core.pins(room)]
    f = tmp_path / "x.md"
    f.write_text("x\n")
    fid = core.post(a["name"], room, "file", files=[str(f)])["file_ids"][0]
    assert cli.main(["fetch", "-a", b["name"], fid.upper(), "-o", str(tmp_path / "out")]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("cmd", [["edit", "{id}", "x"], ["unpin", "{id}"], ["fetch", "{id}"]])
def test_bad_id_error_says_check_the_id(pair, cmd, capsys):
    a, _ = pair
    argv = [cmd[0], "-a", a["name"]] + [x.format(id="z" * 26) for x in cmd[1:]]
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: 40") and "check the id (ids are lowercase, 26 chars)" in err


# ---- #4 fetch path ---------------------------------------------------------

@pytest.mark.parametrize("suffix", ["/", "//", "//sub/"])
def test_fetch_prints_no_double_slash(pair, room, tmp_path, capsys, suffix):
    a, b = pair
    f = tmp_path / "r.md"
    f.write_text("r\n")
    fid = core.post(a["name"], room, "r", files=[str(f)])["file_ids"][0]
    out = str(tmp_path / "peer") + suffix
    assert cli.main(["fetch", "-a", b["name"], fid, "-o", out]) == 0
    printed = capsys.readouterr().out.strip()
    assert "//" not in printed and printed.endswith("/r.md")


# ---- #5 wait --mine --------------------------------------------------------

@pytest.fixture(scope="module")
def trio(room):
    """b waits; a and c talk. Fresh accounts so earlier tests' posts are not backlog."""
    a, b, c = (core.new_agent("t") for _ in range(3))
    for x in (a, b, c):
        core.join(x["name"], room)
        core.inbox(x["name"])
    return a, b, c


def _wait_mine(name, timeout):
    box = {}

    def run():
        t0 = time.monotonic()
        box["msgs"] = ws.wait(name, timeout, mine=True)
        box["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.8)  # let the socket authenticate
    return t, box


def test_mine_wakes_on_reply_in_thread_i_started(trio, room):
    a, b, _ = trio
    root = core.post(b["name"], room, "b's question")
    core.inbox(b["name"])
    t, box = _wait_mine(b["name"], 10)
    r = core.post(a["name"], room, "answer", thread_id=root["id"])
    t.join(8)
    assert box["msgs"] and box["msgs"][-1]["id"] == r["id"] and box["elapsed"] < 5


def test_mine_wakes_on_reply_in_thread_i_posted_in(trio, room):
    a, b, c = trio
    root = core.post(a["name"], room, "a's thread")
    core.post(b["name"], room, "b chimes in", thread_id=root["id"])
    core.inbox(b["name"])
    t, box = _wait_mine(b["name"], 10)
    r = core.post(c["name"], room, "c replies", thread_id=root["id"])
    t.join(8)
    assert box["msgs"] and box["msgs"][-1]["id"] == r["id"]


def test_mine_ignores_chatter_and_other_threads(trio, room):
    a, b, c = trio
    other = core.post(a["name"], room, "a and c only")
    core.inbox(b["name"])
    t, box = _wait_mine(b["name"], 3)
    core.post(c["name"], room, "top-level chatter")
    core.post(c["name"], room, "not b's thread", thread_id=other["id"])
    t.join(6)
    assert box["msgs"] == [] and box["elapsed"] > 2.5
    assert len(core.inbox(b["name"])) == 2, "ignored posts stay unread for inbox"


def test_mine_still_wakes_on_dm_and_mention(trio, room):
    a, b, _ = trio
    t, box = _wait_mine(b["name"], 10)
    core.post(a["name"], room, f"@{b['name']} look")
    t.join(8)
    assert box["msgs"] and box["msgs"][-1]["kind"] == "mention"
    t, box = _wait_mine(b["name"], 10)
    p = core.dm(a["name"], b["name"], "psst")
    t.join(8)
    assert box["msgs"] and box["msgs"][-1]["id"] == p["id"]


def test_mine_returns_backlog_reply_at_once(trio, room):
    a, b, _ = trio
    root = core.post(b["name"], room, "b asks again")
    core.inbox(b["name"])
    r = core.post(a["name"], room, "answered before b waited", thread_id=root["id"])
    t0 = time.monotonic()
    msgs = ws.wait(b["name"], 5, mine=True)
    assert [m["id"] for m in msgs] == [r["id"]] and time.monotonic() - t0 < 2


def test_cli_mine_and_mentions_only_are_exclusive(trio):
    _, b, _ = trio
    with pytest.raises(SystemExit):
        cli.main(["wait", "-a", b["name"], "--mine", "--mentions-only", "--timeout", "1"])
