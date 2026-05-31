"""Test supervisor service routing between OpenAI and Bedrock.

Verifies that the USE_BEDROCK_SUPERVISOR feature flag correctly routes
LLM calls to either Bedrock (Claude Opus 4.6) or OpenAI (gpt-4o-mini).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.room_supervisor_service import RoomSupervisorService


def _text_stream_mock(text: str):
    """Build an async text stream factory for synthesis routing tests."""

    async def _stream(system_prompt: str, user_prompt: str, model: str | None = None):
        yield text

    return MagicMock(side_effect=_stream)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_openai():
    """Mock OpenAIService."""
    svc = AsyncMock()
    svc.call_supervisor_llm_json = AsyncMock(return_value={"action": "done", "reasoning": "OpenAI response"})
    svc.call_supervisor_llm_text_stream = _text_stream_mock("OpenAI synthesis text")
    return svc


@pytest.fixture
def mock_bedrock():
    """Mock BedrockService."""
    svc = AsyncMock()
    svc.call_claude_json = AsyncMock(return_value={"action": "done", "reasoning": "Bedrock response"})
    svc.call_claude_text_stream = _text_stream_mock("Bedrock synthesis text")
    return svc


@pytest.fixture
def mock_db():
    """Mock DatabaseService."""
    return AsyncMock()


@pytest.fixture
def supervisor_svc(mock_openai, mock_bedrock, mock_db):
    """Create RoomSupervisorService with mocked dependencies."""
    svc = RoomSupervisorService(
        openai_service=mock_openai,
        bedrock_service=mock_bedrock,
        database_service=mock_db,
    )
    return svc


# ---------------------------------------------------------------------------
# Test: Feature flag routing for JSON calls
# ---------------------------------------------------------------------------

class TestSupervisorLLMRouting:
    """Test that supervisor LLM calls route correctly based on feature flag."""

    @pytest.mark.asyncio
    async def test_routes_to_openai_when_flag_disabled(self, supervisor_svc, mock_openai, mock_bedrock):
        """When USE_BEDROCK_SUPERVISOR=false, should call OpenAI."""
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = False

            result = await supervisor_svc._call_supervisor_llm(
                system_prompt="Test system",
                user_prompt="Test user",
            )

            # Verify OpenAI was called
            mock_openai.call_supervisor_llm_json.assert_awaited_once_with(
                system_prompt="Test system",
                user_prompt="Test user",
            )

            # Verify Bedrock was NOT called
            mock_bedrock.call_claude_json.assert_not_awaited()

            # Verify response came from OpenAI
            assert result["reasoning"] == "OpenAI response"

    @pytest.mark.asyncio
    async def test_routes_to_bedrock_when_flag_enabled(self, supervisor_svc, mock_openai, mock_bedrock):
        """When USE_BEDROCK_SUPERVISOR=true, should call Bedrock."""
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = True
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"

            result = await supervisor_svc._call_supervisor_llm(
                system_prompt="Test system",
                user_prompt="Test user",
            )

            # Verify Bedrock was called
            mock_bedrock.call_claude_json.assert_awaited_once_with(
                system_prompt="Test system",
                user_prompt="Test user",
                model="anthropic.claude-opus-4-6-20250514-v1:0",
            )

            # Verify OpenAI was NOT called
            mock_openai.call_supervisor_llm_json.assert_not_awaited()

            # Verify response came from Bedrock
            assert result["reasoning"] == "Bedrock response"


# ---------------------------------------------------------------------------
# Test: Feature flag routing for text calls (synthesis)
# ---------------------------------------------------------------------------

class TestSupervisorLLMTextRouting:
    """Test that supervisor text calls route correctly based on feature flag."""

    @pytest.mark.asyncio
    async def test_routes_to_openai_when_flag_disabled(self, supervisor_svc, mock_openai, mock_bedrock):
        """When USE_BEDROCK_SUPERVISOR=false, should call OpenAI."""
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = False

            result = await supervisor_svc._call_supervisor_llm_text(
                system_prompt="Synthesize results",
                user_prompt="Agent A: ..., Agent B: ...",
            )

            # Verify OpenAI stream was called
            mock_openai.call_supervisor_llm_text_stream.assert_called_once_with(
                system_prompt="Synthesize results",
                user_prompt="Agent A: ..., Agent B: ...",
            )

            # Verify Bedrock was NOT called
            mock_bedrock.call_claude_text_stream.assert_not_called()

            # Verify response came from OpenAI
            assert result == "OpenAI synthesis text"

    @pytest.mark.asyncio
    async def test_routes_to_bedrock_when_flag_enabled(self, supervisor_svc, mock_openai, mock_bedrock):
        """When USE_BEDROCK_SUPERVISOR=true, should call Bedrock."""
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = True
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"

            result = await supervisor_svc._call_supervisor_llm_text(
                system_prompt="Synthesize results",
                user_prompt="Agent A: ..., Agent B: ...",
            )

            # Verify Bedrock stream was called
            mock_bedrock.call_claude_text_stream.assert_called_once_with(
                system_prompt="Synthesize results",
                user_prompt="Agent A: ..., Agent B: ...",
                model="anthropic.claude-opus-4-6-20250514-v1:0",
            )

            # Verify OpenAI was NOT called
            mock_openai.call_supervisor_llm_text_stream.assert_not_called()

            # Verify response came from Bedrock
            assert result == "Bedrock synthesis text"


# ---------------------------------------------------------------------------
# Test: Multiple sequential calls maintain routing
# ---------------------------------------------------------------------------

class TestRoutingConsistency:
    """Test that routing remains consistent across multiple calls."""

    @pytest.mark.asyncio
    async def test_multiple_calls_use_same_backend(self, supervisor_svc, mock_openai, mock_bedrock):
        """Multiple calls should consistently use the same backend."""
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = True
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"

            # First call (JSON)
            await supervisor_svc._call_supervisor_llm(
                system_prompt="System 1",
                user_prompt="User 1",
            )

            # Second call (text)
            await supervisor_svc._call_supervisor_llm_text(
                system_prompt="System 2",
                user_prompt="User 2",
            )

            # Third call (JSON)
            await supervisor_svc._call_supervisor_llm(
                system_prompt="System 3",
                user_prompt="User 3",
            )

            # Verify all calls went to Bedrock
            assert mock_bedrock.call_claude_json.await_count == 2
            assert mock_bedrock.call_claude_text_stream.call_count == 1

            # Verify no calls went to OpenAI
            mock_openai.call_supervisor_llm_json.assert_not_awaited()
            mock_openai.call_supervisor_llm_text_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_flag_change_switches_backend(self, supervisor_svc, mock_openai, mock_bedrock):
        """Changing flag should switch backend for subsequent calls."""
        # First call with Bedrock
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = True
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"

            await supervisor_svc._call_supervisor_llm(
                system_prompt="System 1",
                user_prompt="User 1",
            )

        # Second call with OpenAI (flag changed)
        with patch('config.settings.settings') as mock_settings:
            mock_settings.use_bedrock_supervisor = False

            await supervisor_svc._call_supervisor_llm(
                system_prompt="System 2",
                user_prompt="User 2",
            )

        # Verify both were called once
        assert mock_bedrock.call_claude_json.await_count == 1
        assert mock_openai.call_supervisor_llm_json.await_count == 1
