"""The suite runs against its own throwaway Mattermost (docker-compose.test.yml, port 8066)
with its own state dir, so it can create, retire, purge and reset freely without touching
the real server on 8065 or the accounts of running sessions.

After a clean run the server is torn down and the temp state dir deleted. If anything
failed, both are kept for debugging and the run ends by printing where they are; the next
run starts from a fresh server. MM_TEST_KEEP=1 always keeps them (faster reruns).
Remove a kept server with `docker compose -p mm-test -f docker-compose.test.yml down -v`.
"""
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

# Must be set before anything imports mm: api.BASE_URL and state.STATE_DIR read them at import.
TEST_URL = "http://localhost:8066"
os.environ["MM_URL"] = TEST_URL
os.environ["MM_STATE_DIR"] = tempfile.mkdtemp(prefix="mm-test-state-")
os.environ["MM_COMPOSE_PROJECT"] = "mm-test"  # reset's SQL and infra.compose go here, not prod
os.environ["MM_COMPOSE_FILE"] = str(Path(__file__).resolve().parent.parent / "docker-compose.test.yml")

import pytest  # noqa: E402

from mm import api, infra  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COMPOSE = infra.compose_cmd()
assert api.BASE_URL == TEST_URL, "tests must never target the real server"
assert "mm-test" in COMPOSE, "tests must never run compose against the real stack"


@pytest.fixture(scope="session", autouse=True)
def test_server():
    if not os.environ.get("MM_TEST_KEEP"):  # a server kept after a failed run: start clean
        subprocess.call([*COMPOSE, "down", "-v"], cwd=REPO, stderr=subprocess.DEVNULL)
    if subprocess.call([*COMPOSE, "up", "-d"], cwd=REPO) != 0:
        pytest.exit("could not start the test Mattermost (docker running?)", returncode=1)
    if not infra.wait_ready(timeout_s=180):
        pytest.exit(f"test Mattermost did not come up at {TEST_URL}", returncode=1)
    infra.bootstrap()
    yield


def pytest_sessionfinish(session, exitstatus):
    """Clean up after a clean run; keep the server and state dir when something failed."""
    state_dir = os.environ["MM_STATE_DIR"]
    failed = exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)
    if failed or os.environ.get("MM_TEST_KEEP"):
        why = "tests failed" if failed else "MM_TEST_KEEP set"
        print(f"\n[mm-test] {why}: server and state kept for debugging\n"
              f"  server:    {TEST_URL}  (logs: {' '.join(COMPOSE)} logs mattermost)\n"
              f"  state dir: {state_dir}  (admin.json, agents/*.json)\n"
              f"  inspect:   MM_URL={TEST_URL} MM_STATE_DIR={state_dir} mm history -c <room>\n"
              f"  cleanup:   the next clean run removes the server; delete the state dir when done",
              file=sys.stderr)
        return
    # the state dir holds the test admin token; it is useless once the server is gone
    shutil.rmtree(state_dir, ignore_errors=True)
    subprocess.call([*COMPOSE, "down", "-v"], cwd=REPO)


@pytest.fixture(scope="module")
def room():
    """A fresh channel per test module so tests never touch #general. Archived after."""
    from mm import core
    ch = core.new_channel("t", unique=True)
    yield ch["name"]
    core.archive_channel(ch["name"])

