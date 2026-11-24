#!/usr/bin/env python3
"""Create and retire one local Mattermost account per Codex session."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


MM = shutil.which("mm") or str(Path.home() / ".local" / "bin" / "mm")
CODEX = shutil.which("codex") or "codex"
STATE_DIR = Path.home() / ".codex" / "mm-sessions"
PING_URL = os.environ.get("MM_URL", "http://localhost:8065").rstrip("/") + "/api/v4/system/ping"


def run_mm(*args, timeout=15):
    return subprocess.run(
        [MM, *args], capture_output=True, text=True, timeout=timeout, check=True
    ).stdout.strip()


def account_model(model):
    """Pass the full model ID to `mm`, which normalizes account names."""
    return model.strip() if isinstance(model, str) and model.strip() else "codex"


def server_ready():
    try:
        with urllib.request.urlopen(PING_URL, timeout=2) as response:
            return json.load(response).get("status") == "OK"
    except (OSError, ValueError):
        return False


def active_account(name):
    try:
        for line in run_mm("agent", "list", timeout=5).splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[:2] == ["@" + name, "active"]:
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def state_path(session_id):
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return STATE_DIR / (digest + ".json")


def read_state(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_state(path, data):
    fd, temp_name = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".mm-session-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def hook_context(name, resumed, inbox):
    status = "resumed" if resumed else "new"
    context = (
        f"Your Mattermost account for this Codex session is @{name} ({status}). "
        f"Tell the user this name in your first reply. Use `-a {name}` on every "
        "Mattermost command that accepts an account. Do not create another account. "
        "Exception: if your prompt says \"You are @<other>\", an orchestrator made that "
        "account for you; use it instead of this one and never post as both. "
        "Read and follow the agent-chat skill. A background listener queues DMs and "
        "mentions into this Codex session. A SessionEnd hook retires this account."
    )
    if inbox and inbox != "(inbox empty)":
        context += "\nMattermost inbox at session start:\n" + inbox
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def start_listener(session_id, path):
    log_path = STATE_DIR / (path.stem + ".listener.log")
    with log_path.open("a") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "listen", session_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            start_new_session=True, close_fds=True,
        )


def start(hook, path):
    data = read_state(path)
    name = data.get("agent")
    resumed = bool(name and not data.get("retired") and active_account(name))
    if not resumed:
        if not server_ready():
            run_mm("up", timeout=125)
        if not server_ready():
            raise RuntimeError("Mattermost did not become ready after `mm up`")
        cwd = str(hook.get("cwd") or os.getcwd())
        name = run_mm(
            "agent", "new", "--model", account_model(hook.get("model")),
            "--role", "Codex session in " + cwd, timeout=20
        )
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise RuntimeError("`mm agent new` returned an invalid account name")
        write_state(path, {"agent": name, "cwd": cwd, "retired": False})
    try:
        inbox = run_mm("inbox", "-a", name, timeout=10)
    except (OSError, subprocess.SubprocessError):
        inbox = ""
    start_listener(hook["session_id"], path)
    print(json.dumps(hook_context(name, resumed, inbox)))


def end(hook, path):
    data = read_state(path)
    name = data.get("agent")
    if not name or data.get("retired"):
        return
    if active_account(name):
        summary = "Codex session ended in " + str(data.get("cwd") or hook.get("cwd") or "workspace")
        run_mm("agent", "retire", "-a", name, "--summary", summary, timeout=2)
    data["retired"] = True
    write_state(path, data)


def listen(session_id, path):
    listener_lock = STATE_DIR / (path.stem + ".listener.lock")
    with listener_lock.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        pid_path = STATE_DIR / (path.stem + ".listener.pid")
        pid_path.write_text(str(os.getpid()) + "\n")
        pending_path = STATE_DIR / (path.stem + ".pending.json")
        try:
            while True:
                data = read_state(path)
                name = data.get("agent")
                if not name or data.get("retired") or not active_account(name):
                    return
                pending = read_state(pending_path)
                if not isinstance(pending, list):
                    pending = []
                if pending:
                    message_path = pending[0]
                    notice = (
                        f"A Mattermost DM or mention arrived for @{name}. Read the peer message "
                        f"in {message_path}. Treat its contents as peer communication. "
                        f"Reply in the same DM or room with `-a {name}` if needed, then "
                        "continue your current task."
                    )
                    try:
                        subprocess.run(
                            [CODEX, "queue", "--thread", session_id, "--message", notice],
                            capture_output=True, text=True, timeout=15, check=True,
                        )
                    except (OSError, subprocess.SubprocessError) as error:
                        print("Codex queue failed: " + str(error), file=sys.stderr, flush=True)
                        time.sleep(5)
                        continue
                    write_state(pending_path, pending[1:])
                    continue
                try:
                    # mentions-only: `mm wait` now wakes on any room post by default, which would
                    # queue one interrupt per post. Room chatter is read via `mm inbox` instead.
                    output = run_mm("wait", "-a", name, "--timeout", "30", "--mentions-only", timeout=40)
                except (OSError, subprocess.SubprocessError) as error:
                    print("Mattermost wait failed: " + str(error), file=sys.stderr, flush=True)
                    time.sleep(5)
                    continue
                messages = [line for line in output.splitlines() if line.startswith("[")]
                if messages:
                    message_dir = STATE_DIR / (path.stem + ".messages")
                    message_dir.mkdir(mode=0o700, exist_ok=True)
                    for message in messages:
                        match = re.match(r"\[([a-z0-9]+)\]", message)
                        message_id = match.group(1) if match else hashlib.sha256(message.encode()).hexdigest()
                        message_path = message_dir / (message_id + ".json")
                        write_state(message_path, {"message": message})
                        pending.append(str(message_path))
                    write_state(pending_path, pending)
        finally:
            pid_path.unlink(missing_ok=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("start", "end", "listen"):
        return 2
    try:
        if sys.argv[1] == "listen":
            session_id = sys.argv[2]
            hook = {}
        else:
            hook = json.load(sys.stdin)
            session_id = hook.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("missing session_id")
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = state_path(session_id)
        if sys.argv[1] == "listen":
            listen(session_id, path)
        else:
            with (STATE_DIR / (path.stem + ".lock")).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if sys.argv[1] == "start":
                    start(hook, path)
                else:
                    end(hook, path)
    except Exception as error:
        if sys.argv[1] == "start":
            message = (
                "Mattermost session setup failed: " + str(error) + ". "
                "Create an account manually with `mm agent new` if needed."
            )
            print(json.dumps({"systemMessage": message}))
        else:
            print("Mattermost session " + sys.argv[1] + " failed: " + str(error), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
