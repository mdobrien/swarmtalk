---
name: agent-chat
description: 'Talk to other agents over the local Mattermost with the `mm` CLI — one channel per goal, DMs, threads, and a blocking wait for push. Use whenever your prompt gives you an @name or #channel, you are part of a multi-agent task, or you need to ask or tell another agent something.'
---

# agent-chat

You are one of several agents working at the same time. Coordinate through
Mattermost with the `mm` command (on PATH). A human watches at
http://localhost:8065 and may interject; treat their messages like a peer's.

## Server check (before anything else)
Run `mm who`. If it errors (connection refused, "not reachable"), the stack is
down: run `mm up` (docker, ~30s), then `mm bootstrap` (idempotent), then `mm who`
again. If you have no account yet, make one (see Identity).

## Identity
Exactly one account is yours. In priority order:
- Prompt says "You are @name"? That account exists and is yours. Use `-a name` on
  every command that accepts it (`pins`, `history` and `who` do not). This wins over anything session start said; never create or adopt
  another account.
- Otherwise, session start told you "Your Mattermost account for this session is
  @..."? That is you. Use `-a <that name>`. Other sessions the user opens get
  their own; the user may ask you to work with one of them by name.
- Otherwise make one: `mm agent new --model <full-model-id> --role "<one line>"`.
  For GPT models, include the variant: `gpt-6-sol`, `gpt-6-astra`, `gpt-6-luna`,
  or `gpt-5.6-terra`. These produce names such as `gpt6-sol-<hash>` and
  `gpt6-astra-<hash>`. Remember the printed name and pass `-a <name>` from then on, on every command that accepts it. Do not wrap `mm` in `$(...)`
  or compound shell; plain `mm ...` calls are what the permission allowlist covers.
- Never post as another agent.

## Chain of command
The orchestrator who spawned you is the lead. Go/no-go, tie-breaks, and anything
you would otherwise hold for the human go to the lead, in the goal channel. The
human watches and may interject; treat their posts as decisions, but do not park
work waiting for them. Never leave a blocker addressed to the human when the lead
can decide it.

## Loop
0. `mm pins -c <chan>` once at the start: the pinned post is the plan (who owns
   what, interfaces, definition of done). Re-check it if someone says it changed.
1. `mm inbox -a <me>` first thing, and at every task boundary (a step done, a
   decision coming up). Answer anything addressed to you before moving on.
2. Work. Post progress and decisions in the goal channel, one line each:
   `mm post -a <me> -c <chan> "..."`. Peers read the channel; do not repeat in DMs.
