"""Push: block on the Mattermost WebSocket until this agent is messaged."""
import asyncio
import json
import sys
import time

import websockets

from . import core
from .api import BASE_URL

WS_URL = BASE_URL.replace("http", "ws", 1) + "/api/v4/websocket"


def wait(agent_name: str, timeout_s: float = 300, any_post: bool = False,
         mine: bool = False) -> list[dict]:
    """Return the first message(s) for this agent: a DM or @mention, or any post with any_post.
    mine=True also wakes on replies in threads this agent started or posted in.

    Order of operations avoids a lost-message window: open+auth the socket first, then
    check the REST inbox for anything that arrived before the socket, then listen.
    Everything returned is marked read.
    """
    return asyncio.run(_wait(agent_name, timeout_s, any_post, mine))


def _wanted(kind: str, any_post: bool, channel: str = "") -> bool:
    """DMs and @mentions always wake. Plain posts wake with any_post, except in #lobby,
    whose join/leave notices would otherwise wake every waiting agent."""
    if kind in ("dm", "mention"):
        return True
    return any_post and channel != "lobby"


class _MyThreads:
    """Is a thread root one this agent started or posted in? One lookup per root per wait."""

    def __init__(self, me: dict):
        self.me, self.api, self.seen = me, core.agent_api(me["name"]), {}

    def __contains__(self, root_id: str) -> bool:
        if root_id not in self.seen:
            t = self.api.get(f"/posts/{root_id}/thread")
            self.seen[root_id] = any(p["user_id"] == self.me["user_id"] for p in t["posts"].values())
        return self.seen[root_id]


async def _wait(agent_name: str, timeout_s: float, any_post: bool, mine: bool = False) -> list[dict]:
    me = core.agent(agent_name)
    my_threads = _MyThreads(me) if mine else ()

    def wanted(kind: str, channel: str, thread: str) -> bool:
        return _wanted(kind, any_post, channel) or bool(thread and thread in my_threads)
    deadline = time.monotonic() + timeout_s
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"seq": 1, "action": "authentication_challenge",
                                  "data": {"token": me["token"]}}))
        try:
            await ws.recv()  # hello
            await ws.recv()  # auth reply
        except websockets.exceptions.ConnectionClosed:
            raise RuntimeError(f"@{agent_name}: server refused the connection; the account "
                               "is retired or deleted (mm agent list)") from None

        # Anything that arrived before the socket opened. Deliver every undelivered
        # message in the channels that have a wanted one; other channels stay untouched.
        peek = core.inbox(agent_name, mark=False)
        hot = {m["channel_id"] for m in peek if wanted(m["kind"], m["channel"], m["thread"])}
        if hot:
            return core.inbox(agent_name, mark=True, only=hot)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return []
            except websockets.exceptions.ConnectionClosed:
                # Server restart, laptop sleep, network blip: nothing is lost, the
                # inbox cursor still holds it. Treat like a timeout so loops carry on.
                print("(connection dropped; run mm wait again)", file=sys.stderr)
                return []
            ev = json.loads(raw)
            if ev.get("event") != "posted":
                continue
            d = ev["data"]
            p = json.loads(d["post"])
            if p["user_id"] == me["user_id"] or p["type"].startswith("system_"):
                continue
            if d["channel_type"] == "D":
                kind, cname = "dm", ""
            else:
                mentions = json.loads(d.get("mentions") or "[]")
                kind = "mention" if me["user_id"] in mentions else "post"
                cname = d["channel_name"]
            if not wanted(kind, cname, p.get("root_id") or ""):
                continue
            # Read the channel through the cursor rather than trusting the single event,
            # so a burst in the same channel is delivered in full.
            return core.inbox(agent_name, mark=True, only={p["channel_id"]})
