"""`mm group new` = room + N worker accounts + everyone joined, in one call."""
from mm import core


def test_group_new_creates_room_and_workers():
    me = core.new_agent("t", announce=False)
    g = core.new_group("grp", workers=2, model="gpt-6-astra", me=me["name"],
                       roles=["first piece", "second piece"], announce=False)
    assert g["channel"].startswith("grp-") and len(g["workers"]) == 2
    assert all(name.startswith("gpt6-astra-") for name in g["workers"])
    members = {u["name"] for u in core.who(g["channel"])}
    assert set(g["workers"]) | {me["name"]} <= members
    assert core.agent(g["workers"][0])["role"] == "first piece"
    assert core.agent(g["workers"][1])["role"] == "second piece"
    assert g["channel"] in core.agent(g["workers"][0])["channels"]


def test_group_new_default_roles_and_unique_rooms():
    a = core.new_group("grp", workers=1, model="t", announce=False)
    b = core.new_group("grp", workers=1, model="t", announce=False)
    assert a["channel"] != b["channel"]
    assert core.agent(a["workers"][0])["role"] == f"worker 1 in #{a['channel']}"
