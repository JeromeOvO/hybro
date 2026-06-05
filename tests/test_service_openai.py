from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.openai_service import OpenAIService, _GatewayOpenAIResponses
from common.dto import (
    AgentRoutingCandidate,
    ChatContextGenerationInput,
    LLMResponse,
    LLMStructuredResponse,
    ParsedUserMessageRequest,
    RoomMemoryGenerationInput,
    RoomMessageSummary,
)
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import LLMModelRoutingError, LLMServiceNotBoundError

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def openai_svc():
    svc = object.__new__(OpenAIService)
    svc.client = MagicMock()
    svc.client.chat.completions.create = AsyncMock()
    svc.client.responses.create = AsyncMock()
    return svc


def _chat_completion(content: str):
    """Build mock ChatCompletion for chat.completions.create."""
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


async def _stream_text(text: str):
    yield text


def _make_agent(agent_id: str, name: str, description: str = "A test agent",
                skills=None, capabilities=None):
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.agent_card.name = name
    agent.agent_card.description = description
    agent.agent_card.skills = skills or []
    agent.agent_card.capabilities = capabilities or {}
    return agent


@pytest.mark.asyncio
async def test_unbound_openai_service_raises_clear_binding_error():
    svc = OpenAIService()

    with pytest.raises(LLMServiceNotBoundError):
        await svc.get_embedding("hello")


# ---------------------------------------------------------------------------
# Group 1: expand_query_for_discovery
# ---------------------------------------------------------------------------

class TestExpandQueryForDiscovery:

    @pytest.mark.asyncio
    async def test_delegates_to_focused_discovery_service(self, openai_svc):
        discovery = AsyncMock()
        discovery.expand_query_for_discovery = AsyncMock(
            return_value="expanded query text about AI agents and discovery"
        )
        openai_svc._discovery_service = discovery

        result = await openai_svc.expand_query_for_discovery("AI")

        assert isinstance(result, str)
        assert "expanded query text" in result
        discovery.expand_query_for_discovery.assert_awaited_once_with("AI")
        openai_svc.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_discovery_service_unbound(self, openai_svc):
        with pytest.raises(LLMServiceNotBoundError):
            await openai_svc.expand_query_for_discovery("AI")

    @pytest.mark.asyncio
    async def test_returns_original_on_discovery_service_fallback(self, openai_svc):
        discovery = AsyncMock()
        discovery.expand_query_for_discovery = AsyncMock(return_value="AI")
        openai_svc._discovery_service = discovery

        result = await openai_svc.expand_query_for_discovery("AI")

        assert result == "AI"


