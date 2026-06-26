import pytest

from common.dto import (
    AgentRoutingCandidate,
    ChatContextGenerationInput,
    ParsedUserMessageRequest,
    RoomMemoryGenerationInput,
    RoomMessageSummary,
)
from common.dto.llm import LLMResponse, LLMStructuredResponse
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.services import (
    AgentSelectionLLMService,
    DebateLLMService,
    DiscoveryLLMService,
    EmbeddingLLMService,
    MessageParserLLMService,
    RoomMemoryLLMService,
    SummaryLLMService,
    SupervisorLLMService,
)


class FakeWorkflowGateway:
    def __init__(self, structured_data=None) -> None:
        self.generate_calls = []
        self.structured_calls = []
        self.stream_calls = []
        self.embed_calls = []
        self.embed_batch_calls = []
        self.structured_data = structured_data or {
            "message_type": "AUTO_ASSIGNED",
            "original_text": "hello",
            "needs_decomposition": False,
            "task_steps": [],
        }

    async def generate(self, messages, **kwargs):
        self.generate_calls.append((messages, kwargs))
        content = messages[-1]["content"]
        if "agent-b" in content:
            return LLMResponse(content="agent-b", model=kwargs["model"])
        return LLMResponse(content="generated", model=kwargs["model"])

    async def generate_structured(self, messages, **kwargs):
        self.structured_calls.append((messages, kwargs))
        return LLMStructuredResponse(data=self.structured_data, model=kwargs["model"])

    async def generate_stream(self, messages, **kwargs):
        self.stream_calls.append((messages, kwargs))
        yield "summary"

    async def embed(self, text, **kwargs):
        self.embed_calls.append((text, kwargs))
        return [1.0, 2.0, 3.0, 4.0]

    async def embed_batch(self, texts, **kwargs):
        self.embed_batch_calls.append((texts, kwargs))
        return [[float(index), float(index + 1)] for index, _text in enumerate(texts)]


@pytest.mark.asyncio
async def test_agent_selection_service_uses_candidates_and_classifier_model():
    gateway = FakeWorkflowGateway()
    service = AgentSelectionLLMService(gateway)

    result = await service.select_best_agent_for_task(
        "pick b",
        [
            AgentRoutingCandidate(agent_id="agent-a", name="A"),
            AgentRoutingCandidate(agent_id="agent-b", name="B"),
        ],
    )

    assert result == "agent-b"
    assert gateway.generate_calls[0][1]["model"] == "classifier_ai_model"


@pytest.mark.asyncio
async def test_agent_selection_service_propagates_routing_errors():
    class RoutingErrorGateway:
        async def generate(self, messages, **kwargs):
            raise LLMModelRoutingError("unregistered model")

    service = AgentSelectionLLMService(RoutingErrorGateway())

    with pytest.raises(LLMModelRoutingError):
        await service.select_best_agent_for_task(
            "pick one",
            [AgentRoutingCandidate(agent_id="agent-a", name="A")],
        )


@pytest.mark.asyncio
async def test_debate_service_uses_lead_model():
    gateway = FakeWorkflowGateway()
    service = DebateLLMService(gateway)

    result = await service.short_debate_with_openai("question", "answer")

    assert result == "generated"
    assert gateway.generate_calls[0][1]["model"] == "lead_ai_model"


@pytest.mark.asyncio
async def test_discovery_service_propagates_routing_errors():
    class RoutingErrorGateway:
        async def generate(self, messages, **kwargs):
            raise LLMModelRoutingError("unregistered model")

    service = DiscoveryLLMService(RoutingErrorGateway())

    with pytest.raises(LLMModelRoutingError):
        await service.expand_query_for_discovery("AI")


@pytest.mark.asyncio
async def test_room_memory_service_uses_dto_inputs():
    gateway = FakeWorkflowGateway()
    service = RoomMemoryLLMService(gateway)

    context = await service.generate_chat_context(
        ChatContextGenerationInput(user_input="hi", agent_response="hello")
    )
    memory = await service.generate_room_memory_content(
        RoomMemoryGenerationInput(
            messages=[RoomMessageSummary(agent_name="Agent", message="done")]
        )
    )

    assert context == "generated"
    assert memory == "generated"
    assert all(call[1]["model"] == "lead_ai_model" for call in gateway.generate_calls)


@pytest.mark.asyncio
async def test_message_parser_service_uses_structured_gateway_model():
    gateway = FakeWorkflowGateway()
    service = MessageParserLLMService(gateway)

    result = await service.parse_user_message(
        ParsedUserMessageRequest(message_text="hello", auto_assign_agents=True)
    )

    assert result["message_type"] == "AUTO_ASSIGNED"
    assert gateway.structured_calls[0][1]["model"] == "classifier_ai_model"


