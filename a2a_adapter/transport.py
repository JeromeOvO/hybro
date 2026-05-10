import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    SendStreamingMessageRequest,
    TextPart,
)
from httpx_sse import aconnect_sse

from common.dto import AgentStreamEvent, AgentTaskResult, InternalAgentMessage

from .translators import (
    a2a_event_to_stream_event,
    a2a_task_to_result,
    internal_message_to_a2a,
)


class AgentTransportImpl:
    def __init__(
        self,
        timeout: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_message(
        self,
        agent_url: str,
        message: InternalAgentMessage,
        **kwargs,
    ) -> AgentTaskResult:
        try:
            request_payload = _build_send_request(message, streaming=False)
            response = await self._client.post(
                agent_url.rstrip("/"),
                json=request_payload,
            )
            response.raise_for_status()
            payload = response.json()
            task_payload = payload.get("result", payload)
            return a2a_task_to_result(task_payload, message.agent_id)
        except Exception as exc:
            return AgentTaskResult(
                task_id="",
                agent_id=message.agent_id,
                status="error",
                result={},
                error=str(exc),
            )

    async def stream_message(
        self,
        agent_url: str,
        message: InternalAgentMessage,
        **kwargs,
    ) -> AsyncIterator[AgentStreamEvent]:
        try:
            request_payload = _build_send_request(message, streaming=True)
            async with aconnect_sse(
                self._client,
                "POST",
                agent_url.rstrip("/"),
                json=request_payload,
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    event_data = json.loads(sse.data)
                    event_data = _stream_event_payload(event_data)
                    yield a2a_event_to_stream_event(event_data, message.agent_id)
        except Exception as exc:
            yield AgentStreamEvent(
                task_id="",
                agent_id=message.agent_id,
                event_type="error",
                payload={"error": str(exc)},
                final=True,
            )


def _build_send_request(
    message: InternalAgentMessage,
    *,
    streaming: bool,
) -> dict[str, Any]:
    payload = internal_message_to_a2a(message)
    sdk_message = Message(
        message_id=str(uuid4()),
        role=Role(payload["role"]),
        parts=[_to_part(part) for part in payload["parts"]],
        metadata=payload["metadata"],
    )
    params = MessageSendParams(message=sdk_message)
    request_cls = SendStreamingMessageRequest if streaming else SendMessageRequest
    request = request_cls(id=str(uuid4()), params=params)
    return request.model_dump(mode="json", by_alias=True, exclude_none=True)


def _to_part(part: dict[str, Any]) -> Part:
    if "root" in part:
        return Part(**part)

    kind = part.get("kind") or part.get("type")
    metadata = part.get("metadata")
    if kind == "data" or "data" in part:
        return Part(root=DataPart(data=part.get("data", {}), metadata=metadata))
    if kind == "text" or "text" in part:
        return Part(root=TextPart(text=str(part.get("text", "")), metadata=metadata))
    return Part(root=TextPart(text=json.dumps(part, sort_keys=True)))


def _stream_event_payload(event_data: dict[str, Any]) -> dict[str, Any]:
    result = event_data.get("result")
    if isinstance(result, dict):
        return result

    error = event_data.get("error")
    if error is not None:
        return {"type": "error", "error": error, "final": True}

    return event_data


__all__ = ["AgentTransportImpl"]
