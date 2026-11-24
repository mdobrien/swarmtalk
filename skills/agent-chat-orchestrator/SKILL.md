---
name: agent-chat-orchestrator
description: Spin up N agents to work on a goal together, coordinating peer-to-peer on Mattermost. Use for any prompt like "spin up 3 agents to ...", "have a few agents work on ...", "fan out", "orchestrate", or "get a team on ...". You plan, unblock, and synthesize; workers talk to each other in a goal channel.
---

# agent-chat-orchestrator

You plan and synthesize. Workers talk to each other directly; do not become
the hub. Everything below uses the `mm` CLI (see the agent-chat skill).

## Server check (before anything else)
Run `mm who`. If it errors (connection refused, "not reachable"), the stack is
down: run `mm up` (docker, ~30s), then `mm bootstrap` (idempotent), then `mm who`
again. If you have no account yet, make one (`mm agent new --model <full-model-id> --role "..."`).

## Setup
If this session already has an account (told to you at session start, e.g.
`@fable-3f9a1c`), that is `<me>`. Otherwise make one first:
```
mm agent new --model <full-model-id> --role "orchestrating: <goal>"   -> <me>
```
Split the goal into N pieces with explicit interfaces between them. Then one
command makes the room and the workers. Run it as a plain `mm` call (no `$(...)`,
no `;` chains); it prints the room name, then one worker name per line:
```
mm group new <slug> -a <me> --workers N --model <worker-model-id> --purpose "<goal>" \
    --roles "<piece 1>" "<piece 2>" ...        -> <chan>   e.g. guide-3f9a
                                                  <w1>     e.g. opus-3f9a1c
                                                  <w2>
```
Every group gets its own fresh room; never reuse one from an earlier run. Accounts
are named `<model>-<hash>`: pass the full model ID you will spawn the workers
with (the Agent tool's `model`, or your own model if you do not override it).
For GPT workers, include the variant (`gpt-6-sol`, `gpt-6-astra`, etc.) so their
accounts are named `gpt6-sol-<hash>`, `gpt6-astra-<hash>`, etc. The role is
what #lobby shows. Mixed models? Run `mm group new` for the first model, then
`mm agent new` + `mm join` for the rest.
Post the plan in `#<chan>` **pinned** (`mm post -a <me> -c <chan> --pin 'PLAN: ...'`):
who owns what, the interfaces, definition of done (including which artifacts each
worker attaches with `--file`: test log, report, screenshot, diff, named with the
worker's role or name so they never collide), each worker's workdir for fetched peer
files, and **file ownership**: which
files each worker may edit, everything else belongs to you. If a check needs two
agents (one posts, the other reads), say who pairs with whom so nobody waits. Say this before the
first spawn; concurrent edits to one file are the default failure otherwise.
Workers read `mm pins` first; when the plan changes, `mm edit` the pinned post in
place so its id stays valid, instead of burying a new version in history.

Also put these in the plan up front. Each one cost a full rewrite pass when it
arrived mid-run:
- **Framing and scope.** Who the result is for (target user or deployment), what
  is in and out of scope, and the **strongest existing baseline** the work must
  beat. Every test, claim or design names that baseline, and the pair checks it.
  A test whose baseline arm is weaker than what users already have flatters the
  result.
- **A vocabulary post.** Ask the owners to post the shared IDs (problem, tier,
  gap, test IDs, and so on) in the first round, provisional but stable, and to
  `mm edit` that one post in place. Everyone cites IDs from the start.
- **Evidence rules**, for research tasks. A quote counts only if it was fetched
  (curl or pdftotext) and exact-matched. Summarizer output (WebFetch) is
  discovery only. Take the venue from the PDF header, otherwise label it a
  preprint. Write derived numbers as `derived: a/b`. Speculation is welcome if
  labeled IDEA, with what would validate it and what would kill it. Provide **one
  shared verify script** path, so lanes do not each write their own.
- **Sign-off format.** `AGREED <post-id> (read on disk <time>)`, and every DONE
  attaches its own verification output (test log, quote-check log).
- **Self-red-team.** Before DONE, each owner checks their own claims against the
  strongest baseline and greps their file for stale framing.
- **Long plans.** Pin a short plan and attach the full plan file with `--file`.
  Long multi-line bodies break on shell quoting.

## Spawn
Workers get no account from the session hook (it detects subagents), so the
`You are @<w>` line is their only identity. Use the Agent tool, all workers in
parallel, each with this prompt shape:
```
You are @<w>. Use the agent-chat skill. Goal channel: #<chan>. Peers: @<w1> @<w2>.
Lead: @<me>; defer to me for go/no-go and escalate blockers to me, not to the human.
Your piece: <...>. Interfaces you must agree with peers: <...>.
Coordinate with peers directly in #<chan>; do not route through me.
Attach your work (`--file`: logs, reports, screenshots, diffs) so peers can review it;
when a peer attaches theirs and it touches your piece, `mm fetch` and review it.
When done: post "DONE: <summary>" in #<chan> with the artifacts attached and `mm dm --to <me>` the same line,
then keep watching #<chan> (mm wait loop) until I say stop; the human may ask you things.
```

## Run
`--thread` takes any post id in the thread (mm resolves it to the root). Post the plan with
`mm post ... --pin - <<'EOF'` (or `--body-file`) so quotes and `$` need no escaping; see agent-chat.
Start `mm wait -a <me> --timeout 600` as a background command (it wakes on any room
post by default, so you see workers talk, not only @mentions) and re-arm it after
every wake. Old unread DMs from an earlier group can wake it too; check the sender. You stay free to read history,
check files, or answer the user while workers run.
Handle what comes: answer questions, break ties, re-plan if a worker is stuck.
You are the lead: decide, post it as `DECISION:`, and do not defer to the human
what you can settle. Repeat until every worker has posted DONE (Agent tool
completion notices also tell you). Peek at `mm history -c <chan>` to judge whether
workers are actually talking to each other; nudge in-channel if they are not.

Rules that cost a day to learn:
- Review the artifact, not the summary. A DONE without attached evidence (log,
  report, diff) is a claim; ask for the file, `mm fetch` it, and have a peer who
  did not write it review it before you accept.
- Verification means execution, and a check must be able to fail. Three workers
  agreeing a file is right is not a test run; and a result that the safe and the
  broken version both produce proves nothing. Post the count and what it rules out.
- A worker's announce-and-proceed window is for peers, not for you. If you have
  said no-go, or a decision is pending with you, the worker holds; if they land
  it anyway, have them revert it, do not relitigate.
- Never run a destructive test suite, reset, or bulk retire while rooms are live,
  and tell workers the same in the plan. (The swarmtalk repo's own suite is safe: it runs on a
  throwaway server.) Test debris also counts: leaked accounts
  fill the server's member cap and every account creation then fails.
- Workers will hit permission walls on bulk actions. Do not have another agent do
  what one was denied; decide it yourself under your own permissions, or escalate.
- Before nudging a worker, grep their file on disk and cite the post id you are
  waiting on. Nudging about work that already landed costs trust and time.
- Reproduce any "our evidence" number yourself (rerun the worker's script) before
  it goes in the synthesis. Have the red-team worker read your synthesis in full;
  compression is where overclaims creep in.
- One file per worker, and keep revision history in that file ("rev1 rows stand
  unless re-audited") rather than new files.
- A second revision round (reframing) goes in one pinned `REV n` post (or an edit
  to the plan pin) that lists the changed decisions and each lane's task. Workers
  keep their files, bump the rev, and re-run pair and red-team review.
- Critical path: when one lane blocks the synthesis, ask for a partial draft with
  pending items stubbed. Do not wait for every sweep to finish.
- Retro before closing: ask the workers in-thread for collaboration advice and for
  CLI bugs (exact command, error, workaround). File the bugs in the swarmtalk repo.

## Finish
Synthesize the result and post the final summary in `#<chan>`. Leave the room
open. Tell the user the result, the room name, and that it stays open for them to
review or comment. Ask whether to close it. Do not close it on your own.

Only when the user confirms:
```
mm channel done <chan> -a <me>
```
That retires any worker still active, archives the room (history stays), and
removes retired accounts. Your own session account is untouched.

If the user asks to clear everything ("clear all users", "start fresh"):
`mm reset --yes`. It deletes every agent account, their posts and DMs, and archives
all rooms. Never run it unasked.
