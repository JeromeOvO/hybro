from __future__ import annotations

from types import SimpleNamespace

import pytest

from context_memory import assembly
from context_memory.config import TokenBudgetConfig
from context_memory.models import RoomSummaryData


def _fallback_estimate_tokens(text: str | None, model: str = "gpt-4") -> int:
    if not text:
        return 0
    return len(text) // 4


@pytest.fixture(autouse=True)
def deterministic_token_estimator(monkeypatch):
    """Golden token counts are pinned to the fallback estimator."""
    monkeypatch.setattr(assembly, "estimate_tokens", _fallback_estimate_tokens)


def test_stable_prefix_fixed_golden():
    prefix = assembly.build_stable_prefix(
        room_summary=RoomSummaryData(
            current_goal="Ship Phase 5",
            key_decisions=["Use protocols"],
            open_questions=["Any drift?"],
            recent_agent_contributions=["Builder added tests"],
            important_constraints=["No concrete imports"],
        ),
        agent_registry=[
            {"agent_id": "b", "agent_name": "Builder", "description": "Writes code"},
            {"agent_id": "a", "agent_name": "Analyst", "description": "Reads code"},
        ],
        room_facts=["Fact one", "Fact two"],
        memory_search_snippets=["[user] remembered thing"],
    )

    assert prefix == (
        "[Room Context]\n"
        "Current Goal: Ship Phase 5\n"
        "Key Decisions: Use protocols\n"
        "Open Questions: Any drift?\n"
        "Recent Agent Work: Builder added tests\n"
        "Constraints: No concrete imports\n"
        "\n"
        "[Available Agents]\n"
        "- Analyst: Reads code\n"
        "- Builder: Writes code\n"
        "\n"
        "[Room Facts]\n"
        "- Fact one\n"
        "- Fact two\n"
        "\n"
        "[Relevant Memory]\n"
        "- [user] remembered thing\n"
    )


def test_dynamic_suffix_fixed_golden():
    suffix = assembly.build_dynamic_suffix(
        turns=[],
        current_task="Do the work",
        include_summary=True,
        summary="Earlier context",
    )

    assert suffix == (
        "[Earlier conversation summary]\n"
        "Earlier context\n"
        "\n"
        "[Current request]\n"
        "User: Do the work"
    )


def test_agent_quoted_context_fixed_golden():
    suffix = assembly.build_agent_dynamic_suffix(
        turns=[],
        summary=None,
        current_task="Answer this",
        agent_name="Agent",
        quoted_text="quoted content",
    )

    assert suffix == (
        "[Quoted context]\n"
        "The user is referencing the following specific content:\n"
        "\"quoted content\"\n"
        "\n"
        "[Current request]\n"
        "User: Answer this\n"
        "\n"
        "You are Agent. Execute the current request above and provide concrete results. "
        "Do NOT just describe or plan what should be done - actually complete the task "
        "and deliver the output. Use the conversation context if relevant. Pay special "
        "attention to the quoted context — the user is asking about or responding to "
        "that specific content."
    )


def test_supervisor_assembly_fixed_golden_with_search_and_compact_pointer():
    result = assembly.assemble_supervisor_context_from_memory(
        _assembly_room_doc(),
        "Review the plan",
        token_budget=_wide_budget(),
        agent_registry=[
            {"agent_id": "a", "agent_name": "Analyst", "description": "Reads code"}
        ],
        memory_search_results=[
            SimpleNamespace(
                content_preview="Remember migration parity",
                role="user",
            )
        ],
    )

    assert result.metadata["context"] == (
        "[Room Context]\n"
        "Current Goal: Ship Phase 5\n"
        "Key Decisions: Use protocol facades\n"
        "\n"
        "[Available Agents]\n"
        "- Analyst: Reads code\n"
        "\n"
        "[Relevant Memory]\n"
        "- [user] Remember migration parity\n"
        "\n"
        "\n"
        "[Recent conversation]\n"
        "User: Please implement phase five tests.\n"
        "Builder: Implemented repository coverage. "
        "[Content stored: db/conversation_content/doc-2]\n"
        "\n"
        "[Current request]\n"
        "User: Review the plan"
    )
    assert result.blocks[0].content == (
        "[Room Context]\n"
        "Current Goal: Ship Phase 5\n"
        "Key Decisions: Use protocol facades\n"
        "\n"
        "[Available Agents]\n"
        "- Analyst: Reads code\n"
        "\n"
        "[Relevant Memory]\n"
        "- [user] Remember migration parity\n"
    )
    assert result.blocks[1].content == (
        "[Recent conversation]\n"
        "User: Please implement phase five tests.\n"
        "Builder: Implemented repository coverage. "
        "[Content stored: db/conversation_content/doc-2]\n"
        "\n"
        "[Current request]\n"
        "User: Review the plan"
    )
    assert result.total_tokens == 91
    assert result.metadata["stable_prefix_tokens"] == 43
    assert result.metadata["dynamic_suffix_tokens"] == 48
    assert result.metadata["turns_included"] == 2
    assert result.metadata["turns_truncated"] == 0
    assert result.metadata["full_turns"] == 1
    assert result.metadata["compact_turns"] == 1
    assert result.metadata["truncation_reason"] is None
    assert result.metadata["was_truncated"] is False


