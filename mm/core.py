"""Core operations: agents, channels, posting, reading. CLI and tests call these."""
import re
import secrets
import time
from pathlib import Path

from . import infra, state
from .api import Api, ApiError
from .infra import DEFAULT_CHANNELS

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,20}$")
ID_HINT = "check the id (ids are lowercase, 26 chars)"


def norm_id(raw: str) -> str:
    """Mattermost ids are always [a-z0-9]{26}. Agents have been seen quoting them with a
    capitalized first letter, which the server answers with a misleading 403."""
    return raw.strip().lower()


# ---- auth helpers ----------------------------------------------------------

def admin() -> dict:
    a = state.load_admin()
    if not a:
        raise RuntimeError("no state/admin.json — run `mm bootstrap`")
    return a


def admin_api() -> Api:
    return Api(admin()["token"])


def agent(name: str) -> dict:
    a = state.load_agent(name)
    if not a:
        raise RuntimeError(f"unknown agent '{name}' — run `mm agent new` or check `mm agent list`")
    return a


def agent_api(name: str) -> Api:
    return Api(agent(name)["token"])


# ---- agents ----------------------------------------------------------------

def model_family(raw: str | None) -> str:
    """Use Claude families or a GPT version and variant as the account name prefix."""
    if not raw:
        return "claude"
    token = re.sub(r"\[.*?\]", "", raw).lower()
    for fam in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if fam in token:
            return fam
    gpt = re.fullmatch(r"gpt[-_]?(\d+(?:[._]\d+)?)(?:[-_]([a-z][a-z0-9]*))?", token)
    if gpt:
        version = gpt.group(1).replace(".", "-").replace("_", "-")
        variant = f"-{gpt.group(2)}" if gpt.group(2) else ""
        return f"gpt{version}{variant}"
    token = re.sub(r"[^a-z0-9]", "", token)[:12]
    return token or "claude"


def new_agent(base: str, role: str = "", announce: bool = True) -> dict:
    """Create a unique bot account `<model>-<6hex>`, join the team and #lobby, save the
    token, and say hello in #lobby. `base` is the model ID (fable, gpt-6-sol, ...)."""
    base = model_family(base) if base not in ("t",) else base  # 't' = test accounts
    if not NAME_RE.match(base):
        raise ValueError("base name must match [a-z][a-z0-9_-]{0,20}")
    name = f"{base}-{secrets.token_hex(3)}"
    api = admin_api()
    a = admin()
    bot = api.post("/bots", {"username": name, "display_name": name, "description": role})
    token = api.post(f"/users/{bot['user_id']}/tokens", {"description": "mm agent"})["token"]
    api.post(f"/teams/{a['team_id']}/members", {"team_id": a["team_id"], "user_id": bot["user_id"]})
    # Only #lobby by default: an agent's room is whatever it is told to join.
    api.post(f"/channels/{a['channels']['lobby']}/members", {"user_id": bot["user_id"]})
    data = {"name": name, "model": base, "role": role, "user_id": bot["user_id"], "token": token,
            "created_at": int(time.time() * 1000), "channels": ["lobby"]}
    state.save_agent(name, data)
    if announce:
        post(name, "lobby", f":large_green_circle: @{name} joined" + (f" — {role}" if role else ""))
    return data


def retire(agent_name: str, summary: str = "") -> dict:
    """Say goodbye in #lobby, then disable the account. History and state file stay."""
    me = agent(agent_name)
    if me.get("retired_at"):
        return me
    post(agent_name, "lobby", f":red_circle: @{agent_name} leaving" + (f" — {summary}" if summary else ""))
    admin_api().post(f"/bots/{me['user_id']}/disable")
    me["retired_at"] = int(time.time() * 1000)
    state.save_agent(agent_name, me)
    return me


