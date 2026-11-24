"""Create settings, bring the stack up/down, bootstrap admin, team and default channels."""
import os
import secrets
import subprocess
import time

from . import state
from .api import BASE_URL, Api, ApiError

TEAM = "agents"
DEFAULT_CHANNELS = ["lobby", "general"]
PROJECT = "swarmtalk"  # fixed, so renaming the clone never starts an empty server
SECRETS = ("MM_DB_PASSWORD", "MM_ADMIN_PASSWORD")


def init() -> None:
    """Create .env from .env.example with random passwords. Never touches an existing .env."""
    path = state.REPO / ".env"
    if path.exists():
        raise RuntimeError(f"{path} already exists; left unchanged")
    cfg = state.parse_env(state.REPO / ".env.example")
    cfg.update({k: secrets.token_urlsafe(24) for k in SECRETS})
    state.write_private(path, "".join(f"{k}={v}\n" for k, v in cfg.items()))


def require_secrets() -> None:
    """No default passwords: refuse to start a server anyone could log in to."""
    cfg = state.env()
    missing = [k for k in SECRETS if not cfg.get(k)]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} not set in {state.REPO / '.env'}; run `mm init` "
                           "(or fill them in if .env exists)")


def compose_cmd() -> list[str]:
    """`docker compose` for the real stack, or the one MM_COMPOSE_PROJECT/FILE name (tests use this)."""
    cmd = ["docker", "compose", "-p", os.environ.get("MM_COMPOSE_PROJECT") or PROJECT]
    if os.environ.get("MM_COMPOSE_FILE"):
        cmd += ["-f", os.environ["MM_COMPOSE_FILE"]]
    return cmd


def compose(*args: str) -> int:
    return subprocess.call([*compose_cmd(), *args], cwd=state.REPO)


def up() -> int:
    require_secrets()
    return compose("up", "-d")


def down() -> int:
    return compose("down")


def wait_ready(timeout_s: int = 120) -> bool:
    api = Api()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if api.ping():
            return True
        time.sleep(2)
    return False


def bootstrap() -> dict:
    """Idempotent: admin user + persistent token, team, default channels -> state/admin.json."""
    require_secrets()
    cfg = state.env()
    email, user, pw = cfg["MM_ADMIN_EMAIL"], cfg["MM_ADMIN_USER"], cfg["MM_ADMIN_PASSWORD"]
    anon = Api()
    if not anon.ping():
        raise RuntimeError(f"Mattermost not reachable at {BASE_URL} (run `mm up`)")

    # First user on a fresh server becomes system admin. 400/403 = already exists.
    try:
        anon.post("/users", {"email": email, "username": user, "password": pw})
        created = True
    except ApiError as e:
        if e.status not in (400, 403):
            raise
        created = False

    _, headers = anon.request("POST", "/users/login", {"login_id": email, "password": pw})
    session = Api(headers["Token"])
    me = session.get("/users/me")

    existing = state.load_admin()
    if existing and Api(existing["token"]).ping() and _token_valid(existing["token"]):
        token = existing["token"]
    else:
        token = session.post(f"/users/{me['id']}/tokens", {"description": "mm cli"})["token"]
    admin = Api(token)

    try:
        team = admin.get(f"/teams/name/{TEAM}")
    except ApiError as e:
        if e.status != 404:
            raise
        team = admin.post("/teams", {"name": TEAM, "display_name": "Agents", "type": "O"})

    channels = {}
    for name in DEFAULT_CHANNELS:
        try:
            ch = admin.get(f"/teams/{team['id']}/channels/name/{name}")
        except ApiError as e:
            if e.status != 404:
                raise
            ch = admin.post("/channels", {"team_id": team["id"], "name": name,
                                          "display_name": name, "type": "O"})
        channels[name] = ch["id"]

    data = {"user_id": me["id"], "token": token, "team_id": team["id"], "channels": channels,
            "admin_created": created}
    state.save_admin(data)
    return data


def _token_valid(token: str) -> bool:
    try:
        Api(token).get("/users/me")
        return True
    except ApiError:
        return False
