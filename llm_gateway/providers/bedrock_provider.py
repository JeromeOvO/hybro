import json
from typing import Any

import aioboto3

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage


class BedrockProvider:
    def __init__(
        self,
        session: Any | None = None,
        region: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._region = region or settings.bedrock_region
        self._default_model = default_model or settings.bedrock_supervisor_model
        self._session = session or aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=self._region,
        )

    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        model_id = model or self._default_model
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
        schema: dict,
        model: str | None = None,
        **kwargs,
    ) -> LLMStructuredResponse:
        response = await self.generate(
            _with_schema_instruction(messages, schema),
            model=model,
            **kwargs,
        )
        return LLMStructuredResponse(
            data=_extract_json(response.content),
            model=response.model,
            usage=response.usage,
            raw_response=response.raw_response,
        )

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        raise NotImplementedError("BedrockProvider does not support embeddings")

    async def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
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
            bedrock_messages.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content,
                }
            )
    if not bedrock_messages:
        bedrock_messages.append({"role": "user", "content": ""})
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
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError("No valid JSON found in response")


__all__ = ["BedrockProvider"]
