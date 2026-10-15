"""Passthrough request mode, from ExecutionRequest to the kernel profile.

Session sends run as ``mode="passthrough"``: zero orchestrator model turns and
one call to the single mentioned Agent. These tests pin the production path:
the request mode is accepted, persisted on the user message, mapped to the
passthrough profile, resolved without a model route or prompt, and refused by
the chat route.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from api_gateway.routes.room_routes import send_message
from common.config.loader import Settings
from common.dto import ExecutionAck, ExecutionRequest, UserMessageInsertResult
from common.dto.turn_lifecycle import RunStartedPayload
from conversation.runtime import ConversationRuntime
from execution.adapters.profiles import OrchestratorProfileResolver
from execution.facade import ExecutionFacade
from execution.orchestrator_routing import (
    MODE_PROFILE_MAP,
    DualRuntimeRouter,
    RoomMessageEnvelopeResolver,
    map_mode_to_profile,
)
from models.request import OrchestrationRequest, RoomCenterUserMessageRequest
from models.room import MessageContent, Room, RoomUserMessage
from tests.test_execution_facade import _make_facade
from tests.test_orchestrator_passthrough import passthrough_profile


def _passthrough_request(**overrides) -> ExecutionRequest:
    values = {
        "room_id": "room-1",
        "sender_id": "owner-1",
        "client_request_id": "cr-1",
        "message": {"message_content": {"message_text": "plan a trip"}},
        "mode": "passthrough",
        "agent_scope": {"source": "mention", "agent_ids": ["agent-1"]},
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_execution_request_accepts_passthrough_mode():
    request = _passthrough_request()

    assert request.mode == "passthrough"
    assert request.agent_scope.source == "mention"
    assert ExecutionFacade._room_request_extend_info(request) == {
        "execution_mode": "passthrough",
        "agent_scope": {"source": "mention", "agent_ids": ["agent-1"]},
    }


def test_execution_request_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        _passthrough_request(mode="autonomous")


def test_run_started_payload_accepts_passthrough_mode():
    payload = RunStartedPayload.model_validate(
        {
            "hybro_turn_id": "turn-1",
            "user_message_id": "msg-1",
            "started_at": "2026-10-14T00:00:00Z",
            "mode": "passthrough",
        }
    )

    assert payload.mode == "passthrough"


def test_passthrough_mode_maps_to_passthrough_profile():
    assert MODE_PROFILE_MAP["passthrough"] == "passthrough"
    assert map_mode_to_profile("passthrough") == "passthrough"
    # Existing modes are unchanged.
    assert map_mode_to_profile("direct") == "fast"
    assert map_mode_to_profile("supervisor") == "ultimate"


@pytest.mark.asyncio
async def test_schedule_orchestration_carries_passthrough_mode():
    facade, _deps = _make_facade()
    facade._route_orchestration = AsyncMock()
    request = _passthrough_request()
    ack = ExecutionAck(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        should_start_orchestration=True,
        preflight_outcome="ready",
    )

    facade.schedule_orchestration(request, ack)
    await asyncio.sleep(0)

    _routed, orchestration_request = facade._route_orchestration.call_args.args
    assert orchestration_request.mode == "passthrough"
    assert orchestration_request.agent_scope == {
        "source": "mention",
        "agent_ids": ["agent-1"],
    }


def _user_message(extend_info: dict) -> RoomUserMessage:
    return RoomUserMessage(
        room_id="room-1",
        message_id="msg-1",
        user_id="owner-1",
        message_content=MessageContent(message_text="plan a trip"),
        extend_info=extend_info,
    )


@pytest.mark.asyncio
async def test_router_resolves_passthrough_profile_for_one_agent_mention():
    profile = passthrough_profile()
    message = _user_message(
        {
            "execution_mode": "passthrough",
            "candidate_scope_source": "mention",
            "candidate_agent_ids": ["agent-1"],
        }
    )
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=AsyncMock(return_value=message),
        list_room_agent_ids=AsyncMock(return_value=["agent-1"]),
    )
    router = DualRuntimeRouter(
        runtime=SimpleNamespace(profiles={"passthrough": profile}),
        envelope_source=resolver,
    )

    envelope, resolved = await router._resolve_envelope_and_profile(
        OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            user_id="owner-1",
            client_request_id="cr-1",
            mode="passthrough",
            agent_scope={"source": "mention", "agent_ids": ["agent-1"]},
        )
    )

    assert envelope.mode == "passthrough"
    assert envelope.scope_source == "mention"
    assert envelope.candidate_agent_ids == ["agent-1"]
    assert resolved is profile
    assert resolved.max_model_turns == 0


@pytest.mark.asyncio
async def test_router_recovery_reads_persisted_passthrough_mode():
    profile = passthrough_profile()
    message = _user_message(
        {
            "execution_mode": "passthrough",
            "candidate_scope_source": "mention",
            "candidate_agent_ids": ["agent-1"],
        }
    )
    router = DualRuntimeRouter(
        runtime=SimpleNamespace(profiles={"passthrough": profile}),
        envelope_source=RoomMessageEnvelopeResolver(
            get_user_message=AsyncMock(return_value=message),
            list_room_agent_ids=AsyncMock(return_value=["agent-1"]),
        ),
    )

    envelope, resolved = await router._resolve_envelope_and_profile(
        OrchestrationRequest(
            room_id="room-1", room_user_message_id="msg-1", user_id="owner-1"
        )
    )

    assert envelope.mode == "passthrough"
    assert resolved is profile


# -- Persisted mode on the user message -------------------------------------


def _runtime_for_persist() -> tuple[ConversationRuntime, list[dict]]:
    svc = object.__new__(ConversationRuntime)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="owner-1",
            room_owner_name="Owner",
            room_agent_set={},
            extend_info={},
        )
    )
    svc._bound = False
    svc._facade = None
    svc.cancellation_control = MagicMock()
    svc.cancellation_control.create_token.return_value = object()
    svc.cancellation_control.check_cancelled = AsyncMock(return_value=False)
    facade = AsyncMock()
    facade.get_user_message_by_idempotency_key.return_value = None
    svc.bind_facade(facade)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    persisted: list[dict] = []

    async def persist(user_message, **_kwargs):
        persisted.append(dict(user_message.extend_info))
        user_message.message_id = "server-message"
        return UserMessageInsertResult(
            message_id="server-message", created=True, document={}
        )

    svc._persist_user_message = AsyncMock(side_effect=persist)
    return svc, persisted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "stored"),
    [
        ("passthrough", "passthrough"),
        ("direct", "direct"),
        ("supervisor", "supervisor"),
        ("autonomous", "direct"),
    ],
)
async def test_persist_message_stores_request_mode(requested, stored):
    svc, persisted = _runtime_for_persist()
    request = RoomCenterUserMessageRequest(
        room_id="room-1",
        user_id="owner-1",
        client_request_id="cr-1",
        message=RoomUserMessage(
            room_id="room-1",
            message_id="",
            user_id="owner-1",
            message_content=MessageContent(message_text="plan a trip"),
        ),
        extend_info={
            "execution_mode": requested,
            "agent_scope": {"source": "all_agents"},
        },
    )

    response, _context = await svc.persist_message_to_room(
        request,
        target_group="all_agents",
        idempotency_fingerprint="fp-1",
        idempotency_fingerprint_version=1,
    )

    assert response.success is True
    assert persisted[0]["execution_mode"] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "stored"),
    [
        ("passthrough", "passthrough"),
        ("direct", "direct"),
        ("supervisor", "supervisor"),
        (None, "direct"),
    ],
)
async def test_orchestration_envelope_persists_request_mode(requested, stored):
    svc = object.__new__(ConversationRuntime)
    svc._store = MagicMock()
    svc._store.update_room_user_message_by_message_id = AsyncMock(return_value=True)
    extend_info = {"agent_scope": {"source": "mention", "agent_ids": ["agent-1"]}}
    if requested is not None:
        extend_info["execution_mode"] = requested
    request = RoomCenterUserMessageRequest(
        room_id="room-1",
        user_id="owner-1",
        client_request_id="cr-1",
        message=_user_message({}),
        extend_info=extend_info,
    )
    user_message = _user_message({"execution_mode": stored, "quoted_text": "q"})

    result = await svc._prepare_orchestration_envelope(
        request,
        user_message,
        {"agent-1": "Agent One"},
        explicit_mentions=None,
        client_request_id="cr-1",
    )

    assert result.success is True
    persisted = svc._store.update_room_user_message_by_message_id.await_args.args[1]
    assert persisted.extend_info["execution_mode"] == stored
    assert persisted.extend_info["candidate_agent_ids"] == ["agent-1"]
    assert persisted.extend_info["quoted_text"] == "q"


# -- Chat route ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_route_still_rejects_passthrough_mode():
    engine = MagicMock()
    engine.execute = AsyncMock()
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "room_id": "room-1",
            "client_request_id": "cr-1",
            "mode": "passthrough",
            "message": {"message_content": {"message_text": "hi"}},
            "agent_scope": {"source": "mention", "agent_ids": ["agent-1"]},
        }
    )

    response = await send_message(
        request,
        user=SimpleNamespace(user_id="owner-1"),
        store=MagicMock(),
        engine=engine,
        center=MagicMock(),
    )

    assert response.success is False
    assert response.status_code == 400
    assert "direct, supervisor" in (response.error or "")
    engine.execute.assert_not_called()


# -- Profile resolution and configuration ------------------------------------


def test_profile_resolver_builds_passthrough_without_model_or_prompt():
    registry = MagicMock()
    registry.get_route_configuration.side_effect = AssertionError(
        "passthrough must not resolve a model route"
    )
    settings = Settings.model_validate(
        {
            "orchestrator_passthrough_deadline_seconds": 120.0,
            "orchestrator_passthrough_max_transport_retries_per_call": 4,
        }
    )
    resolver = OrchestratorProfileResolver(
        model_registry=registry, settings_obj=settings
    )

    profile = resolver.resolve("passthrough")

    assert profile.profile_id == "passthrough"
    assert profile.model is None
    assert profile.prompt is None
    assert profile.max_model_turns == 0
    assert profile.grace_model_turns == 0
    assert profile.max_agent_calls == 1
    assert profile.max_parallel_calls == 1
    assert profile.max_compactions == 0
    assert profile.deadline_seconds == 120.0
    assert profile.max_transport_retries_per_call == 4
    assert profile.tool_execution == "sequential"
    assert profile.initial_routing == "explicit_agent_first"
    assert profile.finalization == "pass_through"


def test_passthrough_settings_defaults():
    settings = Settings()

    assert settings.orchestrator_passthrough_deadline_seconds == 300.0
    assert settings.orchestrator_passthrough_max_transport_retries_per_call == 2


@pytest.mark.parametrize(
    "field",
    [
        "orchestrator_passthrough_deadline_seconds",
        "orchestrator_passthrough_max_transport_retries_per_call",
    ],
)
def test_passthrough_settings_reject_zero(field):
    with pytest.raises(ValidationError):
        Settings.model_validate({field: 0})


def test_session_wait_settings_defaults_and_bounds():
    settings = Settings()

    assert settings.session_send_wait_seconds == 30.0
    assert settings.session_run_start_grace_seconds == 10.0
    for field in ("session_send_wait_seconds", "session_run_start_grace_seconds"):
        with pytest.raises(ValidationError):
            Settings.model_validate({field: 0})
        assert Settings.model_validate({field: 5}).model_dump()[field] == 5
