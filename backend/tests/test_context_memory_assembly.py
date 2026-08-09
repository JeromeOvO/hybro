from __future__ import annotations

import pytest

from context_memory import assembly
from context_memory.config import TokenBudgetConfig
from context_memory.models import (
    ConversationTurnData,
    RoomSummaryData,
    TruncationReason,
)


def turn(
    turn_id: str,
    content: str,
    *,
    tokens: int = 5,
    role: str = "user",
    representation: str = "full",
):
    return ConversationTurnData(
        turn_id=turn_id,
        role=role,
        agent_name="Agent" if role == "agent" else None,
        content=content,
        representation=representation,
        estimated_tokens_full=tokens,
        estimated_tokens_compact=max(1, tokens // 2),
    )


def room_doc(turns=None, summary=None, room_summary=None, facts=None):
    return {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"summary": summary},
        "conversation_history": [item.to_dict() for item in (turns or [])],
        "room_summary": room_summary or {},
        "room_facts": facts or [],
    }


def small_budget(window: int = 160) -> TokenBudgetConfig:
    return TokenBudgetConfig(
        model_context_window=window,
        system_prompt=10,
        tool_schemas=10,
        response_reserve=10,
        room_context_pct=0.2,
        conversation_history_pct=0.4,
        current_task_pct=0.4,
    )


def test_direct_zero_model_window_does_not_crash():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(turns=[]),
        "current task",
        token_budget=TokenBudgetConfig(
            model_context_window=0,
            system_prompt=0,
            tool_schemas=0,
            response_reserve=0,
        ),
    )

    assert result.total_tokens >= 0
    assert result.metadata["occupancy_pct"] >= 0
    assert "current task" in result.metadata["context"]


def test_build_stable_prefix_empty():
    assert assembly.build_stable_prefix() == ""


def test_build_stable_prefix_with_summary():
    prefix = assembly.build_stable_prefix(
        room_summary=RoomSummaryData(
            current_goal="Finish Phase 5",
            key_decisions=["Use protocols"],
            open_questions=["What remains?"],
        )
    )

    assert "[Room Context]" in prefix
    assert "Current Goal: Finish Phase 5" in prefix
    assert "Key Decisions: Use protocols" in prefix


def test_build_stable_prefix_with_agents():
    prefix = assembly.build_stable_prefix(
        agent_registry=[
            {"agent_id": "b", "agent_name": "Builder", "description": "Writes code"},
            {"agent_id": "a", "agent_name": "Analyst", "description": "Reads code"},
        ]
    )

    assert prefix.index("Analyst") < prefix.index("Builder")
    assert "- Builder: Writes code" in prefix


def test_build_dynamic_suffix_with_turns():
    suffix = assembly.build_dynamic_suffix(
        turns=[turn("t1", "hello")],
        current_task="now",
    )

    assert "[Recent conversation]" in suffix
    assert "User: hello" in suffix
    assert "[Current request]\nUser: now" in suffix


def test_build_agent_dynamic_suffix_with_quoted_text():
    suffix = assembly.build_agent_dynamic_suffix(
        turns=[],
        summary=None,
        current_task="answer",
        agent_name="Agent",
        quoted_text="quoted content",
    )

    assert "[Quoted context]" in suffix
    assert '"quoted content"' in suffix
    assert "You are Agent" in suffix
    assert "quoted context — the user" in suffix


def test_select_turns_within_budget_all_fit():
    selected, truncated = assembly.select_turns_within_budget(
        [turn("t1", "one", tokens=3), turn("t2", "two", tokens=3)],
        budget_tokens=10,
    )

    assert [item.turn_id for item in selected] == ["t1", "t2"]
    assert truncated == 0


def test_select_turns_within_budget_truncates():
    selected, truncated = assembly.select_turns_within_budget(
        [
            turn("t1", "one", tokens=10),
            turn("t2", "two", tokens=10),
            turn("t3", "three", tokens=10),
        ],
        budget_tokens=15,
    )

    assert [item.turn_id for item in selected] == ["t3"]
    assert truncated == 2


