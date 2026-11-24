"""Local state: admin credentials and one JSON file per agent account.

Lives in <repo>/state/ (gitignored), readable only by you: every file holds a live token.
"""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("MM_STATE_DIR", REPO / "state"))
AGENTS_DIR = STATE_DIR / "agents"


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(path, json.dumps(data, indent=2) + "\n")


def write_private(path: Path, text: str) -> None:
    """Write a file only the owner can read, tightening one an older version left open."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def load_admin() -> dict | None:
    return _read(STATE_DIR / "admin.json")


def save_admin(data: dict) -> None:
    _write(STATE_DIR / "admin.json", data)


def load_agent(name: str) -> dict | None:
    return _read(AGENTS_DIR / f"{name}.json")


def save_agent(name: str, data: dict) -> None:
    _write(AGENTS_DIR / f"{name}.json", data)


def list_agents() -> list[str]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.stem for p in AGENTS_DIR.glob("*.json"))


def env() -> dict:
    """Settings from <repo>/.env."""
    return parse_env(REPO / ".env")


def parse_env(path: Path) -> dict:
    """Parse KEY=VALUE lines; a missing file is empty."""
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out