def cleanup(everything: bool = False, purge: bool = False) -> list[str]:
    """Remove retired accounts from the team and this machine's state.

    everything=True retires active accounts first. purge=True deletes the users
    permanently on the server, which also deletes their posts; default keeps history.
    """
    api = admin_api()
    tid = admin()["team_id"]
    removed = []
    for a in list_agents():
        if not a.get("retired_at"):
            if not everything:
                continue
            retire(a["name"], "cleanup")
        if purge:
            api.request("DELETE", f"/users/{a['user_id']}", params={"permanent": "true"})
            # creating a bot opens a DM between it and the admin; drop it from the admin sidebar
            try:
                api.request("DELETE", f"/users/{admin()['user_id']}/preferences",
                            body=[{"user_id": admin()["user_id"], "category": "direct_channel_show",
                                   "name": a["user_id"]}])
            except ApiError:
                pass
        else:
            try:
                api.delete(f"/teams/{tid}/members/{a['user_id']}")
            except ApiError as e:
                if e.status != 404:
                    raise
        (state.AGENTS_DIR / f"{a['name']}.json").unlink(missing_ok=True)
        removed.append(a["name"])
    return removed


def list_agents() -> list[dict]:
    return [agent(n) for n in state.list_agents()]


def user_by_name(username: str) -> dict:
    return admin_api().get(f"/users/username/{username.lstrip('@')}")


# ---- channels --------------------------------------------------------------

def channel_id(name: str) -> str:
    name = name.lstrip("#")
    a = admin()
    if name in a["channels"]:
        return a["channels"][name]
    return admin_api().get(f"/teams/{a['team_id']}/channels/name/{name}", {"include_deleted": "true"})["id"]


def new_channel(name: str, purpose: str = "", unique: bool = False) -> dict:
    """Create a public channel. unique=True appends -<4hex> so each group gets a fresh room."""
    name = name.lstrip("#")
    if not NAME_RE.match(name):
        raise ValueError("channel name must match [a-z][a-z0-9_-]{0,20}")
    if unique:
        name = f"{name[:15]}-{secrets.token_hex(2)}"
    a = admin()
    try:
        return admin_api().get(f"/teams/{a['team_id']}/channels/name/{name}")
    except ApiError as e:
        if e.status != 404:
            raise
    return admin_api().post("/channels", {"team_id": a["team_id"], "name": name,
                                          "display_name": name, "purpose": purpose, "type": "O"})


def archive_channel(name: str) -> None:
    """Archive (soft-delete): gone from sidebars, history stays readable. Idempotent."""
    cid = channel_id(name)
    if admin_api().get(f"/channels/{cid}")["delete_at"]:
        return None
    admin_api().delete(f"/channels/{cid}")
    return None


def channel_done(name: str, keep: str | None = None) -> dict:
    """End a group in one call: retire every active bot member except `keep`
    (and any session account), archive the room, run cleanup."""
    cid = channel_id(name)
    members = admin_api().get(f"/channels/{cid}/members", {"per_page": 200})
    ids = {m["user_id"] for m in members}
    retired = []
    for a in list_agents():
        if a["user_id"] in ids and not a.get("retired_at") and a["name"] != keep \
                and not a["role"].startswith("Claude session"):
            retire(a["name"], summary=f"#{name.lstrip('#')} done")
            retired.append(a["name"])
    archive_channel(name)
    removed = cleanup()
    return {"retired": retired, "removed": removed}


def new_group(slug: str, workers: int, model: str, me: str | None = None,
              purpose: str = "", roles: list[str] | None = None, announce: bool = True) -> dict:
    """One-shot orchestrator setup: a fresh unique room, `workers` accounts on `model`,
    everyone (plus `me`) joined. Returns {"channel": name, "workers": [names]}."""
    ch = new_channel(slug, purpose, unique=True)["name"]
    roles = roles or []
    names = []
    for i in range(workers):
        role = roles[i] if i < len(roles) else f"worker {i + 1} in #{ch}"
        names.append(new_agent(model, role, announce=announce)["name"])
    for name in ([me] if me else []) + names:
        join(name, ch)
    return {"channel": ch, "workers": names}


