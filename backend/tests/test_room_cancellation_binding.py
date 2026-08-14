from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from room.compat.runtime import RoomServices

_DEAD_DEPENDENCY_ATTRIBUTES = {
    "agent_service",
    "agent_selection_service",
    "a2a_service",
    "delivery",
    "remote_task_reader",
}
_OLD_SENTINELS = {
    "UNBOUND_A2A_SERVICE",
    "UNBOUND_AGENT_SELECTION_SERVICE",
    "UNBOUND_AGENT_SERVICE",
    "UNBOUND_DELIVERY",
    "UNBOUND_TASK_SERVICE",
}


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def test_room_services_exposes_only_narrow_cancellation_binding():
    service = RoomServices()
    signature = inspect.signature(RoomServices.bind_cancellation_control)

    assert not any(hasattr(service, name) for name in _DEAD_DEPENDENCY_ATTRIBUTES)
    assert "bind_legacy_dependencies" not in RoomServices.__dict__
    assert list(signature.parameters) == ["self", "cancellation_control"]
    assert (
        signature.parameters["cancellation_control"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )

    control = object()
    service.bind_cancellation_control(cancellation_control=control)
    assert service.cancellation_control is control

    with pytest.raises(RuntimeError, match="cancellation_control is required"):
        service.bind_cancellation_control(cancellation_control=None)


def test_unbound_cancellation_control_is_semantically_named_and_old_sentinels_are_gone():
    import room.compat.unbound as unbound

    service = RoomServices()
    with pytest.raises(
        RuntimeError,
        match="cancellation control dependency has not been bound",
    ):
        service.cancellation_control.create_token("message-1")

    assert hasattr(unbound, "UNBOUND_CANCELLATION_CONTROL")
    assert not any(hasattr(unbound, name) for name in _OLD_SENTINELS)


def test_container_has_single_keyword_only_cancellation_wiring():
    tree = ast.parse(Path("container.py").read_text())
    narrow_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _dotted_name(node.func) == "room_runtime.bind_cancellation_control"
    ]
    legacy_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _dotted_name(node.func) == "room_runtime.bind_legacy_dependencies"
    ]

    assert len(narrow_calls) == 1
    assert legacy_calls == []
    call = narrow_calls[0]
    assert call.args == []
    assert [keyword.arg for keyword in call.keywords] == ["cancellation_control"]
    assert isinstance(call.keywords[0].value, ast.Name)
    assert call.keywords[0].value.id == "_cancellation_runtime"


def test_room_runtime_token_lifecycle_calls_cancellation_control_directly():
    source = Path("room/compat/runtime.py").read_text()
    tree = ast.parse(source)
    calls = {
        _dotted_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }

    assert "self.cancellation_control.create_token" in calls
    assert "self.cancellation_control.check_cancelled" in calls
    assert "self.cancellation_control.release_token" in calls
    assert "self._cancellation.create_token" not in calls
    assert "self._cancellation.check_cancelled" not in calls
    assert "self._cancellation.release_token" not in calls
    assert not any(name in source for name in _OLD_SENTINELS)


@pytest.mark.asyncio
async def test_preflight_release_behavior_is_unchanged():
    service = object.__new__(RoomServices)
    control = SimpleNamespace(
        create_token=MagicMock(),
        check_cancelled=AsyncMock(),
        release_token=MagicMock(),
    )
    service.bind_cancellation_control(cancellation_control=control)
    token = object()
    context = SimpleNamespace(
        user_message=SimpleNamespace(message_id="message-1"),
        token=token,
    )
    service._run_message_preflight_to_room = AsyncMock(
        return_value=SimpleNamespace(preflight_outcome="completed")
    )

    await service.run_message_preflight_to_room(context)

    control.release_token.assert_called_once_with("message-1", token)
