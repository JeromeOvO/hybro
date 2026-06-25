import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_inspection_runtime_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("agent/inspection.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_inspection_runtime_shim_accepts_agent_service_dependency():
    from app_shell.inspection_runtime import AppShellInspectionCenter

    agent_service = MagicMock()
    agent_service.validate_agent_card = AsyncMock(return_value=[])

    center = AppShellInspectionCenter(agent_service_dep=agent_service)

    assert center.agent_service is agent_service


def test_inspection_runtime_shim_uses_app_shell_singleton_by_default():
    from app_shell.agent_service import agent_service
    from app_shell.inspection_runtime import AppShellInspectionCenter

    center = AppShellInspectionCenter()

    assert center.agent_service is agent_service


@pytest.mark.asyncio
async def test_inspection_runtime_shim_allows_partial_service_for_a2a_only(
    monkeypatch,
):
    from app_shell.inspection_runtime import AppShellInspectionCenter
    from models.request import InspectionCenterRequest

    inspect_a2a_connection = AsyncMock(
        return_value={"result": ["connected"], "status_code": 200}
    )
    monkeypatch.setattr(
        "agent.inspection.a2a_inspection.inspect_a2a_connection",
        inspect_a2a_connection,
    )

    center = AppShellInspectionCenter(agent_service_dep=object())

    response = await center.inspect_a2a_connection(
        InspectionCenterRequest(agent_url="https://agent.example")
    )

    inspect_a2a_connection.assert_awaited_once_with("https://agent.example")
    assert response.result == ["connected"]


@pytest.mark.asyncio
async def test_inspection_runtime_shim_uses_injected_agent_service_validator(
    monkeypatch, sample_agent_card
):
    from app_shell.inspection_runtime import AppShellInspectionCenter
    from models.request import InspectionCenterRequest

    fake_service = MagicMock()
    fake_service.validate_agent_card = AsyncMock(return_value=["from fake"])
    fetch_agent_card = AsyncMock(return_value=sample_agent_card)
    monkeypatch.setattr(
        "agent.inspection.a2a_inspection.fetch_agent_card_for_inspection",
        fetch_agent_card,
    )

    center = AppShellInspectionCenter(agent_service_dep=fake_service)

    response = await center.inspect_agent_card(
        InspectionCenterRequest(agent_url="https://agent.example")
    )

    fake_service.validate_agent_card.assert_awaited_once_with(
        sample_agent_card.model_dump(exclude_none=False)
    )
    assert response.result == ["from fake"]