def join(agent_name: str, channel: str) -> None:
    me = agent(agent_name)
    cid = channel_id(channel)
    agent_api(agent_name).post(f"/channels/{cid}/members", {"user_id": me["user_id"]})
    ch = channel.lstrip("#")
    if ch not in me["channels"]:
        me["channels"].append(ch)
        state.save_agent(agent_name, me)


# ---- messages --------------------------------------------------------------

def _read_files(paths: list[str] | None) -> list[tuple[str, bytes]]:
    """Read every attachment up front so a bad path fails before anything is posted."""
    out = []
    for raw in paths or []:
        f = Path(raw).expanduser()
        if not f.is_file():
            raise RuntimeError(f"no such file: {raw}")
        out.append((f.name, f.read_bytes()))
    return out


def _send(api: Api, cid: str, body: dict, files: list[str] | None) -> dict:
    """Post to a channel id, uploading any attachments into that channel first."""
    blobs = _read_files(files)
    if blobs:
        body["file_ids"] = [api.upload(cid, name, data)["id"] for name, data in blobs]
    return api.post("/posts", {"channel_id": cid, **body})


def fetch(agent_name: str, file_id: str, out_dir: str = ".", force: bool = False) -> Path:
    """Download an attachment (by the id shown in [files: ...]) as this agent; returns the saved path."""
    api = agent_api(agent_name)
    file_id = norm_id(file_id)
    info = api.get(f"/files/{file_id}/info")
    dest = Path(out_dir).expanduser() / Path(info["name"]).name
    if dest.exists() and not force:
        raise RuntimeError(f"{dest} exists; fetch into another -o dir or pass --force")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(api.get_raw(f"/files/{file_id}"))
    return dest


def thread_root(api: Api, post_id: str) -> str:
    """The root id of the thread `post_id` is in (itself if it is a root). Mattermost
    threads are flat and only accept the root as root_id; agents naturally quote the
    reply they are answering."""
    post_id = norm_id(post_id)
    try:
        p = api.get(f"/posts/{post_id}")
    except ApiError as e:
        if e.status in (403, 404):
            raise RuntimeError(f"no post {post_id} to reply under; {ID_HINT}") from None
        raise
    return p.get("root_id") or p["id"]


def post(agent_name: str, channel: str, message: str, thread_id: str | None = None,
         pin: bool = False, files: list[str] | None = None) -> dict:
    body = {"message": message}
    api = agent_api(agent_name)
    if thread_id:
        body["root_id"] = thread_root(api, thread_id)
    p = _send(api, channel_id(channel), body, files)
    if pin:  # a pinned post is the room's standing reference (plan, roles, interfaces)
        api.post(f"/posts/{p['id']}/pin")
    return p


def edit(agent_name: str, post_id: str, message: str) -> dict:
    """Rewrite a post in place. The id (and any pin) survives, so references stay valid."""
    return agent_api(agent_name).put(f"/posts/{norm_id(post_id)}/patch", {"message": message})


def unpin(agent_name: str, post_id: str) -> None:
    agent_api(agent_name).post(f"/posts/{norm_id(post_id)}/unpin")


def pins(channel: str) -> list[dict]:
    """Pinned posts in a channel, oldest first."""
    cid = channel_id(channel)
    page = admin_api().get(f"/channels/{cid}/pinned")
    posts = [page["posts"][pid] for pid in page["order"]]
    posts.sort(key=lambda q: q["create_at"])
    return [fmt(q, channel=channel.lstrip("#")) for q in posts]


def dm(agent_name: str, to: str, message: str, files: list[str] | None = None) -> dict:
    me = agent(agent_name)
    other = user_by_name(to)
    api = agent_api(agent_name)
    ch = api.post("/channels/direct", [me["user_id"], other["id"]])
    return _send(api, ch["id"], {"message": message}, files)


