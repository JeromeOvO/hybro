"""Session HTTP routes: request validation, error mapping, auth, and limits."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from api_gateway.dependencies import get_session_access
from common import auth
from common.auth import ClerkUser, get_current_user
from common.errors import AppError
from main import SESSION_MESSAGE_MAX_BYTES, SESSION_MESSAGE_MAX_CONCURRENT, create_app
from models.session import (
    SessionAgent,
    SessionClosed,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionOwnerRecord,
    SessionReply,
    SessionStatus,
    SessionSummary,
)
from session.access import BoundSessionAccess
from session.protocols import SessionAccess

NOW = datetime(2026, 10, 14, tzinfo=UTC)
SEND = "/api/v1/sessions/messages"


class AccessStub:
    def __init__(self) -> None:
        self.sent: list[SessionMessageRequest] = []
        self.error: AppError | None = None
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.release = asyncio.Event()
        self.release.set()

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def send(self, request: SessionMessageRequest) -> SessionMessageResponse:
        self.started.put_nowait(request.client_request_id)
        await self.release.wait()
        self._raise()
        self.sent.append(request)
        return SessionMessageResponse(
            session_id="sess-1",
            accepted=True,
            state="completed",
            reply="Done.",
            client_request_id=request.client_request_id,
        )

    def _status(self, session_id: str) -> SessionStatus:
        return SessionStatus(
            session_id=session_id,
            state="waiting_on_owner",
            latest_replies=[SessionReply(message_id="f1", text="Earlier answer")],
            paused_agent_id="agent-1",
            agents=[SessionAgent(agent_id="agent-1", name="Travel")],
            opened_at=NOW,
            last_activity_at=NOW,
        )

    async def check(self, session_id: str) -> SessionStatus:
        self._raise()
        return self._status(session_id)

    async def close(self, session_id: str) -> SessionClosed:
        self._raise()
        return SessionClosed(session_id=session_id, status="closed")

    async def list_sessions(self) -> SessionListResponse:
        return SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id="sess-1",
                    client_label="Buyer agent",
                    state="waiting_on_owner",
                    conversation_id="conv-1",
                    opened_at=NOW,
                    last_activity_at=NOW,
                )
            ]
        )

    async def get_record(self, session_id: str) -> SessionOwnerRecord:
        self._raise()
        return SessionOwnerRecord(
            **self._status(session_id).model_dump(),
            client_label="Buyer agent",
            status="open",
            conversation_id="conv-1",
        )


def body(**overrides) -> dict:
    data = {
        "agent_id": "agent-1",
        "text": "Plan a trip",
        "client_request_id": "cr-1",
        "client_label": "Buyer agent",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def make_app():
    access = AccessStub()
    app = create_app()
    app.dependency_overrides[get_session_access] = lambda: access
    app.dependency_overrides[get_current_user] = lambda: ClerkUser(
        user_id="owner", session_id="s", claims={}
    )
    return app, access


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://hybro.test",
    )


def test_stub_satisfies_session_access():
    assert isinstance(AccessStub(), SessionAccess)


async def test_send_returns_the_session_result():
    app, access = make_app()
    async with client_for(app) as client:
        response = await client.post(SEND, json=body())

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "sess-1",
        "accepted": True,
        "state": "completed",
        "reply": "Done.",
        "paused_agent_id": None,
        "reason": None,
        "client_request_id": "cr-1",
    }
    assert access.sent[0].agent_id == "agent-1"


@pytest.mark.parametrize(
    "payload",
    [
        body(client_request_id=None),
        body(client_request_id="x" * 129),
        body(text="x" * 10_001),
        body(text="   "),
        body(client_label=None),
        body(data={"a": 1}),
        body(files=[]),
    ],
)
async def test_send_rejects_invalid_requests_with_422(payload):
    app, access = make_app()
    async with client_for(app) as client:
        response = await client.post(SEND, json=payload)

    assert response.status_code == 422
    assert access.sent == []


async def test_follow_up_send_needs_no_client_label():
    app, access = make_app()
    async with client_for(app) as client:
        response = await client.post(
            SEND, json=body(session_id="sess-1", client_label=None)
        )

    assert response.status_code == 200
    assert access.sent[0].session_id == "sess-1"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("invalid_request", 422),
        ("session_not_found", 404),
        ("agent_not_found", 404),
        ("subnet_not_found", 404),
        ("not_found", 404),
        ("forbidden", 403),
        ("session_closed", 409),
        ("request_conflict", 409),
        ("agent_unavailable", 409),
        ("request_rejected", 400),
        ("execution_error", 500),
    ],
)
async def test_send_maps_session_errors(code, status):
    app, access = make_app()
    access.error = AppError("refused", code=code)
    async with client_for(app) as client:
        response = await client.post(SEND, json=body())

    assert response.status_code == status
    assert response.json()["detail"] == {
        "code": code,
        "message": "refused",
        "client_request_id": "cr-1",
    }


async def test_check_close_list_and_record():
    app, _ = make_app()
    async with client_for(app) as client:
        check = await client.get("/api/v1/sessions/sess-1")
        record = await client.get("/api/v1/sessions/sess-1/record")
        listing = await client.get("/api/v1/sessions")
        close = await client.post("/api/v1/sessions/sess-1/close")

    assert check.status_code == 200
    assert check.json()["state"] == "waiting_on_owner"
    assert "conversation_id" not in check.json()
    assert record.json()["conversation_id"] == "conv-1"
    assert listing.json()["sessions"][0]["conversation_id"] == "conv-1"
    assert close.json() == {"session_id": "sess-1", "status": "closed"}


async def test_unknown_session_is_404_on_read_routes():
    app, access = make_app()
    access.error = AppError("Session not found", code="session_not_found")
    async with client_for(app) as client:
        check = await client.get("/api/v1/sessions/missing")
        close = await client.post("/api/v1/sessions/missing/close")
        record = await client.get("/api/v1/sessions/missing/record")

    assert check.status_code == close.status_code == record.status_code == 404
    assert check.json()["detail"]["session_id"] == "missing"


async def test_clerk_anonymous_requests_fail_with_401(monkeypatch):
    app, access = make_app()
    app.dependency_overrides.pop(get_current_user)
    app.dependency_overrides.pop(get_session_access)
    monkeypatch.setattr(auth, "_require_clerk_secret_key", lambda: "sk_test")
    monkeypatch.setattr(
        auth,
        "authenticate_request",
        lambda request, options: SimpleNamespace(is_signed_in=False, payload=None),
    )
    async with client_for(app) as client:
        responses = [
            await client.post(SEND, json=body()),
            await client.get("/api/v1/sessions/sess-1"),
            await client.post("/api/v1/sessions/sess-1/close"),
            await client.get("/api/v1/sessions"),
            await client.get("/api/v1/sessions/sess-1/record"),
        ]

    assert [response.status_code for response in responses] == [401] * 5
    assert access.sent == []


def test_get_session_access_binds_owner_and_resolver():
    facade = object()
    deps = SimpleNamespace(
        session_facade=facade,
        agent_service=object(),
        agent_group_store=object(),
        agent_matcher=object(),
    )
    user = ClerkUser(user_id="owner-1", session_id="s", claims={"username": "estate"})

    access = get_session_access(user, deps)

    assert isinstance(access, BoundSessionAccess)
    assert access._owner.user_id == "owner-1"
    assert access._owner.name == "estate"
    assert access._resolver._requesting_user_id == "owner-1"


# -- Request limits -----------------------------------------------------------


def sized_body(size: int) -> bytes:
    empty = json.dumps(body(text="")).encode()
    return json.dumps(body(text="x" * (size - len(empty)))).encode()


async def test_session_body_limit_is_64_kib():
    assert SESSION_MESSAGE_MAX_BYTES == 64 * 1024
    app, access = make_app()
    async with client_for(app) as client:
        response = await client.post(
            SEND,
            content=b"",
            headers={"Content-Length": str(SESSION_MESSAGE_MAX_BYTES + 1)},
        )
        streamed = await client.post(
            SEND,
            content=sized_body(SESSION_MESSAGE_MAX_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == streamed.status_code == 413
    assert access.started.empty()


async def test_session_send_concurrency_is_bounded():
    assert SESSION_MESSAGE_MAX_CONCURRENT == 4
    app, access = make_app()
    async with client_for(app) as client:
        access.release.clear()
        tasks = [
            asyncio.create_task(client.post(SEND, json=body(client_request_id=str(i))))
            for i in range(4)
        ]
        try:
            async with asyncio.timeout(5):
                entered = {await access.started.get() for _ in tasks}
                assert entered == {"0", "1", "2", "3"}
                overflow = await client.post(SEND, json=body(client_request_id="x"))
            assert overflow.status_code == 429
            # Reads are not admission-limited.
            check = await client.get("/api/v1/sessions/sess-1")
            assert check.status_code == 200
        finally:
            access.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert [task.result().status_code for task in tasks] == [200] * 4
