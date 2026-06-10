import pytest

from common.dto import RoomMessageSummary
from common.prompts.markdown_response_format import HYBRO_MARKDOWN_RESPONSE_FORMAT
from execution.orchestration.room_supervisor_service import SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
from llm_gateway.services.summary import SummaryLLMService


class FakeStreamGateway:
    def __init__(self) -> None:
        self.stream_calls: list[tuple[list[dict[str, str]], dict]] = []

    async def generate_stream(self, messages, **kwargs):
        self.stream_calls.append((messages, kwargs))
        yield "summary"


def test_hybro_markdown_response_format_requires_section_headers() -> None:
    assert "###" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "starts numbering over at `1.`" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "top-level ordered list" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "Hybro renders them nested" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "Do not:" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "1. •" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "4 spaces" in HYBRO_MARKDOWN_RESPONSE_FORMAT


def test_supervisor_synthesis_prompt_includes_shared_markdown_format() -> None:
    assert "### TL;DR" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "starts numbering over at `1.`" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "{trajectory_summary}" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_summary_service_system_prompt_includes_markdown_format() -> None:
    gateway = FakeStreamGateway()
    service = SummaryLLMService(gateway)

    async for _ in service.summarize_agent_responses_stream(
        [
            RoomMessageSummary(
                agent_id="agent-a",
                agent_name="Agent A",
                message="Response A",
            )
        ],
        user_question="question",
    ):
        pass

    system_prompt = gateway.stream_calls[0][0][0]["content"]
    assert "### Prioritized items" in system_prompt
    assert "Do not write `1.` for every item" in system_prompt


@pytest.mark.asyncio
async def test_summary_service_debate_prompt_includes_markdown_format() -> None:
    gateway = FakeStreamGateway()
    service = SummaryLLMService(gateway)

    async for _ in service.summarize_agent_responses_stream(
        [
            RoomMessageSummary(
                agent_id="agent-a",
                agent_name="Agent A",
                message="Response A",
            )
        ],
        mode="debate",
        user_question="question",
    ):
        pass

    system_prompt = gateway.stream_calls[0][0][0]["content"]
    assert "multi-agent debate" in system_prompt
    assert "### Prioritized items" in system_prompt
    assert "Do not write `1.` for every item" in system_prompt
