from unittest.mock import AsyncMock, MagicMock

import pytest

from common.utils.time import utcnow
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from llm_gateway.errors import LLMModelRoutingError, LLMServiceNotBoundError
from models.supervisor import (
    ActionType,
    RoomConfig,
    StepResult,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
)


@pytest.mark.asyncio
async def test_supervisor_service_fails_fast_when_llm_service_unbound():
    service = RoomSupervisorService()

    with pytest.raises(LLMServiceNotBoundError):
        await service._call_supervisor_llm("system", "user")

    with pytest.raises(LLMServiceNotBoundError):
        await service._call_supervisor_llm_text("system", "user")

    with pytest.raises(LLMServiceNotBoundError):
        async for _token in service.synthesize_stream(SupervisorTrajectory(), ""):
            pass

    with pytest.raises(LLMServiceNotBoundError):
        await service.decide_next(
            message_text="help",
            agent_registry=[],
            room_config=RoomConfig(),
            trajectory=SupervisorTrajectory(),
        )


@pytest.mark.asyncio
async def test_supervisor_decide_next_propagates_routing_errors():
    service = RoomSupervisorService()
    service._call_supervisor_llm = AsyncMock(
        side_effect=LLMModelRoutingError("unregistered model")
    )

    with pytest.raises(LLMModelRoutingError):
        await service.decide_next(
            message_text="help",
            agent_registry=[],
            room_config=RoomConfig(),
            trajectory=SupervisorTrajectory(),
        )


@pytest.mark.asyncio
async def test_supervisor_synthesize_stream_propagates_routing_errors():
    async def error_stream():
        raise LLMModelRoutingError("unregistered model")
        yield "unreachable"

    service = RoomSupervisorService()
    service._supervisor_llm_text_stream = MagicMock(return_value=error_stream())
    trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="test",
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-a",
                        agent_name="Agent A",
                        task="answer",
                        response_text="result",
                    )
                ],
                started_at=utcnow(),
            )
        ]
    )

    with pytest.raises(LLMModelRoutingError):
        async for _token in service.synthesize_stream(trajectory, ""):
            pass


@pytest.mark.asyncio
async def test_supervisor_service_delegates_json_to_focused_service():
    supervisor = AsyncMock()
    supervisor.call_json = AsyncMock(
        return_value={"action": "done", "reasoning": "Focused response"}
    )
    service = RoomSupervisorService(
        supervisor_service=supervisor,
    )

    result = await service._call_supervisor_llm("system", "user")

    assert result["reasoning"] == "Focused response"
    supervisor.call_json.assert_awaited_once_with(
        system_prompt="system",
        user_prompt="user",
    )


@pytest.mark.asyncio
async def test_supervisor_planner_json_forwards_schema_to_focused_service():
    supervisor = AsyncMock()
    supervisor.call_json = AsyncMock(return_value={"action": "fail"})
    service = RoomSupervisorService(supervisor_service=supervisor)
    schema = {"type": "object"}

    await service.call_planner_json(
        system_prompt="system",
        user_prompt="user",
        schema=schema,
    )

    supervisor.call_json.assert_awaited_once_with(
        system_prompt="system",
        user_prompt="user",
        schema=schema,
    )


@pytest.mark.asyncio
async def test_supervisor_service_delegates_text_stream_to_focused_service():
    async def stream(system_prompt: str, user_prompt: str):
        yield f"{system_prompt}:{user_prompt}"

    supervisor = AsyncMock()
    supervisor.call_text_stream = MagicMock(side_effect=stream)
    service = RoomSupervisorService(
        supervisor_service=supervisor,
    )

    result = await service._call_supervisor_llm_text("system", "user")

    assert result == "system:user"
    supervisor.call_text_stream.assert_called_once_with(
        system_prompt="system",
        user_prompt="user",
    )