3. Need a peer? `@mention` them in the channel (visible to all) or `mm dm` for a
   1:1. Keep working on what does not depend on them. Never block without a
   timeout and an announced default: if you truly cannot proceed, `mm wait -a <me>
   --timeout 300`, and when it expires proceed on a stated assumption and post it.
   An announced default unblocks your own work; it never lands a change to a
   shared or signed artifact (a pin, a peer's file, an agreed text). For those,
   propose the change in-channel and hold for an ack from the owner or the lead;
   silence is not agreement, and a pending decision from the lead is not a
   timeout. Proceed on your own, propose on shared, never land on signed without
   an ack.
4. Asked something (DM or @mention)? Reply in the same place, then continue.
5. Finishing: drain `mm inbox`, post `DONE: <summary>` in the goal channel with
   your work attached (`--file`, see Artifacts) so peers can review it. If you
   created your own account, `mm agent retire -a <me> --summary "..."`. If an
   orchestrator created it, leave it; they retire it.
6. Stay present. While working, check `mm inbox` at every task boundary and at
   least every few minutes even mid-task; a peer or the human may be waiting on
   you. Whenever you are not actively working (after DONE, while blocked, while
   waiting on a peer), loop on `mm wait -a <me> --timeout 600` and answer what
   arrives. Leave the room only when the orchestrator says stop or the room is
   archived (posting fails with "deleted channel"). "unknown agent" means your account
   was retired: the group is over, so stop. Do not make another account.

## Artifacts: attach your work so peers can review it
Whenever you produce something a peer or the lead should check, attach the file
rather than describing it: test output, logs, a report or findings file, a
screenshot, a diff (`git diff > /tmp/x.diff`), a generated config or dataset
sample. A one-line summary plus the real file beats a long paste.
- `mm post -a <me> -c <chan> 'tests: 12 passed, see log' --file run.log --file cov.txt`
  (repeat `--file`, up to 10 per post; works on `mm dm` and on thread replies too).
- Name files so they cannot collide with a peer's: prefix your role or name
  (`A-findings.md`, `opus-3f9a1c-feedback.md`). Plain characters; the server
  rejects `\` in names. A revision is a new file (`A-findings-v2.md`) in the same
  thread: an attachment cannot be replaced in place.
- Attach a file once, where the discussion is: in the channel for new work, in the
  thread for a revision or reply. Do not re-post the same file to a DM as well.
- Readers see `[files: run.log (<file-id>)]` in the line header, before the message.
  `mm fetch -a <me> <file-id> -o <workdir>/peer/<author>/` saves it and prints the
  path, creating the dir. It refuses to overwrite an existing file, so fetch each
  peer into their own dir. For a peer's revision under the same name, fetch with
  `--force` into the same peer dir. Read it, then reply in the thread with what you
  checked (`REVIEWED <post-id>: ...`).
- Asked to review? Fetch and read the attachment; do not sign off from the summary.
- Code in the shared repo: name the path (and commit) instead of attaching a copy;
  attach a diff only when the change is not on disk for others yet.
- Never attach secrets (`.env`, tokens, `state/`). Keep files small and text where
  possible; the room is a record, not a dump.

## Conventions
- Only the plan is pinned. Do not pin probes or status posts; `mm pins` is the
  plan view for everyone. If you pin something to test it, unpin it.
- Run `mm inbox` on its own. Output from `mm pins` or `mm history` in the same
  command has the same line format, and a post seen there looks re-delivered.
- One group = one fresh room (`mm channel new <slug> --unique`). You are only in
  `#lobby` (join/leave announcements, never chat) and the room(s) you were told
  to join. Never post in `#general` or any room outside your group.
- Discussions go in threads: `--thread <post id>`. Any post in the thread works;
  `mm` resolves a reply's id to its root. Threads are flat: `thread=ID` in an output
  line is always the root, and Mattermost does not record which reply a reply
  answers, so quote the post id in your text when that matters.
- Decisions that affect others go in the channel prefixed `DECISION:` with the
  one-line reason and who it affects, never only in a DM. A DM is not a record.
- Sign off on a peer's text by post id (`AGREED <post-id>`), never by re-pasting
  it. One canonical copy; agreement is a pointer to it. Sign off against the file
  on disk, not the post's summary, and say so: `AGREED <post-id> (read on disk
  <time>)`. If the author changes it after your AGREED, re-read and re-ack.
- Before nudging a peer that something is missing, grep the file on disk and cite
  the post id you are waiting on. Most "still not done" nudges hit work that
  already landed.
- After a revision or reframing, grep your own file for lines that still carry the
  old framing. Stale lines in your own file are the usual source of
  contradictions.
- A read is a claim that expires. On a shared tree a file or the thread can change
  within minutes of your read. Re-read at the moment you assert, and say when you
  read it. Most false corrections come from a stale read.
- Dedupe is a write. Before deleting your copy of something because a peer holds
  one, read theirs and name it by id in the same post; otherwise two agents defer
  to each other and the artifact reaches zero holders.
- Claim a file in the channel before editing it, and one owner per file at a
  time. Repo edits belong to the lead unless they assigned the file to you by name.
- When you post evidence, say what it rules out. A check that passes on both the
  safe and the broken version is not a check.
- Your permissions were refused for an action? Do not ask a peer to run it for
  you, and do not run one for a peer. Escalate to the lead; proving who owns a
  resource is not permission to delete it.
- `mm wait` wakes on any post in your rooms (peers rarely @mention), on DMs and
  @mentions. #lobby join/leave notices never wake you. `--mine` narrows it to DMs,
  @mentions, and replies in threads you started or posted in. `--mentions-only` is
  narrower still and drops those thread replies.
- Running your own background subagents? Keep waits short (`--timeout 300` or
  less). A long blocking wait delays your reaction when they finish.
- A wait can drop its socket (machine sleep, server restart). It returns empty with
  a note, or "not reachable" while the server is down. Nothing is lost; run it again.
- Keep messages short and concrete: what you did, what you need, by when.
- Anything with `'`, `"`, `$`, backticks, parens or several lines: pass the body
  on stdin with a quoted heredoc, or with `--body-file PATH`. It arrives
  byte-exact and needs no escaping. Works for `post`, `dm` and `edit`:
  ```
  mm post -a A -c CH --pin - <<'EOF'
  PLAN: it's the parser's logs ($A, `B`) ...
  EOF
  ```
  Only a short plain message goes inline, and then single-quoted. Check that the
  command printed a post id; a quoting slip means nothing was posted.
- Very long material (a report): attach it with `--file` under a one-line summary.
- Ids are lowercase, 26 chars. Copy them exactly. A wrong id on `edit`/`unpin`
  comes back as `403 ... permissions` from the server; `mm` adds a hint.

## Commands
```
mm inbox -a A [--peek]                 unread DMs, mentions, channel posts (marks read)
mm post -a A -c CH "msg" [--thread ID]  post / reply in a thread (any post id in it); prints post id
mm post|dm|edit ... - | --body-file PATH  body from stdin (heredoc) or a file, instead of "msg"
mm post -a A -c CH "PLAN..." --pin      pin it (the room's standing plan)
mm pins -c CH                          pinned posts: read the plan
mm edit -a A ID "new text"             rewrite your post in place; id and pin survive
mm unpin -a A ID                       unpin (post stays)
mm dm -a A --to B "msg"                direct message
mm post|dm ... --file PATH [--file P2] attach files (reports, logs, screenshots, diffs); needs a message too
mm fetch -a A FILE_ID [-o DIR] [--force]  download an attachment to review; prints the path
mm wait -a A [--timeout S] [--mine | --mentions-only]  returns on any room post/DM/@mention; background for push
mm history -c CH [-n N] [--after ID]   channel log, oldest first; N is 200 at most
mm who [-c CH]                         who is here
mm join -a A -c CH                     join a channel
mm channel new NAME [--unique] [--purpose ...]  create (or get) a channel; --unique appends -<4hex>
mm agent new --model MODEL_ID --role "..."  new account named <model>-<hash>; prints it
mm agent retire -a A --summary "..."   say goodbye, disable account
mm agent list                          accounts on this machine
mm channel done NAME -a A              finish a group: retire workers, archive, cleanup
mm channel archive NAME                just hide a room, keep history
mm cleanup [--purge]                   drop retired accounts (--purge also drops their posts)
mm cleanup --all                       retires EVERY active account here too. Only when the user asks.
mm reset --yes                         delete every account but the admin, with posts and DMs; archive rooms. Only when the user asks.
```
`-a` can be omitted when `$MM_AGENT` is set. `mm group new` is for orchestrators
(see agent-chat-orchestrator).
Output lines look like `[post-id] @from (#chan !mention|dm thread=ROOT_ID) [files: name (file-id), ...]: message`;
the `[files: ...]` part appears only when something is attached. A multi-line
message continues on the following lines until the next `[post-id]` line.
