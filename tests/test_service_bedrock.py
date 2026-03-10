"""Unit tests for BedrockService (AWS Bedrock Claude API integration).

Tests cover:
1. JSON extraction (clean, markdown-wrapped, embedded)
2. API call success paths
3. Error handling (empty responses, invalid JSON, API errors)
4. Configuration and credentials
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.bedrock_service import BedrockService


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def bedrock_svc():
    """Create BedrockService instance with mocked boto3 session."""
    svc = object.__new__(BedrockService)
    svc._session = MagicMock()
    svc._region = "us-east-1"
    svc._timeout = 45.0
    return svc


def _make_bedrock_response(text: str):
    """Build mock Bedrock API response."""
    body_mock = MagicMock()
    # Mock the async read() method on body
    body_mock.read = AsyncMock(return_value=json.dumps({
        'content': [{'text': text}],
        'usage': {'input_tokens': 100, 'output_tokens': 50}
    }).encode('utf-8'))

    response = {
        'body': body_mock,
        'ResponseMetadata': {'HTTPStatusCode': 200}
    }
    return response


def _make_client_error(code: str, message: str):
    """Build mock ClientError exception."""
    from botocore.exceptions import ClientError
    error_response = {'Error': {'Code': code, 'Message': message}}
    return ClientError(error_response, 'invoke_model')


# ---------------------------------------------------------------------------
# Group 1: _extract_json (JSON parsing logic)
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Test JSON extraction from various Claude response formats."""

    def test_clean_json(self):
        """Clean JSON should parse directly."""
        text = '{"action": "delegate", "reasoning": "Need expertise"}'
        result = BedrockService._extract_json(text)
        assert result['action'] == 'delegate'
        assert result['reasoning'] == 'Need expertise'

    def test_json_with_markdown_code_block(self):
        """JSON wrapped in ```json ... ``` should be extracted."""
        text = '''Here is the response:
```json
{
  "action": "synthesize",
  "reasoning": "All results collected"
}
```
Hope this helps!'''
        result = BedrockService._extract_json(text)
        assert result['action'] == 'synthesize'
        assert result['reasoning'] == 'All results collected'

    def test_json_with_plain_code_block(self):
        """JSON wrapped in ``` ... ``` (no json marker) should be extracted."""
        text = '''```
{"action": "done", "reasoning": "Task complete"}
```'''
        result = BedrockService._extract_json(text)
        assert result['action'] == 'done'

    def test_json_embedded_in_text(self):
        """JSON embedded in explanatory text should be extracted."""
        text = '''Based on the analysis, here is my decision:

{"action": "clarify", "questions": [{"prompt": "What do you prefer?", "prompt_type": "choice"}]}

This will help us proceed better.'''
        result = BedrockService._extract_json(text)
        assert result['action'] == 'clarify'
        assert 'questions' in result

    def test_invalid_json_raises_error(self):
        """Completely invalid JSON should raise ValueError."""
        text = "This is just plain text with no JSON at all"
        with pytest.raises(ValueError, match="No valid JSON found"):
            BedrockService._extract_json(text)

    def test_malformed_json_raises_error(self):
        """JSON with syntax errors should raise ValueError."""
        text = '{"action": "delegate", "reasoning": "incomplete'
        with pytest.raises(ValueError, match="No valid JSON found"):
            BedrockService._extract_json(text)

    def test_json_with_nested_objects(self):
        """Complex nested JSON should parse correctly."""
        text = '''{
  "action": "delegate",
  "targets": [
    {"agent_id": "123", "agent_name": "Writer", "task": "Write content"},
    {"agent_id": "456", "agent_name": "Editor", "task": "Review content"}
  ],
  "reasoning": "Need both skills"
}'''
        result = BedrockService._extract_json(text)
        assert result['action'] == 'delegate'
        assert len(result['targets']) == 2
        assert result['targets'][0]['agent_name'] == 'Writer'


# ---------------------------------------------------------------------------
# Group 2: call_claude_json (JSON mode API calls)
# ---------------------------------------------------------------------------