class TestCallSupervisorLlmJson:

    @pytest.mark.asyncio
    async def test_returns_parsed_dict_from_json_response(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            '{"action": "proceed", "confidence": 0.95}'
        )

        result = await openai_svc.call_supervisor_llm_json("system", "user")

        assert isinstance(result, dict)
        assert result == {"action": "proceed", "confidence": 0.95}

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_content(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(None)

        with pytest.raises(ValueError, match="Empty response from Supervisor LLM"):
            await openai_svc.call_supervisor_llm_json("system", "user")

    @pytest.mark.asyncio
    async def test_raises_json_decode_error_on_invalid_json(self, openai_svc):
        openai_svc.client.chat.completions.create.return_value = _chat_completion(
            "not json at all"
        )

        import json

        with pytest.raises(json.JSONDecodeError):
            await openai_svc.call_supervisor_llm_json("system", "user")

    @pytest.mark.asyncio
    async def test_custom_model_override_uses_public_openai_provider_override(self):
        class FakeGateway:
            def __init__(self):
                self.calls = []

            async def generate_structured_with_provider(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                return LLMStructuredResponse(
                    data={"action": "proceed"},
                    model=kwargs["model"],
                )

        gateway = FakeGateway()
        svc = OpenAIService()
        svc.bind_llm_gateway(gateway, LLMGatewayConfig())

        result = await svc.call_supervisor_llm_json(
            "system",
            "user",
            model="custom-openai-model",
        )

        assert result == {"action": "proceed"}
        assert gateway.calls[0][1]["provider"] == "openai"
        assert gateway.calls[0][1]["model"] == "custom-openai-model"

    @pytest.mark.asyncio
    async def test_default_supervisor_calls_use_supervisor_logical_model(self):
        class FakeGateway:
            def __init__(self):
                self.calls = []

            async def generate_structured_with_provider(self, messages, **kwargs):
                raise AssertionError("provider override should not be used")

            async def generate_with_provider(self, messages, **kwargs):
                raise AssertionError("provider override should not be used")

            def generate_stream_with_provider(self, messages, **kwargs):
                raise AssertionError("provider override should not be used")

            async def generate_structured(self, messages, **kwargs):
                self.calls.append(kwargs)
                return LLMStructuredResponse(data={"action": "proceed"}, model="gpt")

            async def generate(self, messages, **kwargs):
                self.calls.append(kwargs)
                from common.dto import LLMResponse

                return LLMResponse(content="ok", model="gpt")

            def generate_stream(self, messages, **kwargs):
                self.calls.append(kwargs)
                async def _stream():
                    yield "ok"

                return _stream()

        gateway = FakeGateway()
        svc = OpenAIService()
        svc.bind_llm_gateway(gateway, LLMGatewayConfig())

        await svc.call_supervisor_llm_json("system", "user")
        await svc.call_supervisor_llm_text("system", "user")
        chunks = [
            chunk async for chunk in svc.call_supervisor_llm_text_stream("system", "user")
        ]

        assert chunks == ["ok"]
        assert [call["model"] for call in gateway.calls] == [
            "supervisor_model",
            "supervisor_model",
            "supervisor_model",
        ]

    @pytest.mark.asyncio
    async def test_responses_compat_custom_model_uses_public_openai_provider_override(self):
        class FakeGateway:
            def __init__(self):
                self.hinted_calls = []
                self.public_calls = []

            async def generate_with_provider(self, messages, **kwargs):
                self.hinted_calls.append((messages, kwargs))
                return LLMResponse(content="ok", model=kwargs["model"])

            async def generate(self, messages, **kwargs):
                self.public_calls.append((messages, kwargs))
                raise AssertionError("public generate should not be used")

        gateway = FakeGateway()
        compat = _GatewayOpenAIResponses(gateway)

        response = await compat.create(
            model="gpt-4.1",
            input=[{"role": "user", "content": "hi"}],
            reasoning={"effort": "low"},
        )

        assert response.output_text == "ok"
        assert gateway.public_calls == []
        assert gateway.hinted_calls[0][1] == {
            "model": "gpt-4.1",
            "provider": "openai",
        }


class TestLegacyFocusedWorkflowDelegation:
    @pytest.mark.asyncio
    async def test_summary_stream_delegates_to_focused_summary_service(self, openai_svc):
        summary_service = MagicMock()
        summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("summary")
        )
        openai_svc._summary_service = summary_service

        chunks = [
            chunk
            async for chunk in openai_svc.summarize_agent_responses_stream(
                [{"agent_id": "a1", "agent_name": "Agent", "message": "answer"}],
                mode="debate",
                user_question="question",
            )
        ]

        assert chunks == ["summary"]
        passed = summary_service.summarize_agent_responses_stream.call_args.args[0]
        assert all(isinstance(item, RoomMessageSummary) for item in passed)
        assert passed[0].agent_name == "Agent"
        summary_service.summarize_agent_responses_stream.assert_called_once_with(
            passed,
            mode="debate",
            user_question="question",
        )
        openai_svc.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_chat_context_delegates_to_room_memory_service(
        self, openai_svc
    ):
        room_memory = AsyncMock()
        room_memory.generate_chat_context = AsyncMock(return_value="context")
        openai_svc._room_memory_llm_service = room_memory
        context_data = MagicMock()
        context_data.context_content = "existing"

        result = await openai_svc.generate_chat_context(
            "user input",
            "agent response",
            context_data,
        )

        assert result == "context"
        request = room_memory.generate_chat_context.await_args.args[0]
        assert isinstance(request, ChatContextGenerationInput)
        assert request.existing_context == "existing"
        openai_svc.client.responses.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_chat_context_propagates_routing_errors(
        self, openai_svc
    ):
        room_memory = AsyncMock()
        room_memory.generate_chat_context = AsyncMock(
            side_effect=LLMModelRoutingError("unregistered model")
        )
        openai_svc._room_memory_llm_service = room_memory
        context_data = MagicMock()
        context_data.context_content = "existing"

        with pytest.raises(LLMModelRoutingError):
            await openai_svc.generate_chat_context(
                "user input",
                "agent response",
                context_data,
            )

    @pytest.mark.asyncio
    async def test_generate_room_memory_content_delegates_to_room_memory_service(
        self, openai_svc
    ):
        room_memory = AsyncMock()
        room_memory.generate_room_memory_content = AsyncMock(return_value="memory")
        openai_svc._room_memory_llm_service = room_memory
        memory_content = MagicMock()
        memory_content.memory_text = "existing memory"

        result = await openai_svc.generate_room_memory_content([], memory_content)

        assert result == "memory"
        request = room_memory.generate_room_memory_content.await_args.args[0]
        assert isinstance(request, RoomMemoryGenerationInput)
        assert request.existing_memory == "existing memory"
        assert request.messages == []
        openai_svc.client.responses.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_room_memory_content_propagates_routing_errors(
        self, openai_svc
    ):
        room_memory = AsyncMock()
        room_memory.generate_room_memory_content = AsyncMock(
            side_effect=LLMModelRoutingError("unregistered model")
        )
        openai_svc._room_memory_llm_service = room_memory
        memory_content = MagicMock()
        memory_content.memory_text = "existing memory"

        with pytest.raises(LLMModelRoutingError):
            await openai_svc.generate_room_memory_content([], memory_content)


class TestParseUserMessageByLlm:
    @pytest.mark.asyncio
    async def test_fails_fast_when_message_parser_service_unbound(self, openai_svc):
        with pytest.raises(LLMServiceNotBoundError):
            await openai_svc.parse_user_message_by_llm("please help")

    @pytest.mark.asyncio
    async def test_delegates_to_focused_message_parser_with_structured_request(
        self, openai_svc
    ):
        parser = AsyncMock()
        parser.parse_user_message = AsyncMock(
            return_value={
                "message_type": "AUTO_ASSIGNED",
                "original_text": "please help",
                "needs_decomposition": False,
                "task_steps": [
                    {
                        "step_id": "step_1",
                        "agent_id": "agent-1",
                        "agent_name": "Agent One",
                        "task_content": "please help",
                        "dependencies": [],
                    }
                ],
            }
        )
        openai_svc._message_parser_service = parser
        openai_svc._debate_rounds = 2

        result = await openai_svc.parse_user_message_by_llm(
            "please help",
            selected_agent_set={"agent-1": "Agent One"},
            auto_assign_agents=True,
            explicit_mentions=[
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "mention_text": "<@agent-1|Agent One>",
                }
            ],
        )

        assert result["task_steps"][0]["agent_id"] == "agent-1"
        parser.parse_user_message.assert_awaited_once()
        request = parser.parse_user_message.await_args.args[0]
        assert isinstance(request, ParsedUserMessageRequest)
        assert request.selected_agents == {"agent-1": "Agent One"}
        assert request.auto_assign_agents is True
        assert request.debate_rounds == 2
        assert request.explicit_mentions[0].agent_id == "agent-1"
        assert request.explicit_mentions[0].mention_text == "<@agent-1|Agent One>"


