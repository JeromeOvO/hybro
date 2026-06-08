from common.prompts.markdown_response_format import HYBRO_MARKDOWN_RESPONSE_FORMAT
from execution.orchestration.room_supervisor_service import SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT


def test_hybro_markdown_response_format_requires_section_headers() -> None:
    assert "###" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "Restart at 1" in HYBRO_MARKDOWN_RESPONSE_FORMAT
    assert "4 spaces" in HYBRO_MARKDOWN_RESPONSE_FORMAT


def test_supervisor_synthesis_prompt_includes_shared_markdown_format() -> None:
    assert "### TL;DR" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "Restart at 1" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT
    assert "{trajectory_summary}" in SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT


def test_summary_service_system_prompt_includes_markdown_format() -> None:
    debate_prompt = (
        "You are HYBRO AI summarizing a multi-agent debate. Compare "
        "perspectives, agreements, disagreements, and actionable conclusions.\n\n"
        + HYBRO_MARKDOWN_RESPONSE_FORMAT
    )
    non_debate_prompt = (
        "You are HYBRO AI synthesizing multi-agent responses. Preserve useful "
        "agent attribution and return a concise user-facing answer.\n\n"
        + HYBRO_MARKDOWN_RESPONSE_FORMAT
    )
    assert "### Prioritized items" in debate_prompt
    assert "### Prioritized items" in non_debate_prompt
    assert "Restart at 1" in non_debate_prompt
