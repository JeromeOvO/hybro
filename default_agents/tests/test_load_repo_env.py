from __future__ import annotations

import os
from pathlib import Path

from load_repo_env import load_repo_env


def test_load_repo_env_reads_monorepo_root(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-root\n")
    agent_dir = tmp_path / "default_agents" / "demo_agent"
    agent_dir.mkdir(parents=True)
    marker = agent_dir / "agent.py"
    marker.write_text("# placeholder\n")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    load_repo_env(start=marker)
    assert os.environ.get("OPENAI_API_KEY") == "from-root"
