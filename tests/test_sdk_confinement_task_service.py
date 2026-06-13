import ast
from pathlib import Path

import pytest

from app_shell import task_service as task_service_module
from app_shell.task_service import TaskService
from common.types import AgentCard


def test_task_service_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("app_shell/task_service.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


@pytest.mark.asyncio
async def test_task_service_passes_agent_card_to_adapter_for_normalization(monkeypatch):
    calls = []

    async def _fetch_remote_task(agent_card, task_id):
        calls.append((agent_card, task_id))
        return None

    monkeypatch.setattr(task_service_module, "fetch_remote_task", _fetch_remote_task)
    card = AgentCard(
        name="Agent",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[{"id": "s", "name": "Skill"}],
    )

    result = await TaskService().get_task_from_agent(card, "task-1")

    assert result is None
    assert calls == [(card, "task-1")]
