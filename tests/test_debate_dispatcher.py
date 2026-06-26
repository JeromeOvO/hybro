"""Unit tests for SequentialDebateDispatcher."""

from __future__ import annotations

from unittest.mock import patch

from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from execution.orchestration.debate_prompt_injector import DebatePromptInjector


class TestSequentialDebateDispatcher:
    """Test suite for SequentialDebateDispatcher."""

    def test_first_agent_no_prior_returns_unchanged(self):
        """First agent (no prior) gets raw task unchanged."""
        original = "Analyze the market trends"
        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=None,
            prior_response=None,
        )
        assert result == original

    def test_subsequent_agent_gets_enriched_prompt(self):
        """Subsequent agent gets enriched prompt with prior agent response."""
        original = "Analyze the market trends"
        prior_name = "MarketAnalyst"
        prior_response = "The market shows bullish trends in Q1."

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
        )

        assert "YOUR TASK: Analyze the market trends" in result
        assert f"RESPONSE FROM PREVIOUS AGENT ({prior_name})" in result
        assert prior_response in result
        assert "END PREVIOUS RESPONSE" in result
        assert "DEBATE MODE INSTRUCTIONS" in result

    def test_truncation_at_max_chars(self):
        """Truncation at 3000 chars with truncation marker."""
        original = "Analyze data"
        prior_name = "DataAgent"
        # Create a response longer than 3000 chars
        prior_response = "A" * 3500

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
            max_chars=3000,
        )

        # Should contain truncated content
        assert "A" * 3000 in result
        # Should have truncation marker
        assert "[truncated — full response: 3500 chars]" in result
        # Should not contain all 3500 A's
        assert "A" * 3500 not in result

    def test_empty_prior_response_returns_raw_task(self):
        """Empty prior_response returns raw task."""
        original = "Do something"
        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name="Agent1",
            prior_response="",
        )
        assert result == original

    def test_none_prior_agent_name_uses_fallback(self):
        """None prior_agent_name uses 'Previous Agent' fallback to preserve debate context."""
        original = "Do something"
        prior_response = "Some response from a deleted agent"
        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=None,
            prior_response=prior_response,
        )
        # Should still include debate context with fallback name
        assert "RESPONSE FROM PREVIOUS AGENT (Previous Agent)" in result
        assert prior_response in result
        assert "YOUR TASK: Do something" in result

    def test_prompt_contains_all_expected_sections(self):
        """Prompt contains all expected sections."""
        original = "Task X"
        prior_name = "AgentY"
        prior_response = "Response Y"

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
        )

        # Check all expected sections
        assert "YOUR TASK:" in result
        assert "=== RESPONSE FROM PREVIOUS AGENT" in result
        assert "=== END PREVIOUS RESPONSE ===" in result
        assert "DEBATE MODE INSTRUCTIONS:" in result
        assert "Review the previous agent's response above" in result
        assert "Provide your own perspective" in result
        assert "Focus on adding value" in result
        assert "Execute your task and deliver concrete results" in result


def test_debate_prompt_injector_default_constructor_does_not_import_database_service():
    with patch("importlib.import_module", side_effect=AssertionError("legacy import attempted")):
        injector = DebatePromptInjector()

    assert injector._message_store is not None

    def test_prior_response_exactly_at_max_chars_boundary(self):
        """Prior response exactly at max_chars boundary (no truncation marker)."""
        original = "Analyze"
        prior_name = "Agent"
        # Exactly 3000 chars
        prior_response = "B" * 3000

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
            max_chars=3000,
        )

        # Should contain all 3000 B's
        assert "B" * 3000 in result
        # Should NOT have truncation marker
        assert "[truncated" not in result
        assert "full response:" not in result

    def test_custom_max_chars(self):
        """Test with custom max_chars parameter."""
        original = "Task"
        prior_name = "Agent"
        prior_response = "X" * 500

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
            max_chars=100,
        )

        # Should be truncated at 100 chars
        assert "X" * 100 in result
        assert "[truncated — full response: 500 chars]" in result

    def test_multiline_prior_response(self):
        """Test with multiline prior response."""
        original = "Review the code"
        prior_name = "CodeReviewer"
        prior_response = "Line 1: Good\nLine 2: Needs improvement\nLine 3: Excellent"

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
        )

        # Should preserve newlines in prior response
        assert "Line 1: Good\nLine 2: Needs improvement\nLine 3: Excellent" in result

    def test_prior_response_with_special_characters(self):
        """Test prior response with special characters."""
        original = "Analyze text"
        prior_name = "TextAnalyzer"
        prior_response = 'Text with "quotes" and \'apostrophes\' and special chars: $%^&*()'

        result = SequentialDebateDispatcher.build_debate_prompt(
            original_task=original,
            prior_agent_name=prior_name,
            prior_response=prior_response,
        )

        # Should preserve special characters
        assert 'Text with "quotes" and \'apostrophes\' and special chars: $%^&*()' in result
