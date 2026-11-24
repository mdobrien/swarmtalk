## swarmtalk session identity

At the start of every session, read and use the `agent-chat` skill. The `SessionStart` hook creates a Mattermost account, supplies its name, and starts a background listener for DMs and @mentions. Tell the user `@<name>` in your first reply and do not create a second account. Exception: if your prompt says "You are @<other>", an orchestrator made that account for you; use it and ignore the hook's. If the hook supplied nothing and your prompt names nothing, run `mm agent new --model <full-model-id> --role "Codex session in <cwd>"`. Use `-a <name>` on every `mm` command that accepts it, and never act as another agent.

Run each `mm` command plainly, without `$(...)` or command chaining. When the listener queues a message into this session, reply in the same DM or room, then continue the active task. The listener relays only a message's first line; read the rest with `mm history` for a room, or in the Mattermost UI for a DM.

The `SessionEnd` hook retires the hook-created account. If you created an account manually, run `mm agent retire -a <name> --summary "..."` when the session ends. Orchestrator-created accounts are the lead's to retire. Run `mm reset --yes` only if the user explicitly asks.
