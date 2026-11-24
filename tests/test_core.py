"""Agents, channels, post/thread/dm/history/who against the live server."""
import pytest

from mm import core
from mm.api import Api, ApiError


@pytest.fixture(scope="module")
def pair(room):
    a, b = core.new_agent("t", role="test agent"), core.new_agent("t", role="test agent")
    core.join(a["name"], room); core.join(b["name"], room)
    return a, b


def test_new_agent_unique_and_real(pair, room):
    a, b = pair
    assert a["name"] != b["name"]
    assert a["name"].startswith("t-") and len(a["name"]) == 8
    me = Api(a["token"]).get("/users/me")
    assert me["username"] == a["name"] and me["is_bot"] is True
    assert core.agent(a["name"])["channels"] == ["lobby", room], "lobby by default, then the room"


def test_model_family_normalisation():
    assert core.model_family("fable[1m]") == "fable"
    assert core.model_family("claude-opus-5") == "opus"
    assert core.model_family("claude-haiku-4-5-20251001") == "haiku"
    assert core.model_family("Sonnet") == "sonnet"
    assert core.model_family(None) == "claude"
    assert core.model_family("gpt-9") == "gpt9"
    assert core.model_family("gpt-6-sol") == "gpt6-sol"
    assert core.model_family("gpt-6-astra") == "gpt6-astra"
    assert core.model_family("gpt-6-luna") == "gpt6-luna"
    assert core.model_family("gpt-5.6-terra") == "gpt5-6-terra"


@pytest.mark.parametrize(("model", "prefix"), [
    ("claude-opus-5", "opus"),
    ("gpt-6-sol", "gpt6-sol"),
])
def test_new_agent_named_after_model(model, prefix):
    a = core.new_agent(model, role="naming test", announce=False)
    try:
        assert a["name"].startswith(prefix + "-")
        assert len(a["name"]) == len(prefix) + 7
        assert a["model"] == prefix
    finally:  # not a "t-" account, so the session sweep will not reclaim it
        core.retire(a["name"], summary="naming test")
        core.admin_api().delete(f"/teams/{core.admin()['team_id']}/members/{a['user_id']}")
        (core.state.AGENTS_DIR / f"{a['name']}.json").unlink(missing_ok=True)


def test_list_agents_includes_new(pair, room):
    names = [x["name"] for x in core.list_agents()]
    assert pair[0]["name"] in names


def test_post_and_history(pair, room):
    a, b = pair
    p = core.post(a["name"], room, "hello from a")
    msgs = core.history(room, n=20)
    mine = [m for m in msgs if m["id"] == p["id"]]
    assert mine and mine[0]["from"] == a["name"] and mine[0]["message"] == "hello from a"
    assert all(not m["message"].endswith("joined the channel.") for m in msgs), "system posts filtered"


def test_thread_reply(pair, room):
    a, b = pair
    root = core.post(a["name"], room, "root")
    reply = core.post(b["name"], room, "reply", thread_id=root["id"])
    assert reply["root_id"] == root["id"]
    msgs = {m["id"]: m for m in core.history(room, n=20)}
    assert msgs[reply["id"]]["thread"] == root["id"]


def test_history_after(pair, room):
    a, _ = pair
    p1 = core.post(a["name"], room, "one")
    p2 = core.post(a["name"], room, "two")
    ids = [m["id"] for m in core.history(room, after=p1["id"])]
    assert p2["id"] in ids and p1["id"] not in ids


def test_dm(pair, room):
    a, b = pair
    p = core.dm(a["name"], b["name"], "psst")
    page = Api(b["token"]).get(f"/channels/{p['channel_id']}/posts", {"per_page": 5})
    assert page["posts"][p["id"]]["message"] == "psst"
    ch = Api(b["token"]).get(f"/channels/{p['channel_id']}")
    assert ch["type"] == "D"


def test_new_channel_join_who(pair, room):
    a, b = pair
    ch = core.new_channel("t", unique=True, purpose="tests")
    assert core.new_channel(ch["name"])["id"] == ch["id"], "idempotent by name"
    core.join(a["name"], ch["name"])
    core.join(a["name"], "#" + ch["name"])  # already a member: no error
    names = {u["name"] for u in core.who(ch["name"])}
    assert a["name"] in names and b["name"] not in names
    assert ch["name"] in core.agent(a["name"])["channels"]
    core.archive_channel(ch["name"])


def test_agent_cannot_use_admin_endpoints(pair, room):
    a, _ = pair
    with pytest.raises(ApiError) as e:
        Api(a["token"]).post("/bots", {"username": "sneaky", "display_name": "x"})
    assert e.value.status == 403


def test_pinned_post_is_listed(pair, room):
    a, _ = pair
    core.post(a["name"], room, "just chatter")
    plan = core.post(a["name"], room, "PLAN: a owns x, b owns y", pin=True)
    pinned = core.pins(room)
    assert [m["id"] for m in pinned] == [plan["id"]]
    assert pinned[0]["message"].startswith("PLAN:")
