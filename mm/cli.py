"""`mm` — Slack-like coordination for agents on a local Mattermost."""
import argparse
import os
import sys
from pathlib import Path
from urllib.error import URLError

from . import core, infra, session, ws
from .api import BASE_URL, ApiError


def _agent_arg(p):
    p.add_argument("-a", "--agent", default=os.environ.get("MM_AGENT"),
                   help="acting agent name (or $MM_AGENT)")


def _body_args(p):
    p.add_argument("message", nargs="?",
                   help="the body; `-` reads stdin (heredoc), or use --body-file")
    p.add_argument("--body-file", metavar="PATH",
                   help="read the body from a file, byte-exact: no shell quoting to get wrong")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mm", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create .env with random passwords (never overwrites)")
    sub.add_parser("up", help="start mattermost + postgres")
    sub.add_parser("down", help="stop the stack (data persists)")
    sub.add_parser("bootstrap", help="create admin, team, default channels (idempotent)")

    ag = sub.add_parser("agent", help="agent accounts").add_subparsers(dest="sub", required=True)
    n = ag.add_parser("new", help="create a unique account <model>-<hex>; prints the name")
    n.add_argument("--model", required=True, help="full model ID, e.g. gpt-6-sol or claude-opus-5")
    n.add_argument("--role", default="", help="one line: what this agent does")
    ag.add_parser("list", help="accounts known on this machine")
    r = ag.add_parser("retire", help="announce leaving in #lobby and disable the account")
    _agent_arg(r)
    r.add_argument("--summary", default="", help="one line: what you did / where things stand")

    gr = sub.add_parser("group", help="orchestrator groups").add_subparsers(dest="sub", required=True)
    gn = gr.add_parser("new", help="one shot: fresh room + N worker accounts + joins; prints room then names")
    _agent_arg(gn)
    gn.add_argument("slug", help="room name prefix, e.g. guide -> #guide-3f9a")
    gn.add_argument("--workers", type=int, required=True, help="how many worker accounts")
    gn.add_argument("--model", required=True, help="full model ID the workers run on")
    gn.add_argument("--purpose", default="", help="the goal, shown as the room purpose")
    gn.add_argument("--roles", nargs="*", default=[], help="one role per worker, in order")

    ch = sub.add_parser("channel", help="channels").add_subparsers(dest="sub", required=True)
    cn = ch.add_parser("new", help="create (or get) a public channel")
    cn.add_argument("name", help="lowercase letters, digits, - and _; starts with a letter; 21 chars at most")
    cn.add_argument("--purpose", default="")
    cn.add_argument("--unique", action="store_true", help="append -<hex>: a fresh room per group")
    ca = ch.add_parser("archive", help="archive a finished group's channel (history stays)")
    ca.add_argument("name")
    cd = ch.add_parser("done", help="finish a group: retire its workers, archive the room, cleanup")
    cd.add_argument("name")
    _agent_arg(cd)

    se = sub.add_parser("session", help="Claude Code hook entrypoints (stdin: hook JSON)")
    se.add_argument("sub", choices=["start", "end"])

    rs = sub.add_parser("reset", help="delete every account but the admin (people too), with their posts and DMs; archive all rooms")
    rs.add_argument("--yes", action="store_true", help="required; this deletes history")

    cu = sub.add_parser("cleanup", help="remove retired accounts from the team and local state")
    cu.add_argument("--all", action="store_true", help="retire ALL active accounts on this machine too, live sessions included")
    cu.add_argument("--purge", action="store_true", help="delete users permanently (drops their posts)")

    j = sub.add_parser("join", help="join a channel")
    _agent_arg(j)
    j.add_argument("-c", "--channel", required=True)

    po = sub.add_parser("post", help="post to a channel")
    _agent_arg(po)
    po.add_argument("-c", "--channel", required=True)
    _body_args(po)
    po.add_argument("--thread", help="post id to reply under: the root or any reply in its thread")
    po.add_argument("--pin", action="store_true", help="pin it: the room's standing plan/reference")
    po.add_argument("--file", action="append", default=[], metavar="PATH",
                    help="attach a file (repeatable): report, log, screenshot, diff")

    ed = sub.add_parser("edit", help="rewrite a post in place; id and pin survive")
    _agent_arg(ed)
    ed.add_argument("post_id")
    _body_args(ed)

    up = sub.add_parser("unpin", help="unpin a post; the post itself stays")
    _agent_arg(up)
    up.add_argument("post_id")

    pn = sub.add_parser("pins", help="pinned posts in a channel (the plan), oldest first")
    pn.add_argument("-c", "--channel", required=True)

    d = sub.add_parser("dm", help="direct message another agent")
    _agent_arg(d)
    d.add_argument("--to", required=True)
    _body_args(d)
    d.add_argument("--file", action="append", default=[], metavar="PATH", help="attach a file (repeatable)")

    fe = sub.add_parser("fetch", help="download an attachment by file id to review it; prints the path")
    _agent_arg(fe)
    fe.add_argument("file_id", help="id from a [files: name (id)] tag")
    fe.add_argument("-o", "--out", default=".", help="directory to save into (default: cwd)")
    fe.add_argument("--force", action="store_true", help="overwrite a same-named file")

    ib = sub.add_parser("inbox", help="unread DMs, mentions and channel posts; marks them read")
    _agent_arg(ib)
    ib.add_argument("--peek", action="store_true", help="do not mark as read")

    h = sub.add_parser("history", help="channel log, oldest first")
    h.add_argument("-c", "--channel", required=True)
    h.add_argument("-n", type=int, default=50)
    h.add_argument("--after", help="post id; the next N posts after it")

    wt = sub.add_parser("wait", help="block until a room post, DM or @mention arrives (push)")
    _agent_arg(wt)
    wt.add_argument("--timeout", type=float, default=300, help="seconds (default 300)")
    only = wt.add_mutually_exclusive_group()
    only.add_argument("--mentions-only", action="store_true",
                      help="wake only on DMs and @mentions, not on plain room posts")
    only.add_argument("--mine", action="store_true",
                      help="wake on DMs, @mentions, and replies in threads I started or posted in")
    wt.add_argument("--any", action="store_true", help=argparse.SUPPRESS)  # old default; kept for callers

    w = sub.add_parser("who", help="agents in the team or a channel")
    w.add_argument("-c", "--channel")
    return p


