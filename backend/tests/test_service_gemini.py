import pytest
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

from app_shell.gemini_service import GeminiService
from common.dto import LLMResponse
from llm_gateway.errors import LLMServiceNotBoundError


class FakeGeminiGateway:
    def __init__(self) -> None:
        self.generated: list[tuple[list[dict], dict]] = []
        self.embedded: list[tuple[list[str], str]] = []

    async def generate(self, messages: list[dict], **kwargs):
        self.generated.append((messages, kwargs))
        content = messages[0]["content"]
        if "plain breakdown" in content:
            text = "single plain step"
        elif "Break down this task" in content:
            text = '{"steps": [{"description": "do it", "step_id": "step_1"}]}'
        elif "Summarize the following agent output" in content:
            text = "short summary"
        else:
            text = "generated text"
        return LLMResponse(content=text, model=kwargs["model"])

    async def embed_batch(self, texts: list[str], model: str):
        self.embedded.append((texts, model))
        return [[0.1, 0.2] for _ in texts]


@pytest.mark.asyncio
async def test_unbound_gemini_service_raises_clear_binding_error():
    svc = GeminiService()

    with pytest.raises(LLMServiceNotBoundError):
        await svc.generate_text("hello")


@pytest.mark.asyncio
async def test_gemini_get_embedding_delegates_to_gateway_batch_embedding():
    gateway = FakeGeminiGateway()
    svc = GeminiService()
    svc.bind_llm_gateway(gateway)

    result = await svc.get_embedding("hello")

    assert result == [0.1, 0.2]
    assert gateway.embedded == [(["hello"], "gemini_embedding_model_name")]


@pytest.mark.asyncio
async def test_gemini_generate_text_includes_context_and_uses_logical_model():
    gateway = FakeGeminiGateway()
    svc = GeminiService()
    svc.bind_llm_gateway(gateway)

    result = await svc.generate_text("hello", context={"room": "r1"})

    assert result == "generated text"
    messages, kwargs = gateway.generated[0]
    assert kwargs["model"] == "gemini_model_name"
    assert "Context:" in messages[0]["content"]


@pytest.mark.asyncio
async def test_gemini_lead_ai_completion_parses_json_or_wraps_plain_text():
    gateway = FakeGeminiGateway()
    svc = GeminiService()
    svc.bind_llm_gateway(gateway)

    parsed = await svc.lead_ai_completion("complex task")
    wrapped = await svc.lead_ai_completion("plain breakdown")

    assert parsed == {"steps": [{"description": "do it", "step_id": "step_1"}]}
    assert wrapped == {
        "steps": [{"description": "single plain step", "step_id": "single_step"}]
    }


@pytest.mark.asyncio
async def test_gemini_process_task_completes_with_generated_agent_message():
    gateway = FakeGeminiGateway()
    svc = GeminiService()
    svc.bind_llm_gateway(gateway)
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.working),
        history=[
            Message(
                message_id="msg-1",
                role=Role.user,
                parts=[Part(root=TextPart(text="hello"))],
            )
        ],
    )

    result = await svc.process_task(task)

    assert result.status.state.value == "completed"
    assert result.history[-1].role == Role.agent
    assert result.history[-1].parts[0].root.text == "generated text"


@pytest.mark.asyncio
async def test_gemini_summarize_output_delegates_to_gateway_generation():
    gateway = FakeGeminiGateway()
    svc = GeminiService()
    svc.bind_llm_gateway(gateway)

    result = await svc.summarize_output("long output")

    assert result == "short summary"
    assert gateway.generated[-1][1]["model"] == "gemini_model_name"