def test_supervisor_max_turns_zero_selects_no_history():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(turns=[turn("t1", "old"), turn("t2", "recent")]),
        "current task",
        token_budget=small_budget(1000),
        max_turns=0,
    )

    assert "old" not in result.metadata["context"]
    assert "recent" not in result.metadata["context"]
    assert result.metadata["turns_included"] == 0
    assert result.metadata["turns_truncated"] == 2
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TURN_COUNT_EXCEEDED.value
    )


def test_supervisor_negative_max_turns_fails_fast():
    with pytest.raises(ValueError, match="max_turns must be non-negative"):
        assembly.assemble_supervisor_context_from_memory(
            room_doc(turns=[turn("t1", "hello")]),
            "current task",
            token_budget=small_budget(1000),
            max_turns=-1,
        )


def test_supervisor_max_turns_discard_is_reported():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "oldest"),
                turn("t2", "middle"),
                turn("t3", "recent"),
            ]
        ),
        "current task",
        token_budget=small_budget(1000),
        max_turns=2,
    )

    assert "oldest" not in result.metadata["context"]
    assert "middle" in result.metadata["context"]
    assert "recent" in result.metadata["context"]
    assert result.metadata["turns_included"] == 2
    assert result.metadata["turns_truncated"] == 1
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TURN_COUNT_EXCEEDED.value
    )


def test_assemble_supervisor_context_basic():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[turn("t1", "hello")],
            room_summary={"current_goal": "Finish tests"},
        ),
        "current task",
        token_budget=small_budget(300),
        agent_registry=[{"agent_id": "a", "name": "Agent", "description": "helps"}],
    )

    assert result.room_id == "r1"
    assert result.metadata["mode"] == "supervisor"
    assert "Finish tests" in result.metadata["context"]
    assert "current task" in result.metadata["context"]


def test_assemble_agent_context_basic():
    result = assembly.assemble_agent_execution_context_from_memory(
        room_doc(
            turns=[turn("t1", "hello")],
            facts=[{"content": "Use pytest"}],
        ),
        "current task",
        token_budget=small_budget(300),
        agent_id="agent-1",
        agent_name="Agent",
    )

    assert result.metadata["mode"] == "agent"
    assert result.metadata["agent_id"] == "agent-1"
    assert "Use pytest" in result.metadata["context"]


def test_assemble_context_metadata_counts_full_and_compact_turns():
    result = assembly.assemble_agent_execution_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "full content", representation="full"),
                turn("t2", "compact content", representation="compact"),
            ]
        ),
        "current task",
        token_budget=small_budget(300),
        agent_name="Agent",
    )

    assert result.metadata["full_turns"] == 1
    assert result.metadata["compact_turns"] == 1


def test_char_limit_truncation(monkeypatch):
    monkeypatch.setattr(assembly, "MAX_CONTEXT_CHARS", 80)

    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(turns=[turn("t1", "x" * 500, tokens=5)]),
        "current task",
        token_budget=small_budget(1000),
    )

    assert result.metadata["was_truncated"] is True
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.CHAR_LIMIT_EXCEEDED.value
    )
    assert "current task" in result.metadata["context"]
    assert len(result.metadata["context"]) <= 80


def test_char_limit_truncates_returned_blocks(monkeypatch):
    monkeypatch.setattr(assembly, "MAX_CONTEXT_CHARS", 90)

    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[turn("t1", "x" * 500, tokens=5)],
            room_summary={"current_goal": "Keep this stable prefix"},
        ),
        "current task",
        token_budget=small_budget(1000),
    )

    stable_block = result.blocks[0].content
    dynamic_block = result.blocks[1].content
    block_context = (
        f"{stable_block}\n\n{dynamic_block}" if stable_block else dynamic_block
    )
    assert result.metadata["was_truncated"] is True
    assert block_context == result.metadata["context"]
    assert stable_block == ""
    assert "current task" in dynamic_block
    assert "x" * 100 not in dynamic_block