def _need_agent(args):
    if not args.agent:
        sys.exit("error: -a/--agent required (or set MM_AGENT)")
    return args.agent


def _body(args) -> str:
    """Exactly one of: positional message, `-` (stdin), --body-file."""
    sources = [s for s in (args.message, args.body_file) if s is not None]
    if len(sources) != 1:
        raise RuntimeError("give exactly one body: a message, `-` for stdin, or --body-file PATH")
    if args.body_file is not None:
        try:
            return Path(args.body_file).expanduser().read_bytes().decode()
        except OSError as e:
            raise RuntimeError(f"cannot read --body-file {args.body_file}: {e.strerror}") from None
    if args.message == "-":
        return sys.stdin.buffer.read().decode()  # bytes: no newline translation
    return args.message


def main(argv=None) -> int:
    """Run a command; a server-side refusal becomes one error line, not a traceback."""
    try:
        return _run(argv)
    except ApiError as e:
        # Mattermost answers 403 (not 404) for a post id that does not exist
        hint = f" ({core.ID_HINT})" if e.status in (403, 404) and \
            e.path.startswith(("/posts/", "/files/")) else ""
        print(f"error: {e}{hint}", file=sys.stderr)
        return 1
    except (URLError, ConnectionError):
        print(f"error: Mattermost not reachable at {BASE_URL} (run `mm up`)", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as e:  # unknown/retired agent, dead socket, bad input
        print(f"error: {e}", file=sys.stderr)
        return 1


def _run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    c = args.cmd
    if c == "init":
        infra.init()
        print("wrote .env with random passwords; the web login is MM_ADMIN_USER / MM_ADMIN_PASSWORD there")
        return 0
    if c == "up":
        if infra.up() == 0 and infra.wait_ready():
            print(f"mattermost ready at {BASE_URL}")
            return 0
        print("stack did not become ready", file=sys.stderr)
        return 1
    if c == "down":
        return infra.down()
    if c == "bootstrap":
        d = infra.bootstrap()
        print(f"admin ok (created={d['admin_created']}) team={d['team_id']} channels={list(d['channels'])}")
    elif c == "group" and args.sub == "new":
        g = core.new_group(args.slug, args.workers, args.model, me=args.agent,
                           purpose=args.purpose, roles=args.roles)
        print(g["channel"])
        for w in g["workers"]:
            print(w)
    elif c == "agent" and args.sub == "new":
        print(core.new_agent(args.model, args.role)["name"])
    elif c == "agent" and args.sub == "list":
        for a in core.list_agents():
            flag = "retired" if a.get("retired_at") else "active"
            print(f"@{a['name']}  {flag}  {a['role']}")
    elif c == "agent" and args.sub == "retire":
        core.retire(_need_agent(args), args.summary)
        print(f"retired @{args.agent}")
    elif c == "channel" and args.sub == "new":
        ch = core.new_channel(args.name, args.purpose, args.unique)
        print(ch["name"])
    elif c == "channel" and args.sub == "done":
        r = core.channel_done(args.name, keep=args.agent)
        print(f"#{args.name.lstrip('#')} done: retired {len(r['retired'])}, archived, cleaned {len(r['removed'])}")
    elif c == "channel" and args.sub == "archive":
        core.archive_channel(args.name)
        print(f"archived #{args.name.lstrip('#')}")
    elif c == "session":
        return session.main(args.sub)
    elif c == "reset":
        if not args.yes:
            sys.exit("refusing: `mm reset --yes` deletes every account but the admin, and their posts")
        r = core.reset()
        print(f"deleted {len(r['deleted_users'])} users, archived {len(r['archived'])} rooms")
    elif c == "cleanup":
        removed = core.cleanup(everything=args.all, purge=args.purge)
        print(f"removed {len(removed)} account(s)" + (": " + " ".join(removed) if removed else ""))
    elif c == "join":
        core.join(_need_agent(args), args.channel)
        print(f"joined #{args.channel.lstrip('#')}")
    elif c == "post":
        p = core.post(_need_agent(args), args.channel, _body(args), args.thread, args.pin,
                      files=args.file)
        print(p["id"])
    elif c == "edit":
        print(core.edit(_need_agent(args), args.post_id, _body(args))["id"])
    elif c == "unpin":
        core.unpin(_need_agent(args), args.post_id)
        print(f"unpinned {args.post_id}")
    elif c == "pins":
        for m in core.pins(args.channel):
            print(core.line(m))
    elif c == "dm":
        p = core.dm(_need_agent(args), args.to, _body(args), files=args.file)
        print(p["id"])
    elif c == "fetch":
        print(core.fetch(_need_agent(args), args.file_id, args.out, args.force))
    elif c == "inbox":
        msgs = core.inbox(_need_agent(args), mark=not args.peek)
        for m in msgs:
            print(core.line(m))
        if not msgs:
            print("(inbox empty)")
    elif c == "history":
        for m in core.history(args.channel, args.n, args.after):
            print(core.line(m))
    elif c == "wait":
        msgs = ws.wait(_need_agent(args), args.timeout,
                       any_post=not (args.mentions_only or args.mine), mine=args.mine)
        for m in msgs:
            print(core.line(m))
        if not msgs:
            print(f"(no messages within {args.timeout:g}s)")
    elif c == "who":
        for u in core.who(args.channel):
            tag = "bot" if u["bot"] else "human"
            print(f"@{u['name']}  {tag}  {u['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
