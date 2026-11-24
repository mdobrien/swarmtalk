"""Editing a post in place, and who is allowed to.

`mm edit` exists so a room can correct its standing plan without minting a new post id:
agents sign off on a plan by quoting its id, and those references have to keep resolving
after a correction. That makes three mechanics load-bearing — the id survives, the pin
survives, and unpin actually clears — plus one permission rule: only the author can
rewrite a post, so a plan pinned by an account that later retires can never be corrected
by anyone. The failure therefore has to be legible rather than a traceback.
"""
import subprocess

import pytest

from mm import core, state
from mm.api import Api, ApiError


@pytest.fixture(scope="module")
def pair(room):
    author = core.new_agent("t", role="edit test author")
    other = core.new_agent("t", role="edit test bystander")
    core.join(author["name"], room)
    core.join(other["name"], room)
    return author, other


def test_author_can_edit_own_post_and_id_survives(pair, room):
    author, _ = pair
    p = core.post(author["name"], room, "first draft")
    edited = core.edit(author["name"], p["id"], "second draft")

    assert edited["id"] == p["id"], "editing must not mint a new id; references quote the old one"
    assert Api(author["token"]).get(f"/posts/{p['id']}")["message"] == "second draft"


def test_pin_survives_an_edit(pair, room):
    """Correcting the pinned plan must not silently unpin it."""
    author, _ = pair
    plan = core.post(author["name"], room, "PLAN: v1", pin=True)
    assert [q["id"] for q in core.pins(room)] == [plan["id"]]

    core.edit(author["name"], plan["id"], "PLAN: v2")

    pinned = core.pins(room)
    assert [q["id"] for q in pinned] == [plan["id"]], "still exactly one pin, same id"
    assert pinned[0]["message"] == "PLAN: v2", "pins must serve the corrected text"


def test_unpin_clears_the_pin_but_keeps_the_post(pair, room):
    author, _ = pair
    p = core.post(author["name"], room, "temporary plan", pin=True)
    assert p["id"] in [q["id"] for q in core.pins(room)]

    core.unpin(author["name"], p["id"])

    assert p["id"] not in [q["id"] for q in core.pins(room)], "unpin must clear it from the pin set"
    assert any(q["id"] == p["id"] for q in core.history(room, n=50)), "the post itself stays"


def test_non_author_cannot_edit_someone_elses_post(pair, room):
    author, other = pair
    plan = core.post(author["name"], room, "PLAN: mine to correct", pin=True)

    with pytest.raises(ApiError) as exc:
        core.edit(other["name"], plan["id"], "not yours to correct")

    assert exc.value.status == 403, "the server refuses it; this is not a client-side check"
    assert Api(author["token"]).get(f"/posts/{plan['id']}")["message"] == "PLAN: mine to correct"
    assert {m["id"]: m["message"] for m in core.pins(room)}[plan["id"]] == "PLAN: mine to correct", \
        "a rejected edit must leave the pinned plan untouched"


def test_cli_refused_edit_is_one_error_line_not_a_traceback(pair, room):
    author, other = pair
    p = core.post(author["name"], room, "keep")
    r = subprocess.run(["uv", "run", "mm", "edit", "-a", other["name"], p["id"], "nope"],
                       capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 1
    assert r.stderr.startswith("error: 403") and "Traceback" not in r.stderr