class TestCallClaudeJson:
    """Test JSON response API calls for supervisor decisions."""

    @pytest.mark.asyncio
    async def test_returns_parsed_dict(self, bedrock_svc):
        """Successful API call should return parsed JSON dict."""
        json_response = '{"action": "delegate", "reasoning": "Need help"}'
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response(json_response)
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        result = await bedrock_svc.call_claude_json(
            system_prompt="You are a supervisor",
            user_prompt="What should I do?",
            model="anthropic.claude-opus-4-6-20250514-v1:0"
        )

        assert isinstance(result, dict)
        assert result['action'] == 'delegate'
        mock_client.invoke_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_strips_markdown_automatically(self, bedrock_svc):
        """Markdown-wrapped JSON should be extracted automatically."""
        json_with_markdown = '''```json
{"action": "synthesize", "synthesis_instruction": "Combine results"}
```'''
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response(json_with_markdown)
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        result = await bedrock_svc.call_claude_json(
            system_prompt="You are a supervisor",
            user_prompt="What next?",
        )

        assert result['action'] == 'synthesize'
        assert 'synthesis_instruction' in result

    @pytest.mark.asyncio
    async def test_uses_default_model_if_not_provided(self, bedrock_svc):
        """Should use settings.bedrock_supervisor_model as default."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('{"action": "done"}')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with patch('services.bedrock_service.settings') as mock_settings:
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"
            await bedrock_svc.call_claude_json(
                system_prompt="Test",
                user_prompt="Test",
            )

        call_args = mock_client.invoke_model.call_args
        assert call_args.kwargs['modelId'] == "anthropic.claude-opus-4-6-20250514-v1:0"

    @pytest.mark.asyncio
    async def test_enhances_system_prompt_for_json(self, bedrock_svc):
        """Should add JSON formatting instructions to system prompt."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('{"action": "done"}')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        await bedrock_svc.call_claude_json(
            system_prompt="You are a supervisor",
            user_prompt="Decide",
        )

        call_args = mock_client.invoke_model.call_args
        request_body = json.loads(call_args.kwargs['body'])
        assert "CRITICAL: Return ONLY valid JSON" in request_body['system']
        assert "You are a supervisor" in request_body['system']

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self, bedrock_svc):
        """Empty response should raise ValueError."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValueError, match="Empty response"):
            await bedrock_svc.call_claude_json(
                system_prompt="Test",
                user_prompt="Test",
            )

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json_response(self, bedrock_svc):
        """Invalid JSON in response should raise ValueError."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('This is not JSON')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValueError, match="No valid JSON found"):
            await bedrock_svc.call_claude_json(
                system_prompt="Test",
                user_prompt="Test",
            )

    @pytest.mark.asyncio
    async def test_handles_bedrock_api_error(self, bedrock_svc):
        """Bedrock API errors should be caught and re-raised as ValueError."""
        mock_client = AsyncMock()
        mock_client.invoke_model.side_effect = _make_client_error(
            'ThrottlingException',
            'Rate limit exceeded'
        )
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValueError, match="Bedrock API error: ThrottlingException"):
            await bedrock_svc.call_claude_json(
                system_prompt="Test",
                user_prompt="Test",
            )


# ---------------------------------------------------------------------------
# Group 3: call_claude_text (text mode API calls)
# ---------------------------------------------------------------------------

