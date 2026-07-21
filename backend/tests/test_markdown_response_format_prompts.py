import pytest

from common.dto import RoomMessageSummary
from common.prompts.markdown_response_format import HYBRO_MARKDOWN_RESPONSE_FORMAT
from execution.orchestration.room_supervisor_service import (
    SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT,
    RoomSupervisorService,
)
from llm_gateway.services.summary import SummaryLLMService
from models.supervisor import SupervisorTrajectory


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
    assert "{user_goal}" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "Answer the original user goal directly" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert '"Requesting ..."' in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "starts numbering over at `1.`" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "{trajectory_summary}" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT


def test_supervisor_synthesis_prompt_is_grounded_in_original_user_goal() -> None:
    service = RoomSupervisorService()

    system_prompt, user_prompt = service._synthesis_prompts(
        SupervisorTrajectory(),
        "Use the available evidence.",
        "Return the final approved launch date.",
    )

    assert "Return the final approved launch date." in system_prompt
    assert "Use the available evidence." in system_prompt
    assert user_prompt == "Write the final answer that best fulfills the original user goal."


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
    assert "final answer to the user's request" in system_prompt
    assert "do not force a TL;DR" in system_prompt
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
    assert "do not force a TL;DR" in system_prompt
    assert "Do not write `1.` for every item" in system_prompt