def test_agent_assembly_fixed_golden_with_blocks_and_counts():
    result = assembly.assemble_agent_execution_context_from_memory(
        _assembly_room_doc(),
        "Patch the bug",
        token_budget=_wide_budget(),
        agent_id="agent-1",
        agent_name="Builder",
        room_awareness="Room requires compatibility.",
        quoted_text="quoted line",
    )

    assert result.metadata["context"] == (
        "[Room Context]\n"
        "Current Goal: Ship Phase 5\n"
        "Key Decisions: Use protocol facades\n"
        "\n"
        "[Room Facts]\n"
        "- Use pytest\n"
        "\n"
        "\n"
        "[Earlier conversation summary]\n"
        "Earlier short summary\n"
        "\n"
        "[Recent conversation]\n"
        "User: Please implement phase five tests.\n"
        "Builder: Implemented repository coverage. "
        "[Content stored: db/conversation_content/doc-2]\n"
        "\n"
        "[Quoted context]\n"
        "The user is referencing the following specific content:\n"
        "\"quoted line\"\n"
        "\n"
        "Room requires compatibility.\n"
        "\n"
        "[Current request]\n"
        "User: Patch the bug\n"
        "\n"
        "You are Builder. Execute the current request above and provide concrete results. "
        "Do NOT just describe or plan what should be done - actually complete the task "
        "and deliver the output. Use the conversation context if relevant. Pay special "
        "attention to the quoted context — the user is asking about or responding to "
        "that specific content."
    )
    assert result.blocks[0].token_count == 26
    assert result.blocks[1].token_count == 175
    assert result.total_tokens == 201
    assert result.metadata["stable_prefix_tokens"] == 26
    assert result.metadata["dynamic_suffix_tokens"] == 175
    assert result.metadata["turns_included"] == 2
    assert result.metadata["turns_truncated"] == 0
    assert result.metadata["full_turns"] == 1
    assert result.metadata["compact_turns"] == 1
    assert result.metadata["agent_id"] == "agent-1"
    assert result.metadata["truncation_reason"] is None
    assert result.metadata["was_truncated"] is False


def test_agent_token_truncation_fixed_golden():
    result = assembly.assemble_agent_execution_context_from_memory(
        _truncation_room_doc(),
        "current task",
        token_budget=TokenBudgetConfig(
            model_context_window=130,
            system_prompt=10,
            tool_schemas=10,
            response_reserve=10,
            room_context_pct=0.2,
            conversation_history_pct=0.2,
            current_task_pct=0.6,
        ),
        agent_name="Builder",
    )

    assert result.metadata["context"] == (
        "[Recent conversation]\n"
        "User: recent content\n"
        "\n"
        "[Current request]\n"
        "User: current task\n"
        "\n"
        "You are Builder. Execute the current request above and provide concrete results. "
        "Do NOT just describe or plan what should be done - actually complete the task "
        "and deliver the output. Use the conversation context if relevant."
    )
    assert result.total_tokens == 76
    assert result.metadata["stable_prefix_tokens"] == 0
    assert result.metadata["dynamic_suffix_tokens"] == 76
    assert result.metadata["turns_included"] == 1
    assert result.metadata["turns_truncated"] == 2
    assert result.metadata["full_turns"] == 1
    assert result.metadata["compact_turns"] == 0
    assert result.metadata["truncation_reason"] == "token_budget_exceeded"
    assert result.metadata["was_truncated"] is True


def test_supervisor_large_truncation_fixed_golden():
    result = assembly.assemble_supervisor_context_from_memory(
        _supervisor_truncation_room_doc(),
        "current task " * 100,
        token_budget=TokenBudgetConfig(
            model_context_window=140,
            system_prompt=10,
            tool_schemas=10,
            response_reserve=10,
            room_context_pct=0.2,
            conversation_history_pct=0.4,
            current_task_pct=0.4,
        ),
    )

    assert result.metadata["context"] == (
        "[Current request]\n"
        f"User: {'current task ' * 30}curre\n"
        "... [context truncated]"
    )
    assert result.total_tokens == 110
    assert result.metadata["stable_prefix_tokens"] == 0
    assert result.metadata["dynamic_suffix_tokens"] == 110
    assert result.metadata["turns_included"] == 0
    assert result.metadata["turns_truncated"] == 3
    assert result.metadata["full_turns"] == 0
    assert result.metadata["compact_turns"] == 0
    assert result.metadata["truncation_reason"] == "token_budget_exceeded"
    assert result.metadata["was_truncated"] is True


