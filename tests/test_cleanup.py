"""Unique channel per group, archive on finish, cleanup of retired accounts."""
import pytest

from mm import core, state
from mm.api import ApiError


def test_unique_channel_differs_each_time():
    a = core.new_channel("grp", unique=True)
    b = core.new_channel("grp", unique=True)
    assert a["name"] != b["name"] and a["name"].startswith("grp-") and len(a["name"]) == 8


def test_archive_channel_hides_but_keeps_history():
    ch = core.new_channel("grp", unique=True)
    w = core.new_agent("t", announce=False)
    core.join(w["name"], ch["name"])
    p = core.post(w["name"], ch["name"], "kept after archive")
    core.archive_channel(ch["name"])
    got = core.admin_api().get(f"/channels/{ch['id']}")
    assert got["delete_at"] > 0
    assert any(m["id"] == p["id"] for m in core.history(ch["name"], n=10))
    with pytest.raises(ApiError):
        core.post(w["name"], ch["name"], "archived channels reject posts")
    assert core.archive_channel(ch["name"]) is None, "idempotent"


def test_cleanup_removes_retired_keeps_active_and_history(room):
    keep = core.new_agent("t", announce=False)
    gone = core.new_agent("t", announce=False)
    core.join(gone["name"], room)
    p = core.post(gone["name"], room, "posted before cleanup")
    core.retire(gone["name"])
    removed = core.cleanup()
    assert gone["name"] in removed and keep["name"] not in removed
    assert state.load_agent(gone["name"]) is None and state.load_agent(keep["name"])
    team = {u["name"] for u in core.who()}
    assert gone["name"] not in team and keep["name"] in team
    assert any(m["id"] == p["id"] for m in core.history(room, n=200)), "history kept"
    assert core.cleanup() == [], "nothing left to clean"


def test_cleanup_all_retires_active_first():
    a = core.new_agent("t", announce=False)
    removed = core.cleanup(everything=True)
    assert a["name"] in removed
    assert state.list_agents() == []


def test_cleanup_purge_deletes_user_and_posts(room):
    a = core.new_agent("t", announce=False)
    core.join(a["name"], room)
    p = core.post(a["name"], room, "purged with me")
    core.retire(a["name"])
    assert a["name"] in core.cleanup(purge=True)
    with pytest.raises(ApiError) as e:
        core.admin_api().get(f"/users/{a['user_id']}")
    assert e.value.status == 404
    assert not any(m["id"] == p["id"] for m in core.history(room, n=200))
    try:
        prefs = core.admin_api().get(f"/users/{core.admin()['user_id']}/preferences/direct_channel_show")
    except ApiError as e:  # a fresh server has no such preferences at all: 404, i.e. none
        assert e.status == 404
        prefs = []
    assert a["user_id"] not in {x["name"] for x in prefs}, "admin sidebar DM for the bot is gone"


def test_channel_done_retires_workers_archives_and_cleans():
    """One call ends a group: workers retired, room archived, cleanup run, caller untouched."""
    ch = core.new_channel("grp", unique=True)
    me = core.new_agent("orch", announce=False)
    w1, w2 = core.new_agent("w", announce=False), core.new_agent("w", announce=False)
    for a in (me, w1, w2):
        core.join(a["name"], ch["name"])
    result = core.channel_done(ch["name"], keep=me["name"])
    assert set(result["retired"]) == {w1["name"], w2["name"]}
    assert set(result["removed"]) >= {w1["name"], w2["name"]}
    assert core.admin_api().get(f"/channels/{ch['id']}")["delete_at"] > 0
    assert state.load_agent(w1["name"]) is None
    assert not core.agent(me["name"]).get("retired_at"), "the caller keeps their account"
    core.retire(me["name"]); core.cleanup()