def test_char_limit_inside_stable_prefix_preserves_current_request(monkeypatch):
    monkeypatch.setattr(assembly, "MAX_CONTEXT_CHARS", 70)

    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "this dynamic turn is fully removed by stable truncation")
            ],
            room_summary={"current_goal": "stable " * 80},
        ),
        "current task",
        token_budget=small_budget(1000),
    )

    stable_block = result.blocks[0]
    dynamic_block = result.blocks[1]
    assert result.metadata["was_truncated"] is True
    assert stable_block.content == ""
    assert dynamic_block.content == result.metadata["context"]
    assert "[Current request]" in dynamic_block.content
    assert "current task" in dynamic_block.content
    assert stable_block.token_count == result.metadata["stable_prefix_tokens"]
    assert dynamic_block.token_count == result.metadata["dynamic_suffix_tokens"]
    assert result.metadata["turns_included"] == 0
    assert result.metadata["full_turns"] == 0


def test_char_limit_preserves_turn_count_reason(monkeypatch):
    monkeypatch.setattr(assembly, "MAX_CONTEXT_CHARS", 80)

    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "old"),
                turn("t2", "recent " * 100, tokens=5),
            ]
        ),
        "current task",
        token_budget=small_budget(1000),
        max_turns=1,
    )

    assert result.metadata["was_truncated"] is True
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TURN_COUNT_EXCEEDED.value
    )


def test_token_budget_reason_overrides_turn_count_reason():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "old"),
                turn("t2", "recent " * 100, tokens=5),
            ]
        ),
        "current task " * 100,
        token_budget=small_budget(90),
        max_turns=1,
    )

    assert result.metadata["was_truncated"] is True
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TOKEN_BUDGET_EXCEEDED.value
    )


def test_char_limit_preserves_token_budget_reason(monkeypatch):
    monkeypatch.setattr(assembly, "MAX_CONTEXT_CHARS", 80)

    result = assembly.assemble_agent_execution_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "old " * 80, tokens=80),
                turn("t2", "middle " * 80, tokens=80),
                turn("t3", "recent", tokens=5),
            ]
        ),
        "current task " * 100,
        token_budget=small_budget(140),
        agent_name="Agent",
    )

    assert result.metadata["turns_truncated"] >= 1
    assert result.metadata["was_truncated"] is True
    assert "current task" in result.metadata["context"]
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TOKEN_BUDGET_EXCEEDED.value
    )


def test_token_budget_truncation_removes_oldest_turns():
    result = assembly.assemble_agent_execution_context_from_memory(
        room_doc(
            turns=[
                turn("t1", "old " * 80, tokens=80),
                turn("t2", "middle " * 80, tokens=80),
                turn("t3", "recent", tokens=5),
            ]
        ),
        "current task",
        token_budget=small_budget(140),
        agent_name="Agent",
    )

    assert result.metadata["was_truncated"] is True
    assert result.metadata["turns_truncated"] >= 1
    assert "recent" in result.metadata["context"]
    assert "old old" not in result.metadata["context"]


def test_token_budget_hard_cap_truncates_oversized_current_task():
    budget = small_budget(140)

    result = assembly.assemble_agent_execution_context_from_memory(
        room_doc(turns=[]),
        "current task " * 200,
        token_budget=budget,
        agent_name="Agent",
    )

    assert result.total_tokens <= budget.available_for_content
    assert result.metadata["was_truncated"] is True
    assert (
        result.metadata["truncation_reason"]
        == TruncationReason.TOKEN_BUDGET_EXCEEDED.value
    )
    assert result.metadata["context"].endswith("... [context truncated]")


def test_token_budget_truncation_preserves_current_request_when_stable_prefix_is_huge():
    result = assembly.assemble_supervisor_context_from_memory(
        room_doc(room_summary={"current_goal": "old summary " * 500}),
        "do the actual requested work",
        token_budget=small_budget(90),
    )

    assert result.metadata["was_truncated"] is True
    assert "[Current request]" in result.metadata["context"]
    assert "do the actual requested work" in result.metadata["context"]
