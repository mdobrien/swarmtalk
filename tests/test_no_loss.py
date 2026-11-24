"""Delivery is exactly-once: nothing lost between a wake and the re-arm, nor in bursts."""
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


def _wait_in_thread(name, timeout):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("msgs", ws.wait(name, timeout)))
    t.start()
    return t, box


def test_message_between_wake_and_rearm(pair):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10)
    time.sleep(0.7)
    m1 = core.dm(a["name"], b["name"], "first")
    t.join(5)
    assert box["msgs"][0]["id"] == m1["id"]
    m2 = core.dm(a["name"], b["name"], "landed before re-arm")   # listener is down now
    t0 = time.monotonic()
    got = ws.wait(b["name"], 5)                                   # re-arm
    assert [m["id"] for m in got] == [m2["id"]] and time.monotonic() - t0 < 2


def test_burst_in_one_channel_all_delivered_once(pair):
    a, b = pair
    t, box = _wait_in_thread(b["name"], 10)
    time.sleep(0.7)
    sent = [core.dm(a["name"], b["name"], f"burst {i}")["id"] for i in range(3)]
    t.join(5)
    delivered = [m["id"] for m in box["msgs"]] + [m["id"] for m in core.inbox(b["name"])]
    assert sorted(delivered) == sorted(sent), "every burst message delivered"
    assert len(delivered) == len(set(delivered)), "no duplicates"
    assert core.inbox(b["name"]) == []


def test_inbox_peek_then_post_then_read(pair, room):
    a, b = pair
    m1 = core.post(a["name"], room, "one")
    assert [m["id"] for m in core.inbox(b["name"], mark=False)] == [m1["id"]]
    m2 = core.post(a["name"], room, "two")
    assert [m["id"] for m in core.inbox(b["name"])] == [m1["id"], m2["id"]]
    assert core.inbox(b["name"]) == []


def test_forced_race_message_during_mark(pair, monkeypatch):
    """A message that lands between reading and marking the channel must not be lost."""
    a, b = pair
    from mm.api import Api

    class RacyApi(Api):
        def post(self, path, body=None):
            if path.endswith("/view"):
                core.dm(a["name"], b["name"], "slipped in during mark")
            return super().post(path, body)

    real = core.agent_api
    monkeypatch.setattr(ws.core, "agent_api", lambda n: RacyApi(real(n).token))
    monkeypatch.setattr(core, "agent_api", lambda n: RacyApi(real(n).token))
    t, box = _wait_in_thread(b["name"], 10)
    time.sleep(0.7)
    m1 = core.dm(a["name"], b["name"], "trigger")
    t.join(5)
    monkeypatch.setattr(core, "agent_api", real)
    monkeypatch.setattr(ws.core, "agent_api", real)
    assert box["msgs"][0]["id"] == m1["id"]
    later = [m["message"] for m in core.inbox(b["name"])]
    assert "slipped in during mark" in later
