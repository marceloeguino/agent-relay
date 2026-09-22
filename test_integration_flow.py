"""Integration test for the acceptance scenario in SPEC.md:

    "Register two agents. One sends a task; the other claims and completes
    it; the sender reads the result."

Unlike test_agent_relay.py (which uses FastAPI's in-process TestClient),
this test starts the *real* server as a subprocess listening on a real
TCP port, backed by a real (scratch) SQLite database file, and talks to it
over real HTTP with httpx -- exercising the same code path a real agent
process or the dashboard would use.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

HOST = "127.0.0.1"
PORT = int(os.environ.get("RELAY_INTEGRATION_PORT", "8321"))
BASE_URL = f"http://{HOST}:{PORT}"
API = f"{BASE_URL}/api/v1"
REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Launch the real `main:app` via uvicorn as a subprocess against a
    scratch database, wait for it to become ready, and tear it down after."""
    db_path = tmp_path_factory.mktemp("relay-integration") / "integration.db"
    env = {
        **os.environ,
        "RELAY_DATABASE_URL": os.environ.get(
            "RELAY_DATABASE_URL_OVERRIDE", f"sqlite:///{db_path}"
        ),
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.time() + 20
        last_error = None
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{BASE_URL}/ready", timeout=1)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                raise RuntimeError(f"server process exited early:\n{out}")
            time.sleep(0.25)
        else:
            raise RuntimeError(f"server never became ready: {last_error}")

        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _register(client: httpx.Client, name: str) -> tuple[str, str]:
    resp = client.post(f"{API}/agents", json={"name": name})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["agent_id"], data["token"]


def test_two_agents_exchange_task_and_result_over_real_http(live_server):
    with httpx.Client(timeout=10) as client:
        sender_id, sender_token = _register(client, "alice")
        recipient_id, recipient_token = _register(client, "uppercase")

        sender_headers = {"Authorization": f"Bearer {sender_token}"}
        recipient_headers = {"Authorization": f"Bearer {recipient_token}"}

        # Sender sends a task to the recipient.
        sent = client.post(
            f"{API}/tasks",
            headers=sender_headers,
            json={"to": recipient_id, "input": "hello relay"},
        )
        assert sent.status_code == 201, sent.text
        task_id = sent.json()["task_id"]
        assert sent.json()["status"] == "queued"

        # Recipient claims the task.
        claimed = client.post(
            f"{API}/tasks/claim",
            headers=recipient_headers,
            json={"worker_id": "integration-test-worker", "wait_seconds": 5},
        )
        assert claimed.status_code == 200, claimed.text
        claim = claimed.json()
        assert claim["task_id"] == task_id
        assert claim["input"] == "hello relay"
        claim_token = claim["claim_token"]

        # While claimed, the sender should see it as "processing".
        mid_flight = client.get(f"{API}/tasks/{task_id}", headers=sender_headers)
        assert mid_flight.status_code == 200
        assert mid_flight.json()["status"] == "processing"

        # Recipient completes the task.
        completed = client.post(
            f"{API}/tasks/{task_id}/complete",
            headers=recipient_headers,
            json={"claim_token": claim_token, "output": "HELLO RELAY"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"

        # Sender reads the final result -- this is the status/output the
        # SENDER sees once the recipient has submitted its result.
        result = client.get(f"{API}/tasks/{task_id}", headers=sender_headers)
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "completed"
        assert body["output"] == "HELLO RELAY"
        assert body["error"] is None

        # A different agent must not be able to read this task.
        outsider_id, outsider_token = _register(client, "outsider")
        forbidden = client.get(
            f"{API}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert forbidden.status_code == 404
