import ast
from pathlib import Path

import pytest

from a2a_adapter import remote_task_reader as remote_task_reader_module
from a2a_adapter.remote_task_reader import RemoteTaskReader
from common.types import AgentCard


def test_remote_task_reader_has_no_direct_a2a_sdk_imports():
    tree = ast.parse(Path("a2a_adapter/remote_task_reader.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: imports a2a SDK directly")


@pytest.mark.asyncio
async def test_remote_task_reader_passes_agent_card_to_adapter_for_normalization(
    monkeypatch,
):
    calls = []

    async def _fetch_remote_task(agent_card, task_id):
        calls.append((agent_card, task_id))
        return None

    monkeypatch.setattr(
        remote_task_reader_module, "fetch_remote_task", _fetch_remote_task
    )
    card = AgentCard(
        name="Agent",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[{"id": "s", "name": "Skill"}],
    )

    result = await RemoteTaskReader().get_task_from_agent(card, "task-1")

    assert result is None
    assert calls == [(card, "task-1")]


@pytest.mark.asyncio
async def test_remote_task_reader_accepts_execution_agent_id_keyword(monkeypatch):
    calls = []

    async def _fetch_remote_task(agent_card, task_id):
        calls.append((agent_card, task_id))
        return None

    monkeypatch.setattr(
        remote_task_reader_module, "fetch_remote_task", _fetch_remote_task
    )
    card = AgentCard(
        name="Agent",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[],
    )

    result = await RemoteTaskReader().get_task_from_agent(
        card,
        "task-2",
        agent_id="agent-1",
    )

    assert result is None
    assert calls == [(card, "task-2")]