# ---------------------------------------------------------------------------
# Group 2: select_best_agent_for_task
# ---------------------------------------------------------------------------

class TestSelectBestAgentForTask:

    @staticmethod
    def _make_selectable_agent(agent_id, name, description="An agent"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.agent_card.name = name
        agent.agent_card.description = description
        agent.agent_card.capabilities = {"streaming": True}
        skill = MagicMock()
        skill.name = "skill1"
        skill.id = "s1"
        agent.agent_card.skills = [skill]
        return agent

    @pytest.mark.asyncio
    async def test_delegates_to_focused_agent_selection_service(self, openai_svc):
        selection = AsyncMock()
        selection.select_best_agent_for_task = AsyncMock(return_value="agent-2")
        openai_svc._agent_selection_service = selection
        agents = [
            self._make_selectable_agent("agent-1", "Agent One"),
            self._make_selectable_agent("agent-2", "Agent Two"),
        ]

        result = await openai_svc.select_best_agent_for_task("Write a blog post", agents)

        assert result == "agent-2"
        candidates = selection.select_best_agent_for_task.await_args.args[1]
        assert all(isinstance(candidate, AgentRoutingCandidate) for candidate in candidates)
        assert candidates[0].agent_id == "agent-1"
        assert candidates[1].agent_id == "agent-2"
        openai_svc.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_agent_selection_service_unbound(self, openai_svc):
        agents = [
            self._make_selectable_agent("agent-1", "Agent One"),
            self._make_selectable_agent("agent-2", "Agent Two"),
        ]

        with pytest.raises(LLMServiceNotBoundError):
            await openai_svc.select_best_agent_for_task("Write a blog post", agents)


class TestDebateDelegation:
    def test_bind_debate_service_sets_focused_debate_service(self, openai_svc):
        debate = object()

        openai_svc.bind_debate_service(debate)

        assert openai_svc._debate_service is debate

    @pytest.mark.asyncio
    async def test_short_debate_delegates_to_focused_debate_service(self, openai_svc):
        debate = AsyncMock()
        debate.short_debate_with_openai = AsyncMock(return_value="updated answer")
        openai_svc._debate_service = debate

        result = await openai_svc.short_debate_with_openai("question", "peer answer")

        assert result == "updated answer"
        debate.short_debate_with_openai.assert_awaited_once_with(
            "question",
            "peer answer",
        )
        openai_svc.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_long_debate_delegates_to_focused_debate_service(self, openai_svc):
        debate = AsyncMock()
        debate.long_debate_with_openai = AsyncMock(return_value="updated answer")
        openai_svc._debate_service = debate

        result = await openai_svc.long_debate_with_openai("question", "peer answer")

        assert result == "updated answer"
        debate.long_debate_with_openai.assert_awaited_once_with(
            "question",
            "peer answer",
        )
        openai_svc.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_debate_service_unbound(self, openai_svc):
        with pytest.raises(LLMServiceNotBoundError):
            await openai_svc.short_debate_with_openai("question", "answer")

        with pytest.raises(LLMServiceNotBoundError):
            await openai_svc.long_debate_with_openai("question", "answer")
