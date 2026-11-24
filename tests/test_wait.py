"""`wait` returns on push, ignores plain chatter unless --any, and never loses a message."""
import subprocess
import threading
import time

import pytest

from mm import core, ws


@pytest.fixture(scope="module")
def pair(room):
    a, b = core.new_agent("t"), core.new_agent("t")
    core.join(a["name"], room); core.join(b["name"], room)
    core.inbox(a["name"]); core.inbox(b["name"])
    return a, b


def _wait_in_thread(name, timeout, any_post=False):
    box = {}

    def run():
        t0 = time.monotonic()
        box["msgs"] = ws.wait(name, timeout, any_post)
        box["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=run)
    t.start()
    return t, box


def test_dm_wakes_fast(pair, room):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10)
    time.sleep(0.7)  # let the socket authenticate
    p = core.dm(a["name"], b["name"], "wake up")
    t.join(5)
    assert box["msgs"] and box["msgs"][0]["id"] == p["id"] and box["msgs"][0]["kind"] == "dm"
    assert box["elapsed"] < 3
    assert core.inbox(b["name"]) == [], "returned message is marked read"


def test_mention_wakes(pair, room):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10)
    time.sleep(0.7)
    core.post(a["name"], room, f"@{b['name']} ping")
    t.join(5)
    assert box["msgs"][0]["kind"] == "mention" and box["msgs"][0]["channel"] == room
    core.inbox(b["name"])


def test_plain_post_does_not_wake_then_timeout(pair, room):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 2)
    time.sleep(0.7)
    core.post(a["name"], room, "just chatter")
    t.join(5)
    assert box["msgs"] == [] and 1.5 < box["elapsed"] < 4
    assert len(core.inbox(b["name"])) == 1, "chatter stays unread for inbox"


def test_any_wakes_on_plain_post(pair, room):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10, any_post=True)
    time.sleep(0.7)
    p = core.post(a["name"], room, "chatter counts now")
    t.join(5)
    assert box["msgs"][0]["id"] == p["id"] and box["msgs"][0]["kind"] == "post"


def test_message_before_wait_is_not_lost(pair, room):
    a, b = pair
    p = core.dm(a["name"], b["name"], "sent before wait")
    t0 = time.monotonic()
    msgs = ws.wait(b["name"], 5)
    assert [m["id"] for m in msgs] == [p["id"]] and time.monotonic() - t0 < 2


def test_any_ignores_lobby_notices(pair, room):
    """A join notice in #lobby must not wake an any-post waiter; a room post must."""
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10, any_post=True)
    time.sleep(1)
    core.post(a["name"], "lobby", "lobby chatter")
    time.sleep(1.5)
    assert t.is_alive(), "woke on a lobby post"
    core.post(a["name"], room, "room post")
    t.join(8)
    assert box["msgs"] and box["msgs"][-1]["message"] == "room post"


def test_wait_on_retired_or_unknown_account_is_one_error_line(room):
    """The failure a worker sees when its account is gone must say so, not stack-trace."""
    from mm import state
    a = core.new_agent("t")
    core.retire(a["name"])
    r = subprocess.run(["uv", "run", "mm", "wait", "-a", a["name"], "--timeout", "2"],
                       capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 1 and "retired or deleted" in r.stderr and "Traceback" not in r.stderr
    r = subprocess.run(["uv", "run", "mm", "wait", "-a", "t-nope00", "--timeout", "2"],
                       capture_output=True, text=True, cwd=state.REPO)
    assert r.returncode == 1 and "unknown agent" in r.stderr and "Traceback" not in r.stderr


def test_dropped_socket_returns_empty_not_traceback(pair, monkeypatch):
    """A websocket that closes mid-wait must end the wait cleanly, like a timeout."""
    import websockets
    from mm import ws as wsmod

    class DeadSocket:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def send(self, _): pass
        calls = 0
        async def recv(self):
            DeadSocket.calls += 1
            if DeadSocket.calls <= 2:  # hello + auth reply
                return "{}"
            raise websockets.exceptions.ConnectionClosedError(None, None)

    monkeypatch.setattr(wsmod.websockets, "connect", lambda url: DeadSocket())
    _, b = pair
    assert wsmod.wait(b["name"], 5) == []
