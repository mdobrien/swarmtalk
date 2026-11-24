"""Inbox returns unread DMs/mentions/posts once, own and system posts excluded."""
import pytest

from mm import core


@pytest.fixture(scope="module")
def pair(room):
    a, b = core.new_agent("t"), core.new_agent("t")
    core.join(a["name"], room); core.join(b["name"], room)
    core.inbox(a["name"]); core.inbox(b["name"])  # drain join noise
    return a, b


def test_channel_post_then_empty(pair, room):
    a, b = pair
    p = core.post(a["name"], room, "for b")
    msgs = core.inbox(b["name"])
    assert [m["id"] for m in msgs] == [p["id"]]
    assert msgs[0]["kind"] == "post" and msgs[0]["channel"] == room
    assert core.inbox(b["name"]) == []


def test_own_posts_not_in_inbox(pair, room):
    a, _ = pair
    core.post(a["name"], room, "talking to myself")
    assert core.inbox(a["name"]) == []
    core.inbox(pair[1]["name"])  # drain for the next test


def test_dm_kind(pair, room):
    a, b = pair
    p = core.dm(a["name"], b["name"], "secret")
    msgs = core.inbox(b["name"])
    assert len(msgs) == 1 and msgs[0]["kind"] == "dm" and msgs[0]["id"] == p["id"]
    assert core.line(msgs[0]).startswith(f"[{p['id']}] @{a['name']} (dm): secret")


def test_mention_kind(pair, room):
    a, b = pair
    core.post(a["name"], room, f"@{b['name']} look here")
    msgs = core.inbox(b["name"])
    assert len(msgs) == 1 and msgs[0]["kind"] == "mention"
    assert "!mention" in core.line(msgs[0])


def test_peek_does_not_mark(pair, room):
    a, b = pair
    core.post(a["name"], room, "peekaboo")
    assert len(core.inbox(b["name"], mark=False)) == 1
    assert len(core.inbox(b["name"])) == 1
    assert core.inbox(b["name"]) == []


def test_ordering_across_channels(pair, room):
    a, b = pair
    p1 = core.post(a["name"], room, "first")
    p2 = core.dm(a["name"], b["name"], "second")
    p3 = core.post(a["name"], "lobby", "third")
    assert [m["id"] for m in core.inbox(b["name"])] == [p1["id"], p2["id"], p3["id"]]