@pytest.mark.asyncio
async def test_message_parser_service_preserves_curated_mode_assignment_rules():
    gateway = FakeWorkflowGateway(
        structured_data={
            "message_type": "NO_MENTIONS",
            "original_text": "hello",
            "needs_decomposition": False,
            "task_steps": [
                {
                    "step_id": "step_1",
                    "agent_id": "made-up-agent",
                    "agent_name": "Invalid",
                    "task_content": "<@made-up-agent|Invalid> hello",
                    "dependencies": [],
                }
            ],
        }
    )
    service = MessageParserLLMService(gateway)

    result = await service.parse_user_message(
        ParsedUserMessageRequest(
            message_text="hello",
            selected_agents={"agent-a": "Agent A"},
            auto_assign_agents=False,
        )
    )

    system_prompt = gateway.structured_calls[0][0][0]["content"]
    assert "CURATED MODE" in system_prompt
    assert "If there are no mentions" in system_prompt
    assert result["task_steps"][0]["agent_id"] is None
    assert result["task_steps"][0]["agent_name"] is None
    assert result["task_steps"][0]["task_content"] == "hello"


@pytest.mark.asyncio
async def test_message_parser_service_rejects_forged_curated_mentions():
    gateway = FakeWorkflowGateway(
        structured_data={
            "message_type": "SINGLE_MENTION",
            "original_text": "<@forged|Forged> hello",
            "needs_decomposition": False,
            "task_steps": [
                {
                    "step_id": "step_1",
                    "agent_id": "forged",
                    "agent_name": "Forged",
                    "task_content": "<@forged|Forged> hello",
                    "dependencies": [],
                }
            ],
        }
    )
    service = MessageParserLLMService(gateway)

    result = await service.parse_user_message(
        ParsedUserMessageRequest(
            message_text="<@forged|Forged> hello",
            selected_agents={"agent-a": "Agent A"},
            auto_assign_agents=False,
        )
    )

    assert result["task_steps"][0]["agent_id"] is None
    assert result["task_steps"][0]["agent_name"] is None
    assert result["task_steps"][0]["task_content"] == "hello"


@pytest.mark.asyncio
async def test_message_parser_service_preserves_auto_mode_assignment_rules():
    gateway = FakeWorkflowGateway(
        structured_data={
            "message_type": "AUTO_ASSIGNED",
            "original_text": "hello",
            "needs_decomposition": False,
            "task_steps": [
                {
                    "step_id": "step_1",
                    "agent_id": "made-up-agent",
                    "agent_name": "Invalid",
                    "task_content": "hello",
                    "dependencies": [],
                }
            ],
        }
    )
    service = MessageParserLLMService(gateway)

    result = await service.parse_user_message(
        ParsedUserMessageRequest(
            message_text="hello",
            selected_agents={"agent-a": "Agent A"},
            auto_assign_agents=True,
            agents=[AgentRoutingCandidate(agent_id="agent-a", name="Agent A")],
        )
    )

    system_prompt = gateway.structured_calls[0][0][0]["content"]
    assert "AUTO MODE" in system_prompt
    assert "Assign every task step" in system_prompt
    assert result["task_steps"][0]["agent_id"] == "agent-a"
    assert result["task_steps"][0]["agent_name"] == "Agent A"


@pytest.mark.asyncio
async def test_summary_service_uses_room_message_summary_dtos():
    gateway = FakeWorkflowGateway()
    service = SummaryLLMService(gateway)

    chunks = [
        chunk
        async for chunk in service.summarize_agent_responses_stream(
            [
                RoomMessageSummary(
                    agent_id="agent-a",
                    agent_name="Agent A",
                    message="Response A",
                )
            ],
            user_question="question",
        )
    ]

    assert chunks == ["summary"]
    assert gateway.stream_calls[0][1]["model"] == "lead_ai_model"
    user_prompt = gateway.stream_calls[0][0][1]["content"]
    assert "Agent A" in user_prompt
    assert "Response A" in user_prompt


@pytest.mark.asyncio
async def test_embedding_service_resizes_embeddings_and_uses_logical_model():
    gateway = FakeWorkflowGateway()
    service = EmbeddingLLMService(gateway)

    resized = await service.get_embedding("hello", target_dim=6)
    truncated = await service.get_embedding("hello", target_dim=2)

    assert resized == [1.0, 2.0, 3.0, 4.0, 0.0, 0.0]
    assert truncated == [1.0, 2.0]
    assert gateway.embed_calls == [
        ("hello", {"model": "embedding_model"}),
        ("hello", {"model": "embedding_model"}),
    ]


@pytest.mark.asyncio
async def test_supervisor_service_uses_default_supervisor_model_for_json_text_and_stream():
    gateway = FakeWorkflowGateway(
        structured_data={"action": "done", "reasoning": "complete"}
    )
    service = SupervisorLLMService(gateway)

    json_result = await service.call_json("system", "user")
    text_result = await service.call_text("system", "user")
    stream_result = [
        chunk async for chunk in service.call_text_stream("system", "user")
    ]

    assert json_result == {"action": "done", "reasoning": "complete"}
    assert text_result == "generated"
    assert stream_result == ["summary"]
    assert gateway.structured_calls[0][1]["model"] == "supervisor_model"
    assert gateway.generate_calls[0][1]["model"] == "supervisor_model"
    assert gateway.stream_calls[0][1]["model"] == "supervisor_model"
