"""AWS Bedrock integration for Claude API (Supervisor LLM).

Provides Claude Opus 4.6 inference via AWS Bedrock for supervisor mode reasoning.
Mirrors the interface of OpenAIService supervisor methods for drop-in replacement.

Why Bedrock for Supervisor:
- Superior reasoning and planning capabilities (Opus 4.6)
- Unified AWS infrastructure (S3 already configured)
- Feature-flagged for easy rollback

Key differences from OpenAI:
- No native JSON mode → manual extraction with markdown stripping
- System prompt is separate field in request (not in messages array)
- Longer timeout (45s vs 30s) to accommodate larger model

See implementation plan for full architecture details.
"""

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from common.utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__)


class BedrockService:
    """AWS Bedrock client for Claude API calls.

    Responsibilities:
    1. Convert OpenAI message format → Claude Messages API format
    2. Handle JSON extraction (Claude doesn't have native JSON mode)
    3. Provide supervisor LLM interface compatible with OpenAIService
    """

    def __init__(self):
        """Initialize Bedrock client with AWS credentials from settings."""
        self._session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.bedrock_region,
        )
        self._region = settings.bedrock_region
        self._timeout = 45.0  # Opus is larger, needs more time than OpenAI's 30s

    async def call_claude_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict:
        """Call Claude via Bedrock and return JSON response.

        Used for supervisor action decisions (DELEGATE, SYNTHESIZE, CLARIFY, DONE).

        Args:
            system_prompt: The system prompt for Claude
            user_prompt: The user prompt for Claude
            model: Optional model override (defaults to settings.bedrock_supervisor_model)

        Returns:
            Parsed JSON response as dict

        Raises:
            ValueError: If response is empty or invalid JSON
            ClientError: If Bedrock API call fails
        """
        model_id = model or settings.bedrock_supervisor_model

        # Add explicit JSON instruction since Claude doesn't have native JSON mode
        enhanced_system_prompt = (
            f"{system_prompt}\n\n"
            "CRITICAL: Return ONLY valid JSON. No markdown, no explanations. "
            "Start with { and end with }. Do not wrap in ```json blocks."
        )

        # Build Claude Messages API request
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": enhanced_system_prompt,  # Separate field, not in messages
            "messages": [
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 1.0,
        }

        try:
            logger.info(
                "Bedrock call_claude_json - model=%s, timeout=%.1fs",
                model_id,
                self._timeout,
            )

            async with self._session.client(
                "bedrock-runtime",
                region_name=self._region,
            ) as client:
                response = await client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(request_body),
                    contentType="application/json",
                    accept="application/json",
                )

                # Parse response body
                body_bytes = await response['body'].read()
                response_body = json.loads(body_bytes)
                content = response_body['content'][0]['text']

                if not content:
                    raise ValueError("Empty response from Bedrock Claude API")

                # Extract JSON from response (handles markdown wrapping)
                json_data = self._extract_json(content)

                logger.info(
                    "Bedrock call_claude_json completed - action=%s",
                    json_data.get('action', 'unknown'),
                )

                return json_data

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(
                "Bedrock API error - code=%s, message=%s",
                error_code,
                error_message,
            )
            raise ValueError(f"Bedrock API error: {error_code} - {error_message}") from e

        except (KeyError, json.JSONDecodeError) as e:
            logger.error("Failed to parse Bedrock response: %s", str(e))
            raise ValueError(f"Invalid Bedrock response format: {str(e)}") from e

    async def call_claude_text(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        """Call Claude via Bedrock and return text response.

        Used for supervisor synthesis (combining agent results into unified answer).

        Args:
            system_prompt: The system prompt for Claude
            user_prompt: The user prompt for Claude
            model: Optional model override (defaults to settings.bedrock_supervisor_model)

        Returns:
            Text response string

        Raises:
            ValueError: If response is empty
            ClientError: If Bedrock API call fails
        """
        model_id = model or settings.bedrock_supervisor_model

        # Build Claude Messages API request
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 1.0,
        }

        try:
            logger.info(
                "Bedrock call_claude_text - model=%s, timeout=%.1fs",
                model_id,
                self._timeout,
            )

            async with self._session.client(
                "bedrock-runtime",
                region_name=self._region,
            ) as client:
                response = await client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(request_body),
                    contentType="application/json",
                    accept="application/json",
                )

                # Parse response body
                body_bytes = await response['body'].read()
                response_body = json.loads(body_bytes)
                content = response_body['content'][0]['text']

                if not content:
                    raise ValueError("Empty response from Bedrock Claude API")

                logger.info(
                    "Bedrock call_claude_text completed - length=%d chars",
                    len(content),
                )

                return content

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(
                "Bedrock API error - code=%s, message=%s",
                error_code,
                error_message,
            )
            raise ValueError(f"Bedrock API error: {error_code} - {error_message}") from e

        except (KeyError, json.JSONDecodeError) as e:
            logger.error("Failed to parse Bedrock response: %s", str(e))
            raise ValueError(f"Invalid Bedrock response format: {str(e)}") from e

    async def call_claude_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream Claude text deltas via Bedrock invoke_model_with_response_stream."""
        model_id = model or settings.bedrock_supervisor_model
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 1.0,
        }

        try:
            logger.info(
                "Bedrock call_claude_text_stream - model=%s, timeout=%.1fs",
                model_id,
                self._timeout,
            )
            async with self._session.client(
                "bedrock-runtime",
                region_name=self._region,
            ) as client:
                response = await client.invoke_model_with_response_stream(
                    modelId=model_id,
                    body=json.dumps(request_body),
                    contentType="application/json",
                    accept="application/json",
                )
                stream = response.get("body")
                if stream is None:
                    return

                async for event in stream:
                    chunk_bytes = event.get("chunk", {}).get("bytes")
                    if not chunk_bytes:
                        continue
                    chunk = json.loads(chunk_bytes)
                    if chunk.get("type") != "content_block_delta":
                        continue
                    delta = chunk.get("delta") or {}
                    text = delta.get("text")
                    if text:
                        yield text

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            logger.error(
                "Bedrock streaming API error - code=%s, message=%s",
                error_code,
                error_message,
            )
            raise ValueError(
                f"Bedrock API error: {error_code} - {error_message}"
            ) from e

        except (KeyError, json.JSONDecodeError) as e:
            logger.error("Failed to parse Bedrock stream chunk: %s", str(e))
            raise ValueError(f"Invalid Bedrock stream format: {str(e)}") from e

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from Claude response, handling markdown wrapping.

        Claude sometimes wraps JSON in ```json ... ``` blocks despite instructions.
        This method handles:
        1. Clean JSON (just parse it)
        2. Markdown-wrapped JSON (strip code blocks)
        3. Text with embedded JSON (find first { to last })

        Args:
            text: Raw text response from Claude

        Returns:
            Parsed JSON as dict

        Raises:
            ValueError: If no valid JSON can be extracted
        """
        text = text.strip()

        # Case 1: Markdown code block
        if "```json" in text or "```" in text:
            # Try to extract from ```json ... ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
            if match:
                json_text = match.group(1).strip()
                try:
                    return json.loads(json_text)
                except json.JSONDecodeError:
                    pass  # Fall through to Case 3

        # Case 2: Clean JSON (starts with {)
        if text.startswith('{'):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass  # Fall through to Case 3

        # Case 3: Find first { to last }
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            json_text = text[start:end + 1]
            try:
                return json.loads(json_text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse JSON from response: {str(e)}") from e

        # No valid JSON found
        raise ValueError(f"No valid JSON found in response: {text[:200]}...")


# Singleton instance
bedrock_service = BedrockService()
