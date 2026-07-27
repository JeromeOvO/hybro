"""One-shot registrar for Hybro default agents.

Reads agents.yaml, waits for the backend and each agent to become ready, then
registers every enabled agent with the backend. Idempotent: re-running treats
"already registered" as success.

Environment:
  BACKEND_URL   Base URL of the backend (default: http://backend:8000)
  API_PREFIX    API prefix (default: /api/v1)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
import yaml

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")
MANIFEST_PATH = Path(os.getenv("AGENTS_MANIFEST", "/app/agents.yaml"))

# Readiness polling
BACKEND_TIMEOUT_S = int(os.getenv("BACKEND_TIMEOUT_S", "180"))
AGENT_TIMEOUT_S = int(os.getenv("AGENT_TIMEOUT_S", "180"))
POLL_INTERVAL_S = float(os.getenv("POLL_INTERVAL_S", "2"))

# Candidate agent-card paths (well-known differs across A2A SDK versions).
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")


def _log(msg: str) -> None:
    print(f"[registrar] {msg}", flush=True)


def load_agents() -> dict[str, dict]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    agents = data.get("agents", {}) or {}
    return {
        name: spec
        for name, spec in agents.items()
        if spec.get("enabled", True)
    }


def wait_for_backend() -> bool:
    url = f"{BACKEND_URL}/health"
    deadline = time.time() + BACKEND_TIMEOUT_S
    _log(f"waiting for backend at {url} ...")
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                _log("backend is ready")
                return True
        except requests.RequestException:
            pass
        time.sleep(POLL_INTERVAL_S)
    _log(f"backend not ready after {BACKEND_TIMEOUT_S}s")
    return False


def wait_for_agent(agent_url: str) -> bool:
    deadline = time.time() + AGENT_TIMEOUT_S
    _log(f"waiting for agent at {agent_url} ...")
    while time.time() < deadline:
        for path in CARD_PATHS:
            try:
                resp = requests.get(f"{agent_url}{path}", timeout=5)
                if resp.status_code == 200:
                    _log(f"agent ready at {agent_url}{path}")
                    return True
            except requests.RequestException:
                pass
        time.sleep(POLL_INTERVAL_S)
    _log(f"agent {agent_url} not ready after {AGENT_TIMEOUT_S}s")
    return False


def register(agent_url: str) -> bool:
    endpoint = f"{BACKEND_URL}{API_PREFIX}/agent/registerAgent"
    try:
        resp = requests.post(endpoint, json={"agent_url": agent_url}, timeout=30)
    except requests.RequestException as exc:
        _log(f"registration request failed for {agent_url}: {exc}")
        return False

    if resp.status_code in (200, 201):
        _log(f"registered {agent_url}")
        return True

    body = resp.text.lower()
    if resp.status_code == 400 and ("already" in body or "exist" in body or "duplicate" in body):
        _log(f"already registered {agent_url} (ok)")
        return True

    _log(f"failed to register {agent_url}: HTTP {resp.status_code} - {resp.text[:300]}")
    return False


def main() -> int:
    agents = load_agents()
    if not agents:
        _log("no enabled agents in manifest; nothing to do")
        return 0

    if not wait_for_backend():
        return 1

    ok = True
    for name, spec in agents.items():
        service = spec.get("service", name)
        port = spec.get("port")
        if port is None:
            _log(f"skipping {name}: no port in manifest")
            ok = False
            continue
        agent_url = f"http://{service}:{port}"

        if not wait_for_agent(agent_url):
            ok = False
            continue
        if not register(agent_url):
            ok = False

    if ok:
        _log("all agents registered")
        return 0
    _log("one or more agents failed to register")
    return 1


if __name__ == "__main__":
    sys.exit(main())
