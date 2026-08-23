"""Step-7 dual-routing seam: routing, ownership, and ingress dispatch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import CancellationAck, ExecutionAck, ExecutionRequest
from common.dto.hitl import (
    A2AInteractionSpec,
    HITLAnswerKind,
    HITLQuestionAnswer,
    HITLQuestionSpec,
    HITLRouteSnapshotV2,
    HITLTextAnswer,
)
from execution.facade import ExecutionFacade
from execution.hitl.exceptions import HITLRoomMismatchError
from execution.orchestrator.session import SessionConflict
from execution.orchestrator_routing import (
    OWNER_LEGACY,
    OWNER_ORCHESTRATOR,
    DualRuntimeRouter,
    RoomMessageEnvelope,
    RoomMessageEnvelopeResolver,
    UnsupportedEnvelopeError,
    WebhookAuthenticationError,
    stable_route_bucket,
)
from models.request import OrchestrationRequest

OWNER = OWNER_ORCHESTRATOR
LEGACY = OWNER_LEGACY


def _settings(**overrides):
    values = dict(
        orchestrator_kill_switch=False,
        orchestrator_routing_enabled=True,
        orchestrator_user_allowlist=[],
        orchestrator_room_allowlist=[],
        orchestrator_fast_ratio=0,
        orchestrator_ultimate_ratio=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _runtime(profiles=None):
    return SimpleNamespace(
        profiles=profiles or {"fast": object(), "ultimate": object()}
    )


def _router(
    settings=None,
    runtime=None,
    envelope_source=None,
    webhook_token_verifier=None,
):
    return DualRuntimeRouter(
        runtime=runtime or _runtime(),
        settings=settings or _settings(),
        envelope_source=envelope_source,
        webhook_token_verifier=webhook_token_verifier,
    )


# -- Run-creation routing -------------------------------------------------


def test_stable_route_bucket_is_deterministic():
    assert stable_route_bucket("room-1", "req-1") == stable_route_bucket(
        "room-1", "req-1"
    )
    assert stable_route_bucket("room-1", "req-1") != stable_route_bucket(
        "room-1", "req-2"
    )
    assert 0 <= stable_route_bucket("room-1", "req-1") < 100


@pytest.mark.asyncio
async def test_assign_runtime_disabled_defaults_to_legacy():
    router = _router(settings=_settings(orchestrator_routing_enabled=False))
    owner = await router.assign_runtime(
        room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
    )
    assert owner == LEGACY


@pytest.mark.asyncio
async def test_assign_runtime_kill_switch_forces_legacy():
    router = _router(
        settings=_settings(orchestrator_kill_switch=True, orchestrator_fast_ratio=100)
    )
    owner = await router.assign_runtime(
        room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
    )
    assert owner == LEGACY


@pytest.mark.asyncio
async def test_assign_runtime_ratio_zero_and_hundred():
    zero = _router(settings=_settings(orchestrator_fast_ratio=0))
    hundred = _router(settings=_settings(orchestrator_fast_ratio=100))
    assert (
        await zero.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
        )
        == LEGACY
    )
    assert (
        await hundred.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
        )
        == OWNER
    )


@pytest.mark.asyncio
async def test_assign_runtime_allowlist_forces_orchestrator_ignoring_ratio():
    router = _router(
        settings=_settings(
            orchestrator_user_allowlist=["user-1"], orchestrator_fast_ratio=0
        )
    )
    owner = await router.assign_runtime(
        room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
    )
    assert owner == OWNER


@pytest.mark.asyncio
async def test_assign_runtime_allowlist_is_exclusive_when_set():
    router = _router(
        settings=_settings(
            orchestrator_user_allowlist=["other"], orchestrator_fast_ratio=100
        )
    )
    owner = await router.assign_runtime(
        room_id="room-1", client_request_id="req-1", user_id="user-1", mode="direct"
    )
    assert owner == LEGACY


@pytest.mark.asyncio
async def test_assign_runtime_mode_mapping():
    fast = _router(settings=_settings(orchestrator_fast_ratio=100))
    ultimate = _router(settings=_settings(orchestrator_ultimate_ratio=100))
    assert (
        await fast.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id=None, mode="direct"
        )
        == OWNER
    )
    assert (
        await fast.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id=None, mode="fast"
        )
        == OWNER
    )
    assert (
        await ultimate.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id=None, mode="supervisor"
        )
        == OWNER
    )
    assert (
        await ultimate.assign_runtime(
            room_id="room-1", client_request_id="req-1", user_id=None, mode="ultimate"
        )
        == OWNER
    )


@pytest.mark.asyncio
async def test_assign_runtime_missing_profile_is_legacy():
    router = _router(
        settings=_settings(orchestrator_ultimate_ratio=100),
        runtime=_runtime(profiles={"fast": object()}),
    )
    owner = await router.assign_runtime(
        room_id="room-1", client_request_id="req-1", user_id=None, mode="supervisor"
    )
    assert owner == LEGACY


@pytest.mark.asyncio
async def test_assign_runtime_rejects_unservable_scope():
    router = _router(settings=_settings(orchestrator_fast_ratio=100))
    owner = await router.assign_runtime(
        room_id="room-1",
        client_request_id="req-1",
        user_id=None,
        mode="direct",
        agent_scope={"source": "legacy_team"},
    )
    assert owner == LEGACY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_scope",
    [
        {"source": "mention", "agent_ids": ["agent-1"]},
        {"source": "room_default"},
        {"source": "all_agents"},
        {"source": "saved_group", "group_id": "group-1"},
    ],
)
async def test_assign_runtime_serves_the_closed_scope_enumeration(agent_scope):
    router = _router(settings=_settings(orchestrator_fast_ratio=100))
    owner = await router.assign_runtime(
        room_id="room-1",
        client_request_id="req-1",
        user_id=None,
        mode="direct",
        agent_scope=agent_scope,
    )
    assert owner == OWNER


# -- Persisted ownership -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_run_owner_present_and_absent():
    run_store = AsyncMock()
    run_store.load = AsyncMock(return_value=object())
    router = _router(runtime=SimpleNamespace(run_store=run_store))
    assert await router.resolve_run_owner("run-1") == OWNER

    run_store.load = AsyncMock(return_value=None)
    assert await router.resolve_run_owner("run-2") == LEGACY


@pytest.mark.asyncio
async def test_resolve_run_owner_by_user_message():
    run_store = AsyncMock()
    run_store.load_by_user_message_id = AsyncMock(
        side_effect=lambda message_id: object() if message_id == "msg-1" else None
    )
    router = _router(runtime=SimpleNamespace(run_store=run_store))
    assert await router.resolve_run_owner_by_user_message("msg-1") == OWNER
    assert await router.resolve_run_owner_by_user_message("missing") == LEGACY
    run_store.load.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_run_owner_ignores_flags_for_existing_runs():
    run_store = AsyncMock()
    run_store.load = AsyncMock(return_value=object())
    router = _router(
        runtime=SimpleNamespace(run_store=run_store),
        settings=_settings(
            orchestrator_routing_enabled=False, orchestrator_kill_switch=True
        ),
    )
    assert await router.resolve_run_owner("run-1") == OWNER


@pytest.mark.asyncio
async def test_resolve_call_and_interaction_owner():
    ledger = AsyncMock()
    ledger.load_by_record_id = AsyncMock(
        side_effect=lambda call_record_id: (
            object() if call_record_id == "call-1" else None
        )
    )
    ledger.find_by_task_id = AsyncMock(
        side_effect=lambda task_id: object() if task_id == "task-1" else None
    )
    hitl_store = AsyncMock()
    hitl_store.load_interaction = AsyncMock(
        side_effect=lambda interaction_id: (
            object() if interaction_id == "interaction-1" else None
        )
    )
    router = _router(runtime=SimpleNamespace(call_ledger=ledger, hitl_store=hitl_store))
    assert (
        await router.resolve_call_owner(
            binding_scope=None, task_id=None, context_id=None, call_record_id="call-1"
        )
        == OWNER
    )
    assert (
        await router.resolve_call_owner(
            binding_scope=None, task_id="task-1", context_id=None, call_record_id=None
        )
        == OWNER
    )
    assert (
        await router.resolve_call_owner(
            binding_scope=None, task_id=None, context_id=None, call_record_id="missing"
        )
        == LEGACY
    )
    assert await router.resolve_interaction_owner("interaction-1") == OWNER
    assert await router.resolve_interaction_owner("missing") == LEGACY


# -- Envelope resolver ---------------------------------------------------


def _user_message(
    *,
    text="hello",
    mode="direct",
    scope=None,
    attachments=None,
    extend_info=None,
):
    return SimpleNamespace(
        message_content=SimpleNamespace(
            message_text=text, attachments=attachments or []
        ),
        extend_info=extend_info
        or {
            "execution_mode": mode,
            "agent_scope": scope or {"source": "mention", "agent_ids": ["agent-1"]},
        },
    )


@pytest.mark.asyncio
async def test_envelope_resolver_maps_persisted_message_fields():
    get_user_message = AsyncMock(return_value=_user_message(text="hi", mode="ultimate"))
    list_room_agent_ids = AsyncMock(return_value=["room-agent"])
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=list_room_agent_ids,
    )
    envelope = await resolver.load_envelope(
        OrchestrationRequest(
            room_id="room-1", room_user_message_id="msg-1", user_id="user-1"
        )
    )
    assert envelope.message_text == "hi"
    assert envelope.mode == "ultimate"
    assert envelope.candidate_agent_ids == ["agent-1"]
    assert envelope.requesting_subject_id == "user-1"


@pytest.mark.asyncio
async def test_envelope_resolver_room_default_scope():
    get_user_message = AsyncMock(
        return_value=_user_message(scope={"source": "room_default"})
    )
    list_room_agent_ids = AsyncMock(return_value=["agent-2", "agent-3"])
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=list_room_agent_ids,
    )
    envelope = await resolver.load_envelope(
        OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
    )
    assert envelope.candidate_agent_ids == ["agent-2", "agent-3"]


@pytest.mark.asyncio
async def test_envelope_resolver_reconstructs_supervisor_candidate_scope():
    """The legacy supervisor preflight whitelists extend_info keys and stores
    the candidate scope as candidate_scope_source/candidate_agent_ids;
    supervisor-mode messages must still resolve to the orchestrator."""
    get_user_message = AsyncMock(
        return_value=_user_message(
            text="supervisor message",
            mode="supervisor",
            extend_info={
                "execution_mode": "supervisor",
                "orchestration": True,
                "candidate_scope_source": "mention",
                "candidate_scope_mode": "mention",
                "candidate_agent_ids": ["agent-9"],
            },
        )
    )
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=AsyncMock(return_value=[]),
    )
    envelope = await resolver.load_envelope(
        OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
    )
    assert envelope.mode == "supervisor"
    assert envelope.candidate_agent_ids == ["agent-9"]


@pytest.mark.asyncio
async def test_envelope_resolver_prefers_live_request_scope_and_mode():
    """The live route-validated mode/scope are authoritative for Run creation;
    the persisted extend_info is only the recovery/re-entry fallback."""
    get_user_message = AsyncMock(return_value=_user_message(text="hi", mode="direct"))
    listed_user_ids: list[str | None] = []

    async def list_all(user_id):
        listed_user_ids.append(user_id)
        return ["agent-all-1", "agent-all-2"]

    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=AsyncMock(return_value=["room-agent"]),
        list_all_active_agent_ids=list_all,
    )
    envelope = await resolver.load_envelope(
        OrchestrationRequest(
            room_id="room-1",
            room_user_message_id="msg-1",
            user_id="user-7",
            mode="supervisor",
            agent_scope={"source": "all_agents"},
        )
    )
    assert envelope.mode == "supervisor"
    assert envelope.candidate_agent_ids == ["agent-all-1", "agent-all-2"]
    assert envelope.requesting_subject_id == "user-7"
    # The visibility-filtered listing receives the requesting user.
    assert listed_user_ids == ["user-7"]


@pytest.mark.asyncio
async def test_envelope_resolver_all_agents_requires_bound_callback():
    get_user_message = AsyncMock(return_value=_user_message(text="hi"))
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=AsyncMock(return_value=[]),
    )
    with pytest.raises(UnsupportedEnvelopeError):
        await resolver.load_envelope(
            OrchestrationRequest(
                room_id="room-1",
                room_user_message_id="msg-1",
                agent_scope={"source": "all_agents"},
            )
        )


@pytest.mark.asyncio
async def test_envelope_resolver_appends_attachment_text_projections():
    """Attachment text projections must reach the kernel's user message so
    the LLM can carry attachment facts into agent tasks."""
    attachment = {
        "file_id": "file-1",
        "mime_type": "application/pdf",
        "size_bytes": 100,
    }
    get_user_message = AsyncMock(
        return_value=_user_message(text="quote this policy", attachments=[attachment])
    )

    async def read_text(envelope):
        return "Policy: Acme risk profile."

    resolver = RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=AsyncMock(return_value=[]),
        attachment_text_reader=read_text,
    )
    envelope = await resolver.load_envelope(
        OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
    )
    assert envelope.attachment_texts == [
        "[attachment file-1 (application/pdf)]:\nPolicy: Acme risk profile."
    ]


@pytest.mark.asyncio
async def test_process_room_user_message_includes_attachment_text_in_user_message():
    host = _FakeSessionHost()
    envelope = RoomMessageEnvelope(
        message_text="quote this policy",
        mode="direct",
        candidate_agent_ids=["agent-1"],
        attachment_texts=[
            "[attachment file-1 (application/pdf)]:\nPolicy: Acme risk profile."
        ],
        requesting_subject_id="user-1",
    )
    router = _router(
        runtime=_adapter_runtime(host),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    host.prompt.return_value = SimpleNamespace(run=SimpleNamespace(run_id="run-1"))
    request = OrchestrationRequest(
        room_id="room-1",
        room_user_message_id="msg-1",
        user_id="user-1",
        client_request_id="req-1",
    )

    await router.process_room_user_message(request)

    message = host.prompt.await_args.args[1]
    assert message.content[0].text == "quote this policy"
    assert "Policy: Acme risk profile." in message.content[1].text


@pytest.mark.asyncio
async def test_process_room_user_message_empty_scope_falls_back():
    host = _FakeSessionHost()
    envelope = RoomMessageEnvelope(
        message_text="hello agents",
        mode="direct",
        candidate_agent_ids=[],
        requesting_subject_id="user-1",
    )
    router = _router(
        runtime=_adapter_runtime(host),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    request = OrchestrationRequest(
        room_id="room-1",
        room_user_message_id="msg-1",
        user_id="user-1",
        client_request_id="req-1",
    )

    with pytest.raises(UnsupportedEnvelopeError):
        await router.process_room_user_message(request)

    host.create_session.assert_not_awaited()
    host.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_envelope_resolver_rejects_missing_message():
    resolver = RoomMessageEnvelopeResolver(
        get_user_message=AsyncMock(return_value=None),
        list_room_agent_ids=AsyncMock(return_value=[]),
    )
    with pytest.raises(UnsupportedEnvelopeError):
        await resolver.load_envelope(
            OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
        )


# -- Message adapter -----------------------------------------------------


class _FakeEnvelopeSource:
    def __init__(self, envelope):
        self.envelope = envelope

    async def load_envelope(self, request):
        return self.envelope


class _FakeSessionHost:
    def __init__(self):
        self.create_session = AsyncMock()
        self.prompt = AsyncMock()
        self.drop_session = MagicMock()
        self._session = object()

    def get_session(self, room_id):
        return None


def _adapter_runtime(session_host, *, profile=None):
    epoch_store = AsyncMock()
    epoch_store.read_active = AsyncMock(return_value=SimpleNamespace(epoch=1))
    catalog = AsyncMock()
    catalog.prepare = AsyncMock(return_value=SimpleNamespace(snapshot=object()))
    profile = profile or SimpleNamespace(
        initial_routing="explicit_agent_first", finalization="pass_through"
    )
    return SimpleNamespace(
        profiles={"fast": profile},
        session_host=session_host,
        epoch_store=epoch_store,
        catalog_assembler=catalog,
    )


@pytest.mark.asyncio
async def test_process_room_user_message_drives_session_prompt():
    host = _FakeSessionHost()
    host.prompt.return_value = SimpleNamespace(run=SimpleNamespace(run_id="run-1"))
    envelope = RoomMessageEnvelope(
        message_text="hello agents",
        mode="direct",
        candidate_agent_ids=["agent-1"],
        requesting_subject_id="user-1",
    )
    router = _router(
        runtime=_adapter_runtime(host),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    request = OrchestrationRequest(
        room_id="room-1",
        room_user_message_id="msg-1",
        user_id="user-1",
        client_request_id="req-1",
    )
    response = await router.process_room_user_message(request)

    host.create_session.assert_awaited_once()
    host.prompt.assert_awaited_once()
    message = host.prompt.await_args.args[1]
    assert message.message_id == "msg-1"
    assert message.content[0].text == "hello agents"
    assert host.prompt.await_args.kwargs["client_request_id"] == "req-1"
    assert response.success is True
    assert response.task_id == "run-1"


@pytest.mark.asyncio
async def test_process_room_user_message_rebuilds_idle_session_per_message():
    host = _FakeSessionHost()
    idle = SimpleNamespace(has_active_run=AsyncMock(return_value=False))
    host.get_session = lambda room_id: idle
    host.prompt.return_value = SimpleNamespace(run=SimpleNamespace(run_id="run-2"))
    envelope = RoomMessageEnvelope(
        message_text="second message",
        mode="direct",
        candidate_agent_ids=["agent-1"],
        requesting_subject_id="user-1",
    )
    router = _router(
        runtime=_adapter_runtime(host),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    request = OrchestrationRequest(
        room_id="room-1",
        room_user_message_id="msg-2",
        user_id="user-1",
        client_request_id="req-2",
    )

    response = await router.process_room_user_message(request)

    # An idle session pins a stale Run id and catalog, so it is dropped and
    # replaced by a freshly prepared session for the new message.
    host.drop_session.assert_called_once_with("room-1")
    host.create_session.assert_awaited_once()
    host.prompt.assert_awaited_once()
    assert response.task_id == "run-2"


@pytest.mark.asyncio
async def test_process_room_user_message_rejects_active_session():
    host = _FakeSessionHost()
    active = SimpleNamespace(has_active_run=AsyncMock(return_value=True))
    host.get_session = lambda room_id: active
    envelope = RoomMessageEnvelope(
        message_text="busy room",
        mode="direct",
        candidate_agent_ids=["agent-1"],
        requesting_subject_id="user-1",
    )
    router = _router(
        runtime=_adapter_runtime(host),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    request = OrchestrationRequest(
        room_id="room-1",
        room_user_message_id="msg-3",
        user_id="user-1",
        client_request_id="req-3",
    )

    with pytest.raises(SessionConflict):
        await router.process_room_user_message(request)

    host.drop_session.assert_not_called()
    host.create_session.assert_not_awaited()
    host.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_room_user_message_requires_envelope_source():
    router = _router(envelope_source=None)
    with pytest.raises(UnsupportedEnvelopeError):
        await router.process_room_user_message(
            OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
        )


@pytest.mark.asyncio
async def test_process_room_user_message_rejects_reserved_finalization():
    host = _FakeSessionHost()
    profile = SimpleNamespace(
        initial_routing="explicit_agent_first", finalization="synthesize"
    )
    envelope = RoomMessageEnvelope(
        message_text="hello", mode="direct", candidate_agent_ids=["agent-1"]
    )
    router = _router(
        runtime=_adapter_runtime(host, profile=profile),
        envelope_source=_FakeEnvelopeSource(envelope),
    )
    with pytest.raises(UnsupportedEnvelopeError):
        await router.process_room_user_message(
            OrchestrationRequest(room_id="room-1", room_user_message_id="msg-1")
        )


# -- Ingress routing -----------------------------------------------------


@pytest.mark.asyncio
async def test_route_webhook_records_orchestrator_observation():
    call = SimpleNamespace(
        call_record_id="call-1",
        assistant_message_id="assistant-1",
        endpoint_scope_digest="scope-digest",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        agent_id="agent-1",
    )
    ledger = AsyncMock()
    ledger.find_by_task_id = AsyncMock(return_value=call)
    ledger.load_by_record_id = AsyncMock(return_value=call)
    ingress = AsyncMock()
    ingress.record = AsyncMock(return_value=("accepted", object()))
    verifier = AsyncMock(return_value=(True, ""))
    router = _router(
        runtime=SimpleNamespace(call_ledger=ledger, observation_ingress=ingress),
        webhook_token_verifier=verifier,
    )

    owner = await router.route_webhook(
        message_id="task-1",
        payload={
            "statusUpdate": {"status": {"state": "completed"}, "taskId": "task-1"}
        },
        token="valid-token",
    )
    assert owner == OWNER
    ledger.find_by_task_id.assert_awaited_with("task-1")
    verifier.assert_awaited_once_with("assistant-1", "valid-token")
    ingress.record.assert_awaited_once()
    observation = ingress.record.await_args.args[0]
    assert observation.call_record_id == "call-1"
    assert observation.binding_scope == "scope-digest"
    assert observation.event_kind == "terminal"


@pytest.mark.asyncio
async def test_route_webhook_falls_back_to_call_record_id():
    call = SimpleNamespace(
        call_record_id="call-1",
        assistant_message_id="assistant-1",
        endpoint_scope_digest="scope-digest",
        a2a_task_id=None,
        a2a_context_id=None,
        agent_id="agent-1",
    )
    ledger = AsyncMock()
    ledger.find_by_task_id = AsyncMock(return_value=None)
    ledger.load_by_record_id = AsyncMock(return_value=call)
    ingress = AsyncMock()
    ingress.record = AsyncMock(return_value=("accepted", object()))
    router = _router(
        runtime=SimpleNamespace(call_ledger=ledger, observation_ingress=ingress),
        webhook_token_verifier=AsyncMock(return_value=(True, "")),
    )

    owner = await router.route_webhook(
        message_id="call-1", payload={}, token="valid-token"
    )
    assert owner == OWNER
    ledger.find_by_task_id.assert_awaited_with("call-1")
    ledger.load_by_record_id.assert_awaited_with("call-1")


@pytest.mark.asyncio
async def test_route_webhook_rejects_invalid_token():
    call = SimpleNamespace(
        call_record_id="call-1",
        assistant_message_id="assistant-1",
        endpoint_scope_digest="scope-digest",
        a2a_task_id="task-1",
        a2a_context_id=None,
        agent_id="agent-1",
    )
    ledger = AsyncMock()
    ledger.find_by_task_id = AsyncMock(return_value=call)
    ledger.load_by_record_id = AsyncMock(return_value=None)
    ingress = AsyncMock()
    router = _router(
        runtime=SimpleNamespace(call_ledger=ledger, observation_ingress=ingress),
        webhook_token_verifier=AsyncMock(return_value=(False, "invalid_token")),
    )

    with pytest.raises(WebhookAuthenticationError) as exc:
        await router.route_webhook(message_id="task-1", payload={}, token="bad-token")
    assert exc.value.status_code == 401
    ingress.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_webhook_legacy_owner_stays_legacy():
    ledger = AsyncMock()
    ledger.find_by_task_id = AsyncMock(return_value=None)
    ledger.load_by_record_id = AsyncMock(return_value=None)
    ingress = AsyncMock()
    router = _router(
        runtime=SimpleNamespace(call_ledger=ledger, observation_ingress=ingress)
    )
    owner = await router.route_webhook(message_id="missing", payload={}, token="t")
    assert owner == LEGACY
    ingress.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_hitl_answer_through_port():
    spec = A2AInteractionSpec(
        schema_version=1,
        interaction_id="interaction-1",
        questions=[
            HITLQuestionSpec(
                question_id="q1",
                interaction_kind="questionnaire",
                prompt="Proceed?",
                answer_kind=HITLAnswerKind.TEXT,
            )
        ],
    )
    route = HITLRouteSnapshotV2(
        orchestration_run_id="run-1",
        call_record_id="call-1",
        invocation_id="inv-1",
        room_id="room-1",
        room_epoch=1,
        binding_id="binding-1",
        agent_id="agent-1",
        interaction_revision=1,
        interaction_fingerprint="fp",
    )
    hitl_port = AsyncMock()
    hitl_port.read_interaction = AsyncMock(return_value=(spec, route, "fp"))
    hitl_port.answer = AsyncMock(return_value="digest")
    router = _router(runtime=SimpleNamespace(hitl_port=hitl_port))

    digest = await router.route_hitl_answer(
        interaction_id="interaction-1",
        answers=[{"request_id": "q1", "user_input": "yes"}],
        responder_id="user-1",
        room_id="room-1",
    )
    assert digest == "digest"
    hitl_port.answer.assert_awaited_once()
    answers = hitl_port.answer.await_args.kwargs["answers"]
    assert answers == [
        HITLQuestionAnswer(question_id="q1", answer=HITLTextAnswer(text="yes"))
    ]


@pytest.mark.asyncio
async def test_route_hitl_answer_rejects_room_mismatch():
    spec = A2AInteractionSpec(
        schema_version=1,
        interaction_id="interaction-1",
        questions=[
            HITLQuestionSpec(
                question_id="q1",
                interaction_kind="questionnaire",
                prompt="Proceed?",
                answer_kind=HITLAnswerKind.TEXT,
            )
        ],
    )
    route = HITLRouteSnapshotV2(
        orchestration_run_id="run-1",
        call_record_id="call-1",
        invocation_id="inv-1",
        room_id="room-1",
        room_epoch=1,
        binding_id="binding-1",
        agent_id="agent-1",
        interaction_revision=1,
        interaction_fingerprint="fp",
    )
    hitl_port = AsyncMock()
    hitl_port.read_interaction = AsyncMock(return_value=(spec, route, "fp"))
    hitl_port.answer = AsyncMock(return_value="digest")
    router = _router(runtime=SimpleNamespace(hitl_port=hitl_port))

    with pytest.raises(HITLRoomMismatchError):
        await router.route_hitl_answer(
            interaction_id="interaction-1",
            answers=[{"request_id": "q1", "user_input": "yes"}],
            responder_id="user-1",
            room_id="room-2",
        )
    hitl_port.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_cancellation_through_coordinator():
    coordinator = AsyncMock()
    coordinator.cancel_run = AsyncMock(return_value={"call-1": "canceled"})
    router = _router(runtime=SimpleNamespace(cancellation_coordinator=coordinator))
    result = await router.route_cancellation("run-1", reason="user:cancel")
    assert result == {"call-1": "canceled"}
    coordinator.cancel_run.assert_awaited_once_with(
        "run-1", reason="user:cancel", deletion_id=None
    )


@pytest.mark.asyncio
async def test_route_cancellation_by_user_message():
    run = SimpleNamespace(run_id="run-1")
    run_store = AsyncMock()
    run_store.load_by_user_message_id = AsyncMock(return_value=run)
    coordinator = AsyncMock()
    coordinator.cancel_run = AsyncMock(return_value={"call-1": "canceled"})
    router = _router(
        runtime=SimpleNamespace(
            run_store=run_store, cancellation_coordinator=coordinator
        )
    )
    result = await router.route_cancellation_by_user_message(
        "msg-1", reason="user:cancel"
    )
    assert result == {"call-1": "canceled"}
    run_store.load_by_user_message_id.assert_awaited_once_with("msg-1")
    coordinator.cancel_run.assert_awaited_once_with(
        "run-1", reason="user:cancel", deletion_id=None
    )


# -- Facade seam wiring --------------------------------------------------


class RecordingTaskFactory:
    def __call__(self, coro, *, name=None):
        return asyncio.create_task(coro, name=name)


def _make_facade(**overrides):
    room_center = SimpleNamespace(
        get_idempotent_user_message=AsyncMock(return_value=None),
        send_message_to_room=AsyncMock(),
    )

    async def persist_message_to_room(*args, **kwargs):
        response = await room_center.send_message_to_room(*args, **kwargs)
        return response, response if getattr(response, "message_id", None) else None

    async def run_message_preflight_to_room(context):
        return context

    room_center.persist_message_to_room = AsyncMock(side_effect=persist_message_to_room)
    room_center.run_message_preflight_to_room = AsyncMock(
        side_effect=run_message_preflight_to_room
    )
    room_center.discard_message_preflight = MagicMock()
    room_center.update_user_message_orchestration_status = AsyncMock(return_value=True)
    room_message_center = SimpleNamespace(process_room_user_message=AsyncMock())
    hitl_manager = SimpleNamespace(
        handle_batch_response=AsyncMock(),
        get_pending_requests=AsyncMock(return_value=[]),
        cancel_interaction_by_user=AsyncMock(return_value=6),
    )
    run_lifecycle = SimpleNamespace(
        heal_diverged_runs=AsyncMock(return_value=2),
        record_processing_status=AsyncMock(return_value=None),
        project_run_state=AsyncMock(return_value={"event_id": "cancel-event"}),
    )
    run_reader = SimpleNamespace(
        get_run=AsyncMock(return_value=None),
        get_runs_for_room=AsyncMock(return_value=[]),
    )
    cancellation_state = SimpleNamespace(
        cancel_message_and_broadcast=AsyncMock(),
        get_active_token=MagicMock(return_value=None),
        release_active_token=MagicMock(return_value=True),
        clear_cancellation=MagicMock(),
    )
    cancellation_repository = SimpleNamespace(
        request=AsyncMock(return_value=True),
        mark_reconciled=AsyncMock(return_value=True),
    )
    deps = {
        "room_center": room_center,
        "room_message_center": room_message_center,
        "hitl_manager": hitl_manager,
        "run_lifecycle": run_lifecycle,
        "run_reader": run_reader,
        "cancellation_state": cancellation_state,
        "cancellation_repository": cancellation_repository,
        "cancellation_message_reader": AsyncMock(return_value=None),
        "hitl_message_cancellation": SimpleNamespace(
            cancel_requests_for_message=AsyncMock()
        ),
        "agent_task_cleanup": SimpleNamespace(
            cleanup_cancelled_message_tasks=AsyncMock()
        ),
        "agent_response_handler": SimpleNamespace(handle=AsyncMock()),
        "event_publisher": SimpleNamespace(emit=AsyncMock()),
        "run_event_enabled": lambda: False,
        "client_request_id_resolver": SimpleNamespace(
            resolve_client_request_id=AsyncMock(
                side_effect=lambda _, provided: provided
            )
        ),
        "task_factory": RecordingTaskFactory(),
    }
    deps.update(overrides)
    facade = ExecutionFacade(**deps)
    return facade, deps


@pytest.mark.asyncio
async def test_facade_schedules_orchestrator_when_assigned():
    router = SimpleNamespace(
        assign_runtime=AsyncMock(return_value=OWNER),
        preflight_room_user_message=AsyncMock(return_value=None),
        process_room_user_message=AsyncMock(return_value=None),
    )
    facade, deps = _make_facade(orchestrator_router=router)
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        client_request_id="req-1",
        mode="direct",
        message_text="hi",
    )
    ack = ExecutionAck(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        should_start_orchestration=True,
    )
    await facade.start_orchestration(request, ack)
    router.assign_runtime.assert_awaited_once()
    router.preflight_room_user_message.assert_awaited_once()
    router.process_room_user_message.assert_awaited_once()
    deps["room_message_center"].process_room_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_facade_envelope_fallback_when_preflight_unsupported():
    router = SimpleNamespace(
        assign_runtime=AsyncMock(return_value=OWNER),
        preflight_room_user_message=AsyncMock(
            side_effect=UnsupportedEnvelopeError("attachment-only")
        ),
        process_room_user_message=AsyncMock(),
    )
    facade, deps = _make_facade(orchestrator_router=router)
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        client_request_id="req-1",
        mode="direct",
        message_text="hi",
    )
    ack = ExecutionAck(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        should_start_orchestration=True,
    )
    await facade.start_orchestration(request, ack)
    router.preflight_room_user_message.assert_awaited_once()
    router.process_room_user_message.assert_not_awaited()
    deps["room_message_center"].process_room_user_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_facade_cancel_routes_orchestrator_run():
    router = SimpleNamespace(
        resolve_run_owner_by_user_message=AsyncMock(return_value=OWNER),
        route_cancellation_by_user_message=AsyncMock(
            return_value={"call-1": "canceled"}
        ),
    )
    facade, _ = _make_facade(orchestrator_router=router)
    result = await facade.cancel("room-1", "msg-1", requested_by_user_id="user-1")
    router.resolve_run_owner_by_user_message.assert_awaited_once_with("msg-1")
    router.route_cancellation_by_user_message.assert_awaited_once()
    assert isinstance(result, CancellationAck)
    assert result.status == "canceled"
    assert result.cancellation_applied is True
    assert result.reconciled is True


@pytest.mark.asyncio
async def test_facade_cancel_does_not_fall_back_when_orchestrator_routing_fails():
    router = SimpleNamespace(
        resolve_run_owner_by_user_message=AsyncMock(return_value=OWNER),
        route_cancellation_by_user_message=AsyncMock(
            side_effect=RuntimeError("routing failed")
        ),
    )
    facade, deps = _make_facade(orchestrator_router=router)

    with pytest.raises(RuntimeError, match="routing failed"):
        await facade.cancel("room-1", "msg-1", requested_by_user_id="user-1")

    router.route_cancellation_by_user_message.assert_awaited_once_with(
        "msg-1", reason="user:user-1"
    )
    deps["cancellation_repository"].request.assert_not_awaited()


@pytest.mark.asyncio
async def test_facade_cancel_ack_reflects_partial_failure():
    router = SimpleNamespace(
        resolve_run_owner_by_user_message=AsyncMock(return_value=OWNER),
        route_cancellation_by_user_message=AsyncMock(
            return_value={"call-1": "canceled", "call-2": "cancel_pending"}
        ),
    )
    facade, _ = _make_facade(orchestrator_router=router)
    result = await facade.cancel("room-1", "msg-1", requested_by_user_id="user-1")
    assert isinstance(result, CancellationAck)
    assert result.status == "cancellation_pending"
    assert result.cancellation_applied is False
    assert result.reconciled is False


@pytest.mark.asyncio
async def test_facade_hitl_batch_routes_orchestrator_interaction():
    router = SimpleNamespace(
        resolve_interaction_owner=AsyncMock(return_value=OWNER),
        route_hitl_answer=AsyncMock(return_value="digest"),
    )
    facade, deps = _make_facade(orchestrator_router=router)
    result = await facade.resolve_hitl_batch(
        "room-1",
        "interaction-1",
        [{"request_id": "q1", "user_input": "yes"}],
        "user-1",
    )
    router.route_hitl_answer.assert_awaited_once_with(
        interaction_id="interaction-1",
        answers=[{"request_id": "q1", "user_input": "yes"}],
        responder_id="user-1",
        room_id="room-1",
    )
    deps["hitl_manager"].handle_batch_response.assert_not_awaited()
    assert result.status == "accepted"