def history(channel: str, n: int = 50, after: str | None = None) -> list[dict]:
    """Channel log, oldest first, system posts dropped. `after` is a post id."""
    cid = channel_id(channel)
    # over-fetch: system posts (joins etc.) are filtered out after the fact
    params = {"per_page": min(n * 3, 200)}
    if after:
        params["after"] = after
    page = admin_api().get(f"/channels/{cid}/posts", params)
    posts = [page["posts"][pid] for pid in page["order"]]
    posts = [p for p in posts if not p["type"].startswith("system_")]
    posts = list(reversed(posts))[-n:]
    return [fmt(p, channel=channel.lstrip("#")) for p in posts]


def who(channel: str | None = None) -> list[dict]:
    a = admin()
    api = admin_api()
    params = {"in_channel": channel_id(channel)} if channel else {"in_team": a["team_id"]}
    users = api.get("/users", {**params, "per_page": 200})
    statuses = {s["user_id"]: s for s in api.post("/users/status/ids", [u["id"] for u in users])}
    return [{"name": u["username"], "bot": u.get("is_bot", False),
             "status": statuses.get(u["id"], {}).get("status", "?"),
             "last_activity": statuses.get(u["id"], {}).get("last_activity_at", 0)}
            for u in users]


# ---- formatting ------------------------------------------------------------

_names: dict[str, str] = {}


def username(user_id: str) -> str:
    if user_id not in _names:
        _names[user_id] = admin_api().get(f"/users/{user_id}")["username"]
    return _names[user_id]


def fmt(p: dict, channel: str = "", kind: str | None = None) -> dict:
    """Normalize a post into what an agent needs to read."""
    return {"id": p["id"], "from": username(p["user_id"]), "channel": channel, "channel_id": p["channel_id"],
            "kind": kind or "post", "thread": p.get("root_id") or "", "ts": p["create_at"], "message": p["message"],
            "files": [{"id": f["id"], "name": f["name"]} for f in (p.get("metadata") or {}).get("files") or []]}


def line(m: dict) -> str:
    where = f"#{m['channel']}" if m["channel"] else "dm"
    tag = " !mention" if m["kind"] == "mention" else ""
    thread = f" thread={m['thread']}" if m["thread"] else ""
    files = m.get("files") or []
    att = f" [files: {', '.join(f'{f['name']} ({f['id']})' for f in files)}]" if files else ""
    return f"[{m['id']}] @{m['from']} ({where}{tag}{thread}){att}: {m['message']}"


# ---- inbox -----------------------------------------------------------------
#
# Delivery is tracked with a local cursor per channel in the agent's state file:
#   cursors[channel_id] = {"ts": <create_at of newest delivered post>, "ids": [ids at that ts]}
# The server's "viewed" mark is only for the UI. Trusting it lost messages that
# landed between reading a channel and marking it.

def _cursor(me: dict, cid: str) -> dict:
    return me.setdefault("cursors", {}).get(cid) or {"ts": me["created_at"], "ids": []}


def _undelivered(me: dict, api: Api, ch: dict) -> list[dict]:
    cur = _cursor(me, ch["id"])
    page = api.get(f"/channels/{ch['id']}/posts", {"since": cur["ts"]})
    posts = [page["posts"][pid] for pid in page["order"]]
    posts = [p for p in posts
             if not p["type"].startswith("system_") and p["user_id"] != me["user_id"]
             and not p.get("delete_at")  # pin/edit keeps the old version as a deleted row
             and (p["create_at"] > cur["ts"] or (p["create_at"] == cur["ts"] and p["id"] not in cur["ids"]))]
    return sorted(posts, key=lambda p: (p["create_at"], p["id"]))


def _advance(me: dict, cid: str, posts: list[dict]) -> None:
    if not posts:
        return
    top = posts[-1]["create_at"]
    cur = _cursor(me, cid)
    ids = [p["id"] for p in posts if p["create_at"] == top]
    if top == cur["ts"]:
        ids = cur["ids"] + ids
    me.setdefault("cursors", {})[cid] = {"ts": top, "ids": ids}


def _classify(me: dict, ch: dict, p: dict) -> dict:
    if ch["type"] == "D":
        return fmt(p, channel="", kind="dm")
    kind = "mention" if f"@{me['name']}" in p["message"] else "post"
    return fmt(p, channel=ch["name"], kind=kind)


