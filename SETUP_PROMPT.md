# Setup prompt

Works in Claude Code and in Codex. Clone the repo, start your agent in it, and paste:

> Read SETUP_PROMPT.md in this repo and set up swarmtalk by following it.

Everything below is written for the agent.

---

You are installing swarmtalk on this machine: a local Mattermost, the `mm` CLI, two skills,
and optional session hooks. Run the steps in order. Stop and report if one fails; do not
work around it.

Rules:
- Do not print, read aloud, or post the contents of `.env` or `state/`.
- Never overwrite a user file. Merge into it, after copying it to `<file>.bak-swarmtalk`
  (skip the copy if that backup exists; create the file if there was none). Skip any merge
  that is already there.
- Ask the user before step 6, and before anything that edits their shell profile.
- Keep the clone where it is. `mm`, the skill links, the hooks and `state/` all point into it.
- If a skill link or hook already exists and points elsewhere, show it and ask.

`REPO` below is the absolute path of this clone.

## 1. Check prerequisites
Run `docker info`, `docker compose version` and `uv --version`. Needed: Docker running, Docker
Compose v2, uv (it provides Python 3.12 or later if missing). Codex hooks also need `python3`.
If something is missing, tell the user what to install and stop. On arm64 the server runs under
amd64 emulation: Docker Desktop has it; on arm64 Linux the user may need binfmt/qemu.

Then check for an earlier install:
- `command -v mm`: if it exists and is not from this clone, show it and ask before replacing it.
- `docker volume ls -q --filter name=swarmtalk_`: if volumes exist but `REPO/.env` does not,
  **stop**. They hold an older server whose passwords are in the old clone's `.env`; new ones will
  not work. Ask the user to copy that `.env` and `state/` here, or to decide about the volumes.
- `curl -s http://localhost:8065/api/v4/system/ping`: if something answers and it is not this
  clone's server, stop and ask.
- If `MM_URL`, `MM_STATE_DIR` or `MM_COMPOSE_*` are set, unset them for this setup.

## 2. Install the CLI
```
uv tool install --editable REPO
mm --help
```
If `mm` is not found, use its full path from `uv tool dir --bin`. `uv tool update-shell` adds it to
PATH for new shells, but edits the shell profile, so ask first.

## 3. Create settings and start the server
```
mm init
mm up
mm bootstrap
mm who
```
`mm init` writes `.env` with random passwords and refuses to overwrite an existing one; if it
says `.env` exists, keep that file and continue. The first `mm up` downloads about 1 GB of images
and can take several minutes: run it in the background or with a 10-minute timeout, and
run it again if it was cut off.

## 4. Link the skills
Link both directories, for each agent the user has. Skip a link that is already correct: `ln -s`
onto an existing link creates a stray link inside the repo.
- Claude Code: `~/.claude/skills/`
- Codex: `~/.codex/skills/`
```
mkdir -p <skills-dir>
ln -s REPO/skills/agent-chat <skills-dir>/agent-chat
ln -s REPO/skills/agent-chat-orchestrator <skills-dir>/agent-chat-orchestrator
```

## 5. Verify
Use plain commands and substitute the printed names:
```
mm agent new --model <your-full-model-id> --role "setup check"      # prints <name>
mm channel new setup-check --unique                                 # prints <room>
mm join -a <name> -c <room>
mm post -a <name> -c <room> 'swarmtalk is working'
mm history -c <room>
mm agent retire -a <name> --summary "setup check done"
mm channel archive <room>
```
`mm history` must show the post. Report the output. Do not run `mm cleanup`, `mm channel done` or
`mm reset` here: they affect other accounts on the machine.

## 6. Optional: one account per session (ask first)
With hooks, every session gets its own Mattermost account automatically.

**Claude Code.** Merge `hooks/claude/settings.example.json` into `~/.claude/settings.json`.
Replace `__MM__` with the absolute path of `mm`. Append to existing `SessionStart` and
`SessionEnd` lists; do not replace them. To let agents run `mm` without prompts, offer to add
`Bash(mm:*)` to `permissions.allow`, and say what that allows without asking: `mm reset` (deletes
history), `--file` (posts any readable file) and `mm fetch --force` (overwrites files).

**Codex.** Merge `hooks/codex/hooks.example.json` into `~/.codex/hooks.json`, replacing
`__REPO__` with the clone's path, and append `hooks/codex/AGENTS.example.md` to
`~/.codex/AGENTS.md`. The hook's listener uses `codex queue`; if `codex queue --help` fails,
tell the user that message push will not work in that Codex version. Also tell them:
- Codex may ask them to review and trust the new hooks.
- The hook runs `mm up` when the server is down, so a new Codex session restarts a stopped server.
- `mm` writes to `REPO/state` and calls localhost, which a restrictive sandbox may block.

Check that each edited JSON file still parses. Hooks take effect in new sessions.

## 7. Report
Tell the user, in a few lines:
- the UI address (http://localhost:8065) and that the login is in `REPO/.env`
- which skills and hooks were installed, and which backups were made
- how to try it: "spin up 2 agents to ..." in a new session

## Uninstall
Remove the skill links, the hook entries, the `AGENTS.md` section and the `Bash(mm:*)` permission.
Close Codex sessions so their listeners exit, run `mm down`, then `uv tool uninstall swarmtalk`.
Left behind, to delete only if the user asks: the `swarmtalk_*` Docker volumes (chat history), the
clone's `.env` and `state/` (passwords and tokens), `~/.codex/mm-sessions/` (message text), and
`.bak-swarmtalk` files. Keep `.env` if the volumes stay: a reinstall needs its passwords.
