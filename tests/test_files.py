"""File attachments on posts and DMs, readable by a peer."""
import pytest

from mm import cli, core
from mm.api import Api


@pytest.fixture(scope="module")
def pair(room):
    a, b = core.new_agent("t", role="test agent"), core.new_agent("t", role="test agent")
    core.join(a["name"], room); core.join(b["name"], room)
    return a, b


@pytest.fixture
def report(tmp_path):
    f = tmp_path / "report.md"
    f.write_text("# results\n12 passed\n")
    return f


def test_post_with_file_readable_by_peer(pair, room, report):
    a, b = pair
    p = core.post(a["name"], room, "report attached", files=[str(report)])
    assert len(p["file_ids"]) == 1
    # a peer, not just the uploader, can fetch the exact bytes
    raw = Api(b["token"]).get_raw(f"/files/{p['file_ids'][0]}")
    assert raw == report.read_bytes()


def test_history_line_names_attachment(pair, room, report):
    a, _ = pair
    core.post(a["name"], room, "second report", files=[str(report)])
    lines = [core.line(m) for m in core.history(room, 5)]
    assert any("second report" in l and "report.md" in l for l in lines), lines


def test_dm_with_two_files(pair, report, tmp_path):
    a, b = pair
    log = tmp_path / "run.log"
    log.write_text("ok\n")
    p = core.dm(a["name"], b["name"], "two files", files=[str(report), str(log)])
    assert len(p["file_ids"]) == 2
    msgs = core.inbox(b["name"])
    hit = [m for m in msgs if m["message"] == "two files"]
    assert hit and "report.md" in core.line(hit[0]) and "run.log" in core.line(hit[0])


def test_cli_file_flag_repeatable(pair, room, report, tmp_path, capsys):
    a, _ = pair
    other = tmp_path / "shot.png"
    other.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    rc = cli.main(["post", "-a", a["name"], "-c", room, "cli files",
                   "--file", str(report), "--file", str(other)])
    assert rc == 0
    pid = capsys.readouterr().out.strip()
    assert len(core.admin_api().get(f"/posts/{pid}")["file_ids"]) == 2


def test_missing_file_fails_before_posting(pair, room, capsys):
    a, _ = pair
    before = len(core.history(room, 50))
    rc = cli.main(["post", "-a", a["name"], "-c", room, "should not appear", "--file", "/nope/x.txt"])
    assert rc == 1
    assert "/nope/x.txt" in capsys.readouterr().err
    assert len(core.history(room, 50)) == before


def test_fetch_saves_attachment_for_review(pair, room, report, tmp_path, capsys):
    a, b = pair
    p = core.post(a["name"], room, "review me", files=[str(report)])
    out = tmp_path / "inbox"
    rc = cli.main(["fetch", "-a", b["name"], p["file_ids"][0], "-o", str(out)])
    assert rc == 0
    saved = capsys.readouterr().out.strip()
    assert saved.endswith("report.md")
    assert (out / "report.md").read_bytes() == report.read_bytes()


def test_quoted_filename_round_trips(pair, room, tmp_path):
    a, b = pair
    odd = tmp_path / 'q"uote.txt'
    odd.write_text("quoted\n")
    p = core.post(a["name"], room, "odd name", files=[str(odd)])
    info = Api(b["token"]).get(f"/files/{p['file_ids'][0]}/info")
    assert info["name"] == 'q"uote.txt'


def test_fetch_refuses_to_overwrite_unless_forced(pair, room, tmp_path, capsys):
    a, b = pair
    first, second = tmp_path / "v1" / "notes.md", tmp_path / "v2" / "notes.md"
    for f, text in ((first, "one\n"), (second, "two\n")):
        f.parent.mkdir(); f.write_text(text)
    id1 = core.post(a["name"], room, "v1", files=[str(first)])["file_ids"][0]
    id2 = core.post(a["name"], room, "v2", files=[str(second)])["file_ids"][0]
    out = tmp_path / "peer"
    assert cli.main(["fetch", "-a", b["name"], id1, "-o", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["fetch", "-a", b["name"], id2, "-o", str(out)]) == 1
    assert "exists" in capsys.readouterr().err
    assert (out / "notes.md").read_text() == "one\n", "first copy not clobbered"
    assert cli.main(["fetch", "-a", b["name"], id2, "-o", str(out), "--force"]) == 0
    assert (out / "notes.md").read_text() == "two\n"


def test_files_tag_in_header_before_body(pair, room, report):
    a, _ = pair
    core.post(a["name"], room, "line one\nline two", files=[str(report)])
    ln = core.line(core.history(room, 1)[-1])
    assert ln.index("[files: ") < ln.index("line one"), ln


def test_pin_does_not_leak_deleted_version_into_inbox(pair, room):
    """Pinning edits the post; Mattermost keeps the old version as a deleted row that
    `?since=` returns. It must not reach the reader as an empty message."""
    a, b = pair
    core.inbox(b["name"])  # drain
    p = core.post(a["name"], room, "PLAN: pinned", pin=True)
    core.edit(a["name"], p["id"], "PLAN: pinned v2")
    got = core.inbox(b["name"])
    assert [m["id"] for m in got] == [p["id"]], [core.line(m) for m in got]
