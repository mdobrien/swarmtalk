"""Runs last: reset leaves only system users, no DM channels, no local state.

Destructive, but only to the throwaway test server (see conftest).
"""
from mm import core, state


def test_reset_leaves_server_clean():
    a = core.new_agent("t", announce=False)          # creates a bot + its admin DM
    core.post(a["name"], "lobby", "about to be wiped")
    r = core.reset()
    assert a["name"] in r["deleted_users"]
    api = core.admin_api(); adm = core.admin()
    users = {u["username"] for u in api.get("/users", {"per_page": 200})}
    assert users <= core.KEEP_USERS
    mine = api.get(f"/users/{adm['user_id']}/teams/{adm['team_id']}/channels")
    assert all(c["type"] == "O" for c in mine), "no DM channels left for admin"
    assert state.list_agents() == []
    assert "about to be wiped" not in [m["message"] for m in core.history("lobby", n=100)]
