"""New agents announce in #lobby; retire announces, disables, keeps history."""
import pytest

from mm import core
from mm.api import ApiError


def _lobby_lines():
    return [m["message"] for m in core.history("lobby", n=100)]


def test_new_announces_in_lobby():
    a = core.new_agent("t", role="lifecycle test")
    assert f"@{a['name']} joined — lifecycle test" in " ".join(_lobby_lines())


def test_retire_announces_disables_keeps_history(room):
    a = core.new_agent("t", role="short lived")
    core.join(a["name"], room)
    p = core.post(a["name"], room, "I was here")
    core.retire(a["name"], summary="done, nothing pending")
    lines = _lobby_lines()
    assert any(f"@{a['name']} leaving — done, nothing pending" in l for l in lines)
    with pytest.raises(ApiError) as e:
        core.post(a["name"], room, "after retire")
    assert e.value.status in (401, 403)
    assert any(m["id"] == p["id"] for m in core.history(room, n=100)), "history kept"
    assert core.agent(a["name"])["retired_at"]
    assert core.retire(a["name"]) == core.agent(a["name"]), "idempotent"


def test_retired_shows_in_list():
    names = {a["name"]: a for a in core.list_agents()}
    assert any(a.get("retired_at") for a in names.values())
