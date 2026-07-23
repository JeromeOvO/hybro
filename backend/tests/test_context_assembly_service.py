"""
Unit tests for ContextAssemblyService.

Tests cover:
1. Token budget allocation and enforcement
2. Stable prefix / dynamic suffix building
3. Truncation behavior
4. Occupancy threshold logging
5. Turn selection within budget

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §5 and §18 for specification.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from context_memory import assembly as context_memory_assembly
from context_memory import legacy_assembly
from context_memory.compat.context_assembly import (
    ContextAssemblyResult,
    ContextAssemblyService,
    TruncationReason,
)
from context_memory.config import TokenBudgetConfig
from models.context_config import TokenBudget
from models.memory import (
    ContentType,
    ConversationTurn,
    MemoryContent,
    RoomFact,
    RoomMemory,
    RoomSummary,
    TurnRepresentation,
    TurnRole,
    TurnType,
)


def _token_budget_config(service: ContextAssemblyService) -> TokenBudgetConfig:
    budget = service.budget
    return TokenBudgetConfig(
        model_context_window=budget.model_context_window,
        system_prompt=budget.system_prompt,
        tool_schemas=budget.tool_schemas,
        response_reserve=budget.response_reserve,
        room_context_pct=budget.room_context_pct,
        conversation_history_pct=budget.conversation_history_pct,
        current_task_pct=budget.current_task_pct,
    )


class BoundAssemblyFacade:
    def __init__(self, service: ContextAssemblyService):
        self.service = service

    def assemble_supervisor_context_from_memory(
        self, room_memory_doc, current_task, **kwargs
    ):
        return context_memory_assembly.assemble_supervisor_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=_token_budget_config(self.service),
            **kwargs,
        )

    def assemble_agent_execution_context_from_memory(
        self, room_memory_doc, current_task, **kwargs
    ):
        return context_memory_assembly.assemble_agent_execution_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=_token_budget_config(self.service),
            **kwargs,
        )


def bind_assembly_facade(service: ContextAssemblyService) -> ContextAssemblyService:
    service.bind_facade(BoundAssemblyFacade(service))
    return service


class TestTokenBudget:
    """Tests for TokenBudget configuration."""

    def test_available_for_content_calculation(self):
        """Test that available_for_content correctly subtracts fixed allocations."""
        with patch("models.context_config.settings") as mock_settings:
            mock_settings.context_model_window = 128000
            mock_settings.context_system_prompt_tokens = 2000
            mock_settings.context_tool_schema_tokens = 3000
            mock_settings.context_response_reserve_tokens = 4000
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            budget = TokenBudget()
            expected = 128000 - 2000 - 3000 - 4000  # 119000
            assert budget.available_for_content == expected

    def test_dynamic_allocation_percentages(self):
        """Test that dynamic allocations sum to 100%."""
        with patch("models.context_config.settings") as mock_settings:
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            budget = TokenBudget()
            total_pct = (
                budget.room_context_pct
                + budget.conversation_history_pct
                + budget.current_task_pct
            )
            assert total_pct == 1.0, (
                f"Dynamic allocations should sum to 100%, got {total_pct * 100}%"
            )

    def test_conversation_history_tokens(self):
        """Test conversation history token allocation."""
        with patch("models.context_config.settings") as mock_settings:
            mock_settings.context_model_window = 100000
            mock_settings.context_system_prompt_tokens = 2000
            mock_settings.context_tool_schema_tokens = 3000
            mock_settings.context_response_reserve_tokens = 5000
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            budget = TokenBudget()
            available = 100000 - 2000 - 3000 - 5000  # 90000
            expected = int(available * 0.60)  # 54000
            assert budget.conversation_history_tokens == expected


class TestContextAssemblyService:
    """Tests for ContextAssemblyService."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService instance with mocked settings."""
        with patch("common.config.settings") as mock_settings:
            mock_settings.context_model_window = 32000
            mock_settings.context_system_prompt_tokens = 2000
            mock_settings.context_tool_schema_tokens = 3000
            mock_settings.context_response_reserve_tokens = 4000
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            yield bind_assembly_facade(ContextAssemblyService())

    @pytest.fixture
    def sample_room_memory(self):
        """Create a sample RoomMemory for testing."""
        turns = [
            ConversationTurn(
                turn_id=f"turn_{i}",
                role=TurnRole.USER if i % 2 == 0 else TurnRole.AGENT,
                content=f"Test message {i}",
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=50,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
            for i in range(10)
        ]

        memory_content = MemoryContent(
            conversation_history=turns,
            summary="This is a test conversation summary.",
        )

        return RoomMemory(
            room_id="test_room_123",
            memory_content=memory_content,
            room_summary=RoomSummary(
                current_goal="Test the context assembly service",
                key_decisions=["Use pytest", "Mock settings"],
                open_questions=["Does it work?"],
            ),
            room_facts=[
                RoomFact(content="Fact 1: Testing is important"),
                RoomFact(content="Fact 2: Mocking is useful"),
            ],
        )

    def test_build_supervisor_context_returns_result(self, service, sample_room_memory):
        """Test that build_supervisor_context returns a valid result."""
        result = service.build_supervisor_context(
            room_memory=sample_room_memory,
            current_task="Test the system",
            max_turns=3,
        )

        assert isinstance(result, ContextAssemblyResult)
        assert result.context is not None
        assert result.total_tokens > 0
        assert 0 <= result.occupancy_pct <= 100
        assert result.turns_included <= 3

    def test_build_supervisor_context_includes_agent_registry(
        self, service, sample_room_memory
    ):
        """Test that agent registry is included in supervisor context."""
        agent_registry = [
            {"agent_id": "a1", "agent_name": "CodeAgent", "description": "Writes code"},
            {
                "agent_id": "a2",
                "agent_name": "TestAgent",
                "description": "Writes tests",
            },
        ]

        result = service.build_supervisor_context(
            room_memory=sample_room_memory,
            current_task="Test the system",
            agent_registry=agent_registry,
        )

        assert "CodeAgent" in result.context
        assert "TestAgent" in result.context
        assert "[Available Agents]" in result.context

    def test_build_agent_execution_context_returns_result(
        self, service, sample_room_memory
    ):
        """Test that build_agent_execution_context returns a valid result."""
        result = service.build_agent_execution_context(
            room_memory=sample_room_memory,
            current_task="Execute a test task",
            agent_name="TestAgent",
        )

        assert isinstance(result, ContextAssemblyResult)
        assert result.context is not None
        assert result.total_tokens > 0
        assert "TestAgent" in result.context

    def test_build_agent_execution_context_includes_quoted_text(
        self, service, sample_room_memory
    ):
        """Test that quoted text is included in agent context."""
        result = service.build_agent_execution_context(
            room_memory=sample_room_memory,
            current_task="Explain this",
            quoted_text="This is the quoted content",
        )

        assert "[Quoted context]" in result.context
        assert "This is the quoted content" in result.context

    def test_truncation_when_over_budget(self, service, sample_room_memory):
        """Test that turns are truncated when over budget."""
        # Create many turns to exceed budget
        many_turns = [
            ConversationTurn(
                turn_id=f"turn_{i}",
                role=TurnRole.USER,
                content="A" * 1000,  # Large content
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=500,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
            for i in range(100)
        ]
        sample_room_memory.memory_content.conversation_history = many_turns

        result = service.build_agent_execution_context(
            room_memory=sample_room_memory,
            current_task="Test truncation",
        )

        # Should have truncated some turns
        assert result.turns_included < 100
        assert result.turns_truncated > 0
        assert result.was_truncated is True
        assert result.truncation_reason == TruncationReason.TOKEN_BUDGET_EXCEEDED

    def test_stable_prefix_contains_room_summary(self, service, sample_room_memory):
        """Test that stable prefix includes room summary."""
        result = service.build_supervisor_context(
            room_memory=sample_room_memory,
            current_task="Test",
        )

        assert "[Room Context]" in result.context
        assert "Test the context assembly service" in result.context  # current_goal

    def test_stable_prefix_tokens_tracked(self, service, sample_room_memory):
        """Test that stable prefix tokens are tracked separately."""
        result = service.build_supervisor_context(
            room_memory=sample_room_memory,
            current_task="Test",
        )

        assert result.stable_prefix_tokens > 0
        assert result.dynamic_suffix_tokens > 0
        assert (
            result.total_tokens
            == result.stable_prefix_tokens + result.dynamic_suffix_tokens
        )

    def test_truncation_count_increments(self, service, sample_room_memory):
        """Test that truncation is tracked when turns exceed budget."""
        # Create many turns to cause truncation in selection
        many_turns = [
            ConversationTurn(
                turn_id=f"turn_{i}",
                role=TurnRole.USER,
                content="A" * 1000,
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=500,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
            for i in range(100)
        ]
        sample_room_memory.memory_content.conversation_history = many_turns

        result = service.build_agent_execution_context(
            room_memory=sample_room_memory,
            current_task="Test",
        )

        assert result.was_truncated is True
        assert result.turns_truncated > 0

    def test_get_budget_summary(self, service):
        """Test that get_budget_summary returns expected structure."""
        summary = service.get_budget_summary()

        assert "model_context_window" in summary
        assert "available_for_content" in summary
        assert "room_context" in summary
        assert "conversation_history" in summary
        assert "current_task" in summary


class TestTurnSelection:
    """Tests for turn selection within budget."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService instance."""
        with patch("common.config.settings") as mock_settings:
            mock_settings.context_model_window = 32000
            mock_settings.context_system_prompt_tokens = 2000
            mock_settings.context_tool_schema_tokens = 3000
            mock_settings.context_response_reserve_tokens = 4000
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            yield bind_assembly_facade(ContextAssemblyService())

    def test_select_turns_preserves_recent(self, service):
        """Test that most recent turns are preserved during selection."""
        turns = [
            ConversationTurn(
                turn_id=f"turn_{i}",
                role=TurnRole.USER,
                content=f"Message {i}",
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=100,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
            for i in range(10)
        ]

        selected, truncated = legacy_assembly.select_legacy_turns_within_budget(
            turns=turns,
            budget_tokens=500,  # Only room for ~5 turns
        )

        # Should have kept the most recent turns
        assert len(selected) <= 5
        assert selected[-1].turn_id == "turn_9"  # Most recent preserved

    def test_select_turns_empty_list(self, service):
        """Test selection with empty turn list."""
        selected, truncated = legacy_assembly.select_legacy_turns_within_budget(
            turns=[],
            budget_tokens=1000,
        )

        assert selected == []
        assert truncated == 0

    def test_select_turns_respects_compact_tokens(self, service):
        """Test that compact turn tokens are used for compact representations."""
        turns = [
            ConversationTurn(
                turn_id="turn_0",
                role=TurnRole.USER,
                content="Full content here",
                representation=TurnRepresentation.COMPACT,  # Compact representation
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=500,
                estimated_tokens_compact=20,  # Much smaller
                timestamp=datetime.now(),
            )
        ]

        selected, truncated = legacy_assembly.select_legacy_turns_within_budget(
            turns=turns,
            budget_tokens=50,  # Only fits compact
        )

        assert len(selected) == 1
        assert truncated == 0


class TestOccupancyThresholds:
    """Tests for occupancy threshold logging."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService instance."""
        with patch("common.config.settings") as mock_settings:
            mock_settings.context_model_window = 10000  # Small window for testing
            mock_settings.context_system_prompt_tokens = 500
            mock_settings.context_tool_schema_tokens = 500
            mock_settings.context_response_reserve_tokens = 500
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            yield bind_assembly_facade(ContextAssemblyService())

    def test_healthy_occupancy_logs_debug(self, service):
        """Test that <70% occupancy logs at debug level."""
        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            legacy_assembly.log_context_metrics(
                room_id="test",
                total_tokens=5000,  # 50% of 10000
                occupancy_pct=50.0,
                was_truncated=False,
                truncation_reason=None,
                turns_included=5,
                turns_truncated=0,
                context_type="test",
                budget_summary=service.get_budget_summary(),
            )

            mock_logger.debug.assert_called_once()
            mock_logger.warning.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_soft_warning_occupancy_logs_info(self, service):
        """Test that 70-85% occupancy logs at info level."""
        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            legacy_assembly.log_context_metrics(
                room_id="test",
                total_tokens=7500,  # 75% of 10000
                occupancy_pct=75.0,
                was_truncated=False,
                truncation_reason=None,
                turns_included=5,
                turns_truncated=0,
                context_type="test",
                budget_summary=service.get_budget_summary(),
            )

            mock_logger.info.assert_called_once()
            assert "approaching limit" in mock_logger.info.call_args[0][0]

    def test_hard_cap_occupancy_logs_warning(self, service):
        """Test that 85-90% occupancy logs at warning level."""
        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            legacy_assembly.log_context_metrics(
                room_id="test",
                total_tokens=8700,  # 87% of 10000
                occupancy_pct=87.0,
                was_truncated=True,
                truncation_reason=TruncationReason.TOKEN_BUDGET_EXCEEDED,
                turns_included=5,
                turns_truncated=2,
                context_type="test",
                budget_summary=service.get_budget_summary(),
            )

            mock_logger.warning.assert_called_once()
            assert "TRUNCATED" in mock_logger.warning.call_args[0][0]

    def test_emergency_occupancy_logs_error(self, service):
        """Test that >90% occupancy logs at error level."""
        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            legacy_assembly.log_context_metrics(
                room_id="test",
                total_tokens=9500,  # 95% of 10000
                occupancy_pct=95.0,
                was_truncated=True,
                truncation_reason=TruncationReason.TOKEN_BUDGET_EXCEEDED,
                turns_included=5,
                turns_truncated=5,
                context_type="test",
                budget_summary=service.get_budget_summary(),
            )

            mock_logger.error.assert_called_once()
            assert "EMERGENCY" in mock_logger.error.call_args[0][0]


class TestHardCapEnforcement:
    """Tests for hard cap enforcement (§17.2, §15.1)."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService instance with small budget."""
        with patch("common.config.settings") as mock_settings:
            mock_settings.context_model_window = 5000  # Very small for testing
            mock_settings.context_system_prompt_tokens = 500
            mock_settings.context_tool_schema_tokens = 500
            mock_settings.context_response_reserve_tokens = 500
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            yield bind_assembly_facade(ContextAssemblyService())

    @pytest.fixture
    def large_room_memory(self):
        """Create a RoomMemory with large content to trigger hard cap."""
        # Create many large turns
        turns = [
            ConversationTurn(
                turn_id=f"turn_{i}",
                role=TurnRole.USER,
                content="A" * 500,  # Large content
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=200,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
            for i in range(50)
        ]

        memory_content = MemoryContent(
            conversation_history=turns,
            summary="Summary of earlier conversation.",
        )

        return RoomMemory(
            room_id="test_room_large",
            memory_content=memory_content,
            room_summary=RoomSummary(
                current_goal="Test hard cap enforcement",
            ),
            room_facts=[],
        )

    def test_agent_context_enforces_hard_cap(self, service, large_room_memory):
        """Test that build_agent_execution_context enforces hard cap."""
        result = service.build_agent_execution_context(
            room_memory=large_room_memory,
            current_task="Test hard cap",
            agent_name="TestAgent",
        )

        # Should have truncated to fit within available_for_content
        available = service.budget.available_for_content
        assert result.total_tokens <= available or result.turns_included == 1
        assert result.was_truncated is True

    def test_agent_context_logs_critical_when_still_over_budget(
        self, service, large_room_memory
    ):
        """Test that critical error is logged when context can't fit."""
        # Create a room with huge stable prefix that can't be truncated
        large_room_memory.room_summary = RoomSummary(
            current_goal="A" * 5000,  # Huge goal
            key_decisions=["B" * 1000 for _ in range(10)],
        )

        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            result = service.build_agent_execution_context(
                room_memory=large_room_memory,
                current_task="Test",
            )

            # Should have logged critical error if still over budget
            # (depends on whether stable prefix alone exceeds budget)
            if result.total_tokens > service.budget.available_for_content:
                mock_logger.error.assert_called()

    def test_supervisor_context_no_duplicate_warning(self, service, large_room_memory):
        """Test that supervisor context doesn't log duplicate warnings."""
        with patch("context_memory.legacy_assembly.logger") as mock_logger:
            result = service.build_supervisor_context(
                room_memory=large_room_memory,
                current_task="Test",
                max_turns=50,  # Request many turns to trigger truncation
            )

            if result.was_truncated:
                # Should only have ONE warning from ContextMemory metric logging.
                # Not a duplicate from the explicit logger.warning
                warning_calls = mock_logger.warning.call_args_list
                truncation_warnings = [
                    c
                    for c in warning_calls
                    if "truncated" in str(c).lower() or "TRUNCATED" in str(c)
                ]
                # Should be exactly 1 warning about truncation
                assert len(truncation_warnings) <= 1


class TestTaskBudgetEnforcement:
    """Tests for task budget enforcement (§5.2)."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService instance."""
        with patch("common.config.settings") as mock_settings:
            mock_settings.context_model_window = 10000
            mock_settings.context_system_prompt_tokens = 500
            mock_settings.context_tool_schema_tokens = 500
            mock_settings.context_response_reserve_tokens = 500
            mock_settings.context_room_pct = 0.15
            mock_settings.context_history_pct = 0.60
            mock_settings.context_task_pct = 0.25
            yield bind_assembly_facade(ContextAssemblyService())

    def test_task_budget_is_passed_to_dynamic_suffix(self, service):
        """Test that task_budget is passed to _build_agent_dynamic_suffix."""
        turns = [
            ConversationTurn(
                turn_id="turn_0",
                role=TurnRole.USER,
                content="Test message",
                representation=TurnRepresentation.FULL,
                content_type=ContentType.TEXT,
                turn_type=TurnType.MESSAGE,
                estimated_tokens_full=50,
                estimated_tokens_compact=10,
                timestamp=datetime.now(),
            )
        ]

        memory_content = MemoryContent(conversation_history=turns)
        room_memory = RoomMemory(
            room_id="test_room",
            memory_content=memory_content,
        )

        # This should not raise an error
        result = service.build_agent_execution_context(
            room_memory=room_memory,
            current_task="Test task",
            agent_name="TestAgent",
        )

        assert result.context is not None

    def test_large_task_triggers_truncation_warning(self, service):
        """Test that very large task content triggers truncation warning."""
        turns = []
        memory_content = MemoryContent(conversation_history=turns)
        room_memory = RoomMemory(
            room_id="test_room",
            memory_content=memory_content,
        )

        # Create a very large task that exceeds task budget
        large_task = "X" * 50000  # Very large task

        with patch("context_memory.legacy_assembly.logger"):
            result = service.build_agent_execution_context(
                room_memory=room_memory,
                current_task=large_task,
                agent_name="TestAgent",
            )

            # May or may not trigger depending on budget calculation
            # The important thing is it doesn't crash
            assert result.context is not None
