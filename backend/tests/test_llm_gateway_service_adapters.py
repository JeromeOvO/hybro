import pytest

from common.dto import (
    AgentRoutingCandidate,
)
from common.dto.llm import LLMResponse, LLMStructuredResponse
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.services import (
    AgentSelectionLLMService,
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
async def test_agent_selection_ranking_accepts_fenced_json_and_sanitizes_ids():
    class FencedGateway:
        async def generate(self, messages, **kwargs):
            return LLMResponse(
                content='```json\n["agent-b", "unknown", "agent-b"]\n```',
                model=kwargs["model"],
            )

    service = AgentSelectionLLMService(FencedGateway())
    candidates = [
        AgentRoutingCandidate(agent_id="agent-a", name="A"),
        AgentRoutingCandidate(agent_id="agent-b", name="B"),
    ]

    assert await service.rank_agents_for_task("pick b", candidates) == [
        "agent-b",
        "agent-a",
    ]


@pytest.mark.asyncio
async def test_agent_selection_ranking_falls_back_for_unparseable_output():
    class InvalidGateway:
        async def generate(self, messages, **kwargs):
            return LLMResponse(content="agent-b first", model=kwargs["model"])

    service = AgentSelectionLLMService(InvalidGateway())
    candidates = [
        AgentRoutingCandidate(agent_id="agent-a", name="A"),
        AgentRoutingCandidate(agent_id="agent-b", name="B"),
    ]

    assert await service.rank_agents_for_task("pick b", candidates) == [
        "agent-a",
        "agent-b",
    ]
