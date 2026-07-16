from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse
from common.utils.time import utcnow
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from llm_gateway.errors import LLMModelRoutingError, LLMServiceNotBoundError
from llm_gateway.gateway import LLMGatewayImpl
from llm_gateway.model_registry import ModelRegistryImpl
from llm_gateway.services import SupervisorLLMService
from models.supervisor import (
    ActionType,
    RoomConfig,
    StepResult,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
)


class FakeGatewayProvider:
    def __init__(self, structured_data=None) -> None:
        self.generate_calls = []
        self.structured_calls = []
        self.stream_calls = []
        self.structured_data = structured_data or {
            "action": "done",
            "reasoning": "provider response",
        }

    async def generate(self, messages, model: str, **kwargs):
        self.generate_calls.append({"messages": messages, "model": model, **kwargs})
        return LLMResponse(content="text", model=model)

    async def generate_structured(
        self,
        messages,
        model: str,
        schema=None,
        json_mode: bool = False,
        **kwargs,
    ):
        self.structured_calls.append(
            {
                "messages": messages,
                "model": model,
                "schema": schema,
                "json_mode": json_mode,
                **kwargs,
            }
        )
        return LLMStructuredResponse(data=self.structured_data, model=model)

    async def generate_stream(self, messages, model: str, **kwargs):
        self.stream_calls.append({"messages": messages, "model": model, **kwargs})
        yield "focused stream"

    async def embed(self, text: str, model: str):
        return [1.0]

    async def embed_batch(self, texts, model: str):
        return [[1.0] for _ in texts]


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


@pytest.mark.asyncio
async def test_focused_supervisor_routes_to_bedrock_provider_when_flag_enabled(
    monkeypatch,
):
    monkeypatch.setattr(settings, "use_bedrock_supervisor", True)
    monkeypatch.setattr(
        settings,
        "bedrock_supervisor_model",
        "anthropic.claude-opus-test",
    )
    openai_provider = FakeGatewayProvider()
    bedrock_provider = FakeGatewayProvider(
        structured_data={
            "action": "done",
            "reasoning": "Bedrock provider response",
        }
    )
    gateway = LLMGatewayImpl(
        model_registry=ModelRegistryImpl(),
        providers={
            "openai": openai_provider,
            "bedrock": bedrock_provider,
            "gemini": FakeGatewayProvider(),
        },
    )
    service = RoomSupervisorService(
        supervisor_service=SupervisorLLMService(gateway),
    )

    result = await service._call_supervisor_llm(
        system_prompt="Test system",
        user_prompt="Test user",
    )

    assert result["reasoning"] == "Bedrock provider response"
    assert bedrock_provider.structured_calls[0]["model"] == (
        "anthropic.claude-opus-test"
    )
    assert bedrock_provider.structured_calls[0]["json_mode"] is True
    assert openai_provider.structured_calls == []
