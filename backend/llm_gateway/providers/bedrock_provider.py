import json
from typing import Any

import aioboto3

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from llm_gateway.structured_generation import with_json_object_instruction


class BedrockProvider:
    def __init__(
        self,
        session: Any | None = None,
        region: str | None = None,
    ) -> None:
        self._region = region or settings.bedrock_region
        self._session = session or aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=self._region,
        )

    async def generate(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        model_id = model
        system, bedrock_messages = _to_bedrock_messages(messages)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": kwargs.pop("max_tokens", 4096),
            "system": system,
            "messages": bedrock_messages,
            "temperature": kwargs.pop("temperature", 1.0),
            **kwargs,
        }
        async with self._session.client(
            "bedrock-runtime",
            region_name=self._region,
        ) as client:
            response = await client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            raw = json.loads((await response["body"].read()).decode())
        content = _bedrock_content(raw)
        return LLMResponse(
            content=content,
            model=model_id,
            usage=_bedrock_usage(raw),
            raw_response=raw,
        )

    async def generate_structured(
        self,
        messages: list[dict],
        *args,
        model: str | None = None,
        schema: dict | None = None,
        json_mode: bool = False,
        **kwargs,
    ) -> LLMStructuredResponse:
        model, schema = _normalize_structured_args(
            args,
            model,
            schema,
        )
        structured_messages = (
            with_json_object_instruction(messages)
            if schema is None and json_mode
            else _with_schema_instruction(messages, schema or {})
        )
        response = await self.generate(
            structured_messages,
            model=model,
            **kwargs,
        )
        return LLMStructuredResponse(
            data=_extract_json(response.content),
            model=response.model,
            usage=response.usage,
            raw_response=response.raw_response,
        )

    async def generate_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ):
        system, bedrock_messages = _to_bedrock_messages(messages)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": kwargs.pop("max_tokens", 4096),
            "system": system,
            "messages": bedrock_messages,
            "temperature": kwargs.pop("temperature", 1.0),
            **kwargs,
        }
        async with self._session.client(
            "bedrock-runtime",
            region_name=self._region,
        ) as client:
            response = await client.invoke_model_with_response_stream(
                modelId=model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            stream = response.get("body")
            if stream is None:
                return
            async for event in stream:
                stream_error = _bedrock_stream_error(event)
                if stream_error is not None:
                    raise ValueError(stream_error)
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

    async def embed(self, text: str, model: str) -> list[float]:
        raise NotImplementedError("BedrockProvider does not support embeddings")

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        raise NotImplementedError("BedrockProvider does not support embeddings")


def _to_bedrock_messages(messages: list[dict]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    bedrock_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        content = _content_text(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
        else:
            mapped_role = "assistant" if role == "assistant" else "user"
            content_block = [{"type": "text", "text": content}]
            if bedrock_messages and bedrock_messages[-1]["role"] == mapped_role:
                bedrock_messages[-1]["content"].extend(content_block)
            else:
                bedrock_messages.append({"role": mapped_role, "content": content_block})
    if not bedrock_messages:
        bedrock_messages.append(
            {"role": "user", "content": [{"type": "text", "text": ""}]}
        )
    return "\n\n".join(part for part in system_parts if part), bedrock_messages


def _with_schema_instruction(messages: list[dict], schema: dict) -> list[dict]:
    instruction = (
        "Return only valid JSON that conforms to this JSON Schema. "
        "Do not include markdown fences or explanatory text. "
        f"Schema: {json.dumps(schema, sort_keys=True)}"
    )
    updated = [dict(message) for message in messages]
    if updated and updated[0].get("role") == "system":
        updated[0]["content"] = f"{updated[0].get('content', '')}\n\n{instruction}"
    else:
        updated.insert(0, {"role": "system", "content": instruction})
    return updated


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content)


def _bedrock_content(raw: dict[str, Any]) -> str:
    content = raw.get("content", [])
    if not content:
        return ""
    first = content[0]
    if isinstance(first, dict):
        return str(first.get("text", ""))
    return str(first)


def _bedrock_usage(raw: dict[str, Any]) -> LLMUsage | None:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = int(usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("output_tokens", 0) or 0)
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    fenced_json = _extract_fenced_json_text(stripped)
    if fenced_json is not None:
        return json.loads(fenced_json)

    embedded_json = _extract_embedded_json_text(stripped)
    if embedded_json is not None:
        return json.loads(embedded_json)

    raise ValueError("No valid JSON found in response")


def _extract_fenced_json_text(text: str) -> str | None:
    if not text.startswith("```"):
        return None
    inner_lines = []
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```") and not in_fence:
            in_fence = True
            continue
        if line.strip() == "```" and in_fence:
            break
        if in_fence:
            inner_lines.append(line)
    return "\n".join(inner_lines) if inner_lines else None


def _extract_embedded_json_text(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


__all__ = ["BedrockProvider"]


def _normalize_structured_args(
    args: tuple[Any, ...],
    model: str | None,
    schema: dict | str | None,
) -> tuple[str, dict | None]:
    if len(args) == 2:
        legacy_schema, legacy_model = args
        return str(legacy_model), legacy_schema if isinstance(
            legacy_schema, dict
        ) else None
    if len(args) == 1:
        first = args[0]
        if isinstance(first, dict):
            if model is None:
                raise TypeError("model is required")
            return model, first
        return str(first), schema if isinstance(schema, dict) else None
    if model is None:
        raise TypeError("model is required")
    return model, schema if isinstance(schema, dict) else None


def _bedrock_stream_error(event: dict[str, Any]) -> str | None:
    error_keys = [
        "internalServerException",
        "modelStreamErrorException",
        "validationException",
        "throttlingException",
        "modelTimeoutException",
        "serviceUnavailableException",
    ]
    for key in error_keys:
        if key in event:
            payload = event.get(key) or {}
            message = payload.get("message") if isinstance(payload, dict) else payload
            return f"Bedrock stream error {key}: {message or 'unknown error'}"
    return None