class TestCallClaudeText:
    """Test text response API calls for supervisor synthesis."""

    @pytest.mark.asyncio
    async def test_returns_text_string(self, bedrock_svc):
        """Successful API call should return text string."""
        text_response = "Based on all agent responses, the answer is..."
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response(text_response)
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        result = await bedrock_svc.call_claude_text(
            system_prompt="Synthesize results",
            user_prompt="Agent A said X, Agent B said Y",
            model="anthropic.claude-opus-4-6-20250514-v1:0"
        )

        assert isinstance(result, str)
        assert "Based on all agent responses" in result
        mock_client.invoke_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_default_model_if_not_provided(self, bedrock_svc):
        """Should use settings.bedrock_supervisor_model as default."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response("Response text")
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with patch('services.bedrock_service.settings') as mock_settings:
            mock_settings.bedrock_supervisor_model = "anthropic.claude-opus-4-6-20250514-v1:0"
            await bedrock_svc.call_claude_text(
                system_prompt="Test",
                user_prompt="Test",
            )

        call_args = mock_client.invoke_model.call_args
        assert call_args.kwargs['modelId'] == "anthropic.claude-opus-4-6-20250514-v1:0"

    @pytest.mark.asyncio
    async def test_does_not_enhance_system_prompt_for_text(self, bedrock_svc):
        """Text mode should NOT add JSON instructions to system prompt."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response("Response")
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        await bedrock_svc.call_claude_text(
            system_prompt="You are synthesizing",
            user_prompt="Combine",
        )

        call_args = mock_client.invoke_model.call_args
        request_body = json.loads(call_args.kwargs['body'])
        assert "CRITICAL: Return ONLY valid JSON" not in request_body['system']
        assert request_body['system'] == "You are synthesizing"

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self, bedrock_svc):
        """Empty response should raise ValueError."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValueError, match="Empty response"):
            await bedrock_svc.call_claude_text(
                system_prompt="Test",
                user_prompt="Test",
            )

    @pytest.mark.asyncio
    async def test_handles_bedrock_api_error(self, bedrock_svc):
        """Bedrock API errors should be caught and re-raised as ValueError."""
        mock_client = AsyncMock()
        mock_client.invoke_model.side_effect = _make_client_error(
            'ValidationException',
            'Invalid request parameters'
        )
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValueError, match="Bedrock API error: ValidationException"):
            await bedrock_svc.call_claude_text(
                system_prompt="Test",
                user_prompt="Test",
            )


# ---------------------------------------------------------------------------
# Group 4: Configuration and initialization
# ---------------------------------------------------------------------------

class TestBedrockServiceInit:
    """Test BedrockService initialization and configuration."""

    def test_initializes_with_settings_credentials(self):
        """Should initialize session with AWS credentials from settings."""
        with patch('services.bedrock_service.settings') as mock_settings:
            mock_settings.aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"
            mock_settings.aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            mock_settings.bedrock_region = "us-west-2"

            with patch('services.bedrock_service.aioboto3.Session') as mock_session_class:
                svc = BedrockService()

                mock_session_class.assert_called_once_with(
                    aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                    aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                    region_name="us-west-2",
                )

    def test_timeout_is_45_seconds(self):
        """Timeout should be 45s (longer than OpenAI's 30s for larger model)."""
        with patch('services.bedrock_service.aioboto3.Session'):
            svc = BedrockService()
            assert svc._timeout == 45.0

    def test_region_stored_from_settings(self):
        """Region should be stored from settings."""
        with patch('services.bedrock_service.settings') as mock_settings:
            mock_settings.bedrock_region = "ap-southeast-1"
            with patch('services.bedrock_service.aioboto3.Session'):
                svc = BedrockService()
                assert svc._region == "ap-southeast-1"


# ---------------------------------------------------------------------------
# Group 5: Claude Messages API format
# ---------------------------------------------------------------------------

class TestClaudeMessagesAPIFormat:
    """Test that requests conform to Claude Messages API format."""

    @pytest.mark.asyncio
    async def test_request_body_structure(self, bedrock_svc):
        """Request body should match Claude Messages API specification."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('{"action": "done"}')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        await bedrock_svc.call_claude_json(
            system_prompt="System instructions",
            user_prompt="User question",
        )

        call_args = mock_client.invoke_model.call_args
        request_body = json.loads(call_args.kwargs['body'])

        # Verify required fields
        assert request_body['anthropic_version'] == "bedrock-2023-05-31"
        assert request_body['max_tokens'] == 4096
        assert 'system' in request_body
        assert 'messages' in request_body
        assert request_body['temperature'] == 1.0

        # System prompt is separate field, not in messages
        assert isinstance(request_body['system'], str)
        assert "System instructions" in request_body['system']

        # Messages array contains only user message
        assert len(request_body['messages']) == 1
        assert request_body['messages'][0]['role'] == 'user'
        assert request_body['messages'][0]['content'] == 'User question'

    @pytest.mark.asyncio
    async def test_invoke_model_parameters(self, bedrock_svc):
        """invoke_model should be called with correct parameters."""
        mock_client = AsyncMock()
        mock_client.invoke_model.return_value = _make_bedrock_response('{"action": "done"}')
        bedrock_svc._session.client.return_value.__aenter__.return_value = mock_client

        await bedrock_svc.call_claude_json(
            system_prompt="Test",
            user_prompt="Test",
            model="anthropic.claude-opus-4-6-20250514-v1:0"
        )

        call_args = mock_client.invoke_model.call_args
        assert call_args.kwargs['modelId'] == "anthropic.claude-opus-4-6-20250514-v1:0"
        assert call_args.kwargs['contentType'] == "application/json"
        assert call_args.kwargs['accept'] == "application/json"
        assert isinstance(call_args.kwargs['body'], str)  # JSON string
