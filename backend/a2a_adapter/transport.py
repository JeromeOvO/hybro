"""Raw HTTP/SSE transport for agents addressed by URL.

Unlike ``client_facade`` (which drives the SDK client), this module speaks the
JSON-RPC binding directly: it is used when a caller already holds an agent URL
and a pre-built message payload, and needs a plain result or an SSE event
stream without card discovery.

1.0 specifics handled here: the JSON-RPC method names are ``SendMessage`` /
``SendStreamingMessage``, the negotiated version is sent in the ``A2A-Version``
header (a missing header is treated as 0.3), and stream frames are single-member
objects discriminated by their member name rather than a ``kind`` field.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from a2a.types import Message as SDKMessage
from httpx_sse import aconnect_sse

from common.dto import AgentStreamEvent, AgentTaskResult, InternalAgentMessage

from .docker_host_fallback import (
    stream_with_docker_host_url_fallback,
    with_docker_host_url_fallback,
)
from .message_factory import to_sdk_message
from .translators import a2a_event_to_stream_event, a2a_task_to_result

_SEND_METHOD = "SendMessage"
_STREAM_METHOD = "SendStreamingMessage"
_VERSION_HEADER = "A2A-Version"
_PROTOCOL_VERSION = "1.0"


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
        *,
        user_id: str | None = None,
        accepted_output_modes: Sequence[str] | None = None,
    ) -> AgentTaskResult:
        try:
            request_payload = _build_send_request(
                message,
                streaming=False,
                accepted_output_modes=accepted_output_modes,
            )

            async def _post(candidate_url: str) -> httpx.Response:
                response = await self._client.post(
                    candidate_url,
                    json=request_payload,
                    headers={_VERSION_HEADER: _PROTOCOL_VERSION},
                )
                response.raise_for_status()
                return response

            response = await with_docker_host_url_fallback(
                agent_url.rstrip("/"),
                _post,
            )
            payload = response.json()
            task_payload = payload
            if "jsonrpc" not in payload and isinstance(payload.get("result"), dict):
                task_payload = payload["result"]
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
        *,
        user_id: str | None = None,
        accepted_output_modes: Sequence[str] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        try:
            request_payload = _build_send_request(
                message,
                streaming=True,
                accepted_output_modes=accepted_output_modes,
            )

            async def _stream(candidate_url: str) -> AsyncIterator[AgentStreamEvent]:
                async with aconnect_sse(
                    self._client,
                    "POST",
                    candidate_url,
                    json=request_payload,
                    headers={_VERSION_HEADER: _PROTOCOL_VERSION},
                ) as event_source:
                    async for sse in event_source.aiter_sse():
                        event_data = json.loads(sse.data)
                        event_data = _stream_event_payload(event_data)
                        yield a2a_event_to_stream_event(event_data, message.agent_id)

            async for event in stream_with_docker_host_url_fallback(
                agent_url.rstrip("/"),
                _stream,
            ):
                yield event
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
    accepted_output_modes: Sequence[str] | None = None,
) -> dict[str, Any]:
    sdk_message = to_sdk_message(
        {
            "role": message.role,
            "parts": message.parts,
            "metadata": {"agent_id": message.agent_id, **message.metadata},
        }
    )
    params: dict[str, Any] = {"message": _message_to_wire(sdk_message)}
    if accepted_output_modes:
        params["configuration"] = {"acceptedOutputModes": list(accepted_output_modes)}
    return {
        "jsonrpc": "2.0",
        "id": _new_id(),
        "method": _STREAM_METHOD if streaming else _SEND_METHOD,
        "params": params,
    }


def _message_to_wire(message: SDKMessage) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    return MessageToDict(message)


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def _stream_event_payload(event_data: dict[str, Any]) -> dict[str, Any]:
    """Return the frame that carries the event.

    JSON-RPC SSE responses wrap the event under ``result``; a transport-level
    error is surfaced as an error event instead.
    """
    if "jsonrpc" in event_data:
        if event_data.get("error") is not None:
            return event_data
        result = event_data.get("result")
        if isinstance(result, dict):
            return event_data
    error = event_data.get("error")
    if error is not None:
        return {"type": "error", "error": error, "final": True}
    return event_data


__all__ = ["AgentTransportImpl"]
