"""Guard: docker-compose.yml must stay in sync with agents.yaml.

The default-agent service blocks + registrar in docker-compose.yml are generated
from default_agents/agents.yaml by render_compose.py. This test fails if the two
have diverged, so a manifest edit that wasn't regenerated is caught in CI.

    Fix: python default_agents/render_compose.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

DEFAULT_AGENTS_DIR = Path(__file__).resolve().parents[1]
RENDER_SCRIPT = DEFAULT_AGENTS_DIR / "render_compose.py"

_spec = importlib.util.spec_from_file_location("render_compose", RENDER_SCRIPT)
render_compose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_compose)


def test_compose_matches_manifest() -> None:
    agents = render_compose.load_enabled_agents()
    region = render_compose.render_region(agents)
    current = render_compose.COMPOSE_PATH.read_text(encoding="utf-8")
    expected = render_compose.build_expected(current, region)
    assert current == expected, (
        "docker-compose.yml is out of sync with default_agents/agents.yaml. "
        "Run: python default_agents/render_compose.py"
    )