def inbox(agent_name: str, mark: bool = True, only: set[str] | None = None) -> list[dict]:
    """Everything undelivered for this agent across DMs and joined channels, oldest first.

    kind: "dm" | "mention" | "post". Own posts and system posts are dropped.
    mark=True advances the delivery cursor (and marks the channel viewed for the UI).
    only= restricts to those channel ids.
    """
    me = agent(agent_name)
    api = agent_api(agent_name)
    tid = admin()["team_id"]
    out = []
    for ch in api.get(f"/users/{me['user_id']}/teams/{tid}/channels"):
        if only is not None and ch["id"] not in only:
            continue
        posts = _undelivered(me, api, ch)
        if not posts:
            continue
        out += [_classify(me, ch, p) for p in posts]
        if mark:
            _advance(me, ch["id"], posts)
            api.post(f"/channels/members/{me['user_id']}/view", {"channel_id": ch["id"]})
    if mark:
        state.save_agent(agent_name, me)
    return sorted(out, key=lambda m: (m["ts"], m["id"]))


# ---- reset -----------------------------------------------------------------

KEEP_USERS = {"admin", "system-bot", "feedbackbot", "playbooks", "calls", "boards"}
_ORPHAN_SQL = """
CREATE TEMP TABLE orphan AS
  SELECT c.id FROM channels c
  WHERE c.type IN ('D','G')
    AND (SELECT count(*) FROM channelmembers m JOIN users u ON u.id = m.userid
         WHERE m.channelid = c.id) < 2;
DELETE FROM posts WHERE channelid IN (SELECT id FROM orphan);
DELETE FROM channelmembers WHERE channelid IN (SELECT id FROM orphan);
DELETE FROM sidebarchannels WHERE channelid IN (SELECT id FROM orphan);
DELETE FROM channelmemberhistory WHERE channelid IN (SELECT id FROM orphan);
DELETE FROM channels WHERE id IN (SELECT id FROM orphan);
DELETE FROM preferences WHERE category IN ('direct_channel_show','group_channel_show')
  AND name NOT IN (SELECT id FROM users);
"""


def reset_victims(users: list[dict], admin_id: str) -> dict:
    """Who `mm reset` deletes, as {id: username}: everyone but system users and the admin."""
    return {u["id"]: u["username"] for u in users
            if u["id"] != admin_id and u["username"] not in KEEP_USERS}


def reset() -> dict:
    """Wipe every agent: delete all non-system users permanently (posts go with them),
    remove the orphaned DM channels Mattermost leaves behind (SQL, since the API refuses
    to delete D/G channels), archive every non-default room, clear local state."""
    import subprocess
    api = admin_api()
    a = admin()
    users = api.get("/users", {"per_page": 200}) + api.get("/users", {"per_page": 200, "inactive": "true"})
    victims = reset_victims(users, a["user_id"])
    for uid in victims:
        api.request("DELETE", f"/users/{uid}", params={"permanent": "true"})
    sql = subprocess.run([*infra.compose_cmd(), "exec", "-T", "postgres", "psql", "-U", "mmuser",
                          "-d", "mattermost", "-v", "ON_ERROR_STOP=1", "-q"],
                         input="BEGIN;" + _ORPHAN_SQL + "COMMIT;", capture_output=True, text=True,
                         cwd=state.REPO)
    if sql.returncode != 0:
        raise RuntimeError("orphan DM cleanup failed: " + sql.stderr.strip())
    archived = []
    for c in api.get(f"/teams/{a['team_id']}/channels", {"per_page": 200}):
        if c["name"] not in ("town-square", "off-topic", *DEFAULT_CHANNELS) and not c["delete_at"]:
            archive_channel(c["name"])
            archived.append(c["name"])
    for p in list(state.AGENTS_DIR.glob("*.json")) + list((state.STATE_DIR / "sessions").glob("*.json")):
        p.unlink()
    return {"deleted_users": sorted(victims.values()), "archived": archived}
