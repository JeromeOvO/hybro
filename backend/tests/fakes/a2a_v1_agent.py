"""In-process A2A 1.0 agent used to drive adapter tests over the real wire.

This is a protocol double, not an SDK server: it implements the JSON-RPC
binding and SSE framing from the A2A 1.0 specification directly, so adapter
tests validate Hybro against the protocol rather than against the SDK's own
interpretation of it.

It is mounted on an ``httpx.AsyncClient`` through ``ASGITransport``, which the
adapter's bounded clients are pointed at via monkeypatch.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tests.fakes.a2a_v1 import make_agent_card

_SEND = "SendMessage"
_STREAM = "SendStreamingMessage"
_GET_TASK = "GetTask"
_CANCEL_TASK = "CancelTask"


class A2A10AgentStub:
    """Scriptable 1.0 agent.

    ``frames`` is the ordered list of stream frames returned by
    ``SendStreamingMessage``; ``send_result`` is the single frame returned by
    ``SendMessage``. Both are plain 1.0 JSON payloads, e.g.
    ``{"task": {...}}`` or ``{"statusUpdate": {...}}``.
    """

    def __init__(
        self,
        *,
        frames: list[dict[str, Any]] | None = None,
        send_result: dict[str, Any] | None = None,
        send_error: dict[str, Any] | None = None,
        card: Any | None = None,
        card_path_status: dict[str, int] | None = None,
        task_payload: dict[str, Any] | None = None,
        cancel_error: dict[str, Any] | None = None,
    ) -> None:
        self.frames = frames or []
        self.send_result = send_result
        self.send_error = send_error
        self.card = card or make_agent_card(capabilities={"streaming": True})
        self.card_path_status = card_path_status or {}
        self.task_payload = task_payload
        self.cancel_error = cancel_error

        self.requests: list[dict[str, Any]] = []
        self.request_headers: list[dict[str, str]] = []
        self.card_requests: list[str] = []

        self.app = Starlette(
            routes=[
                Route(
                    "/.well-known/agent-card.json",
                    self._card,
                    methods=["GET"],
                ),
                Route("/.well-known/agent.json", self._card, methods=["GET"]),
                Route("/", self._rpc, methods=["POST"]),
                Route("/a2a", self._rpc, methods=["POST"]),
            ]
        )

    # -- endpoints ---------------------------------------------------------

    async def _card(self, request: Request) -> JSONResponse:
        self.card_requests.append(request.url.path)
        status = self.card_path_status.get(request.url.path, 200)
        if status != 200:
            return JSONResponse({"error": "not found"}, status_code=status)
        return JSONResponse(MessageToDict(self.card))

    async def _rpc(self, request: Request) -> Any:
        body = await request.json()
        self.requests.append(body)
        self.request_headers.append(dict(request.headers))

        method = body.get("method")
        request_id = body.get("id")
        if request.headers.get("a2a-version") != "1.0":
            return self._error(
                request_id, -32009, "VERSION_NOT_SUPPORTED", "missing A2A-Version"
            )
        if method == _STREAM:
            return StreamingResponse(
                self._sse(request_id), media_type="text/event-stream"
            )
        if method == _SEND:
            if self.send_error is not None:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": request_id, "error": self.send_error}
                )
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "result": self.send_result}
            )
        if method == _GET_TASK:
            if self.task_payload is None:
                return self._error(request_id, -32001, "TASK_NOT_FOUND")
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "result": self.task_payload}
            )
        if method == _CANCEL_TASK:
            if self.cancel_error is not None:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": request_id, "error": self.cancel_error}
                )
            return JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "result": self.task_payload}
            )
        return self._error(request_id, -32601, "Method not found")

    async def _sse(self, request_id: Any):
        for frame in self.frames:
            payload = {"jsonrpc": "2.0", "id": request_id, "result": frame}
            yield f"data: {json.dumps(payload)}\n\n"

    @staticmethod
    def _error(
        request_id: Any, code: int, reason: str, message: str = ""
    ) -> JSONResponse:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": code,
                    "message": message or reason,
                    "data": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": reason,
                            "domain": "a2a-protocol.org",
                        }
                    ],
                },
            }
        )

    # -- helpers -----------------------------------------------------------

    def last_message(self) -> dict[str, Any]:
        assert self.requests, "no request reached the agent"
        return self.requests[-1]["params"]["message"]


def completed_task_payload(
    *,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    text: str = "done",
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A 1.0 task frame in the completed state."""
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_COMPLETED",
            "message": {
                "messageId": "status-msg",
                "role": "ROLE_AGENT",
                "parts": [{"text": text, "mediaType": "text/plain"}],
            },
        },
        "artifacts": artifacts
        if artifacts is not None
        else [
            {
                "artifactId": "art-1",
                "name": "response",
                "parts": [{"text": text, "mediaType": "text/plain"}],
            }
        ],
    }


def input_required_task_payload(
    *,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    question: str = "Which city?",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A 1.0 task frame paused for human input, carrying HITL metadata."""
    message: dict[str, Any] = {
        "messageId": "status-msg",
        "role": "ROLE_AGENT",
        "parts": [{"text": question, "mediaType": "text/plain"}],
    }
    if metadata:
        message["metadata"] = metadata
    return {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": message},
    }


def text_status_update(
    *,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    state: str = "TASK_STATE_WORKING",
    text: str | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {"state": state}
    if text is not None:
        status["message"] = {
            "messageId": "status-msg",
            "role": "ROLE_AGENT",
            "parts": [{"text": text, "mediaType": "text/plain"}],
        }
    return {"taskId": task_id, "contextId": context_id, "status": status}


def text_artifact_update(
    *,
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    artifact_id: str = "art-1",
    name: str = "response",
    text: str = "chunk",
    append: bool = False,
    last_chunk: bool = True,
) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "contextId": context_id,
        "append": append,
        "lastChunk": last_chunk,
        "artifact": {
            "artifactId": artifact_id,
            "name": name,
            "parts": [{"text": text, "mediaType": "text/plain"}],
        },
    }


def new_message_id() -> str:
    return uuid4().hex


__all__ = [
    "A2A10AgentStub",
    "completed_task_payload",
    "input_required_task_payload",
    "new_message_id",
    "text_artifact_update",
    "text_status_update",
]