def test_direct_and_legacy_history_shape_fixed_golden():
    result = assembly.assemble_supervisor_context_from_memory(
        _direct_legacy_room_doc(),
        "current task",
        token_budget=_wide_budget(),
    )

    assert result.metadata["context"] == (
        "[Recent conversation]\n"
        "User: direct fresh\n"
        "User: legacy only\n"
        "Builder: direct only\n"
        "\n"
        "[Current request]\n"
        "User: current task"
    )
    assert result.total_tokens == 29
    assert result.metadata["stable_prefix_tokens"] == 0
    assert result.metadata["dynamic_suffix_tokens"] == 29
    assert result.metadata["turns_included"] == 3
    assert result.metadata["turns_truncated"] == 0
    assert result.metadata["full_turns"] == 3
    assert result.metadata["compact_turns"] == 0
    assert result.metadata["truncation_reason"] is None
    assert result.metadata["was_truncated"] is False


def _wide_budget() -> TokenBudgetConfig:
    return TokenBudgetConfig(
        model_context_window=1000,
        system_prompt=10,
        tool_schemas=10,
        response_reserve=10,
        room_context_pct=0.2,
        conversation_history_pct=0.4,
        current_task_pct=0.4,
    )


def _assembly_room_doc() -> dict:
    turns = [
        {
            "turn_id": "t1",
            "role": "user",
            "content": "Please implement phase five tests.",
            "representation": "full",
            "estimated_tokens_full": 8,
            "estimated_tokens_compact": 2,
        },
        {
            "turn_id": "t2",
            "role": "agent",
            "agent_name": "Builder",
            "content": None,
            "representation": "compact",
            "brief_summary": "Implemented repository coverage.",
            "content_ref": {
                "storage_type": "mongodb",
                "collection": "conversation_content",
                "document_id": "doc-2",
            },
            "estimated_tokens_full": 80,
            "estimated_tokens_compact": 6,
        },
    ]
    return {
        "room_id": "r-golden",
        "memory_id": "m-golden",
        "memory_content": {
            "summary": "Earlier short summary",
            "conversation_history": turns,
        },
        "conversation_history": turns,
        "room_summary": {
            "current_goal": "Ship Phase 5",
            "key_decisions": ["Use protocol facades"],
        },
        "room_facts": [{"content": "Use pytest"}],
    }


def _truncation_room_doc() -> dict:
    turns = [
        {
            "turn_id": "old",
            "role": "user",
            "content": "old content " * 40,
            "representation": "full",
            "estimated_tokens_full": 80,
            "estimated_tokens_compact": 2,
        },
        {
            "turn_id": "middle",
            "role": "agent",
            "agent_name": "Builder",
            "content": "middle content " * 40,
            "representation": "full",
            "estimated_tokens_full": 80,
            "estimated_tokens_compact": 2,
        },
        {
            "turn_id": "recent",
            "role": "user",
            "content": "recent content",
            "representation": "full",
            "estimated_tokens_full": 4,
            "estimated_tokens_compact": 2,
        },
    ]
    return {
        "room_id": "r-golden",
        "memory_id": "m-golden",
        "memory_content": {"summary": None, "conversation_history": turns},
        "conversation_history": turns,
        "room_summary": {},
        "room_facts": [],
    }


def _supervisor_truncation_room_doc() -> dict:
    doc = _truncation_room_doc()
    doc["room_summary"] = {"current_goal": "Ship it"}
    return doc


def _direct_legacy_room_doc() -> dict:
    return {
        "room_id": "r-golden",
        "memory_id": "m-golden",
        "memory_content": {
            "summary": None,
            "conversation_history": [
                {
                    "turn_id": "same",
                    "role": "user",
                    "content": "legacy stale",
                    "representation": "compact",
                    "content_ref": {
                        "storage_type": "mongodb",
                        "collection": "conversation_content",
                        "document_id": "legacy",
                    },
                    "estimated_tokens_full": 20,
                    "estimated_tokens_compact": 4,
                },
                {
                    "turn_id": "legacy-only",
                    "role": "user",
                    "content": "legacy only",
                    "representation": "full",
                    "estimated_tokens_full": 3,
                    "estimated_tokens_compact": 2,
                },
            ],
        },
        "conversation_history": [
            {
                "turn_id": "same",
                "role": "user",
                "content": "direct fresh",
                "representation": "full",
                "turn_notes": {"one_liner": "fresh"},
                "estimated_tokens_full": 3,
                "estimated_tokens_compact": 2,
            },
            {
                "turn_id": "direct-only",
                "role": "agent",
                "agent_name": "Builder",
                "content": "direct only",
                "representation": "full",
                "estimated_tokens_full": 3,
                "estimated_tokens_compact": 2,
            },
        ],
        "room_summary": {},
        "room_facts": [],
    }
