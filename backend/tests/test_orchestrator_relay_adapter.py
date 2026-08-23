from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from execution.orchestrator.a2a_runtime.models import (
    A2ACancellationCommand,
    A2AContinuationCommand,
    A2ADispatchCommand,
    A2ADispatchReceipt,
)
from hub_runtime_bridge.orchestrator_relay import (
    InMemoryRelayCommandJournalStore,
    RelayCommandJournal,
    RelayCommandSender,
)

NOW = datetime.now(UTC)


def _dispatch_command(**updates) -> A2ADispatchCommand:
    values = {
        "command_id": "command-1",
        "call_record_id": "call-1",
        "invocation_id": "inv-1",
        "message_id": "message-1",
        "binding_id": "binding-1",
        "agent_id": "agent-1",
        "skill_id": None,
        "endpoint_scope": "hub-1",
        "transport_kind": "relay",
        "task": "do work",
        "materialized_resources": [],
        "room_id": "room-1",
        "room_epoch": 1,
        "deadline_at": NOW + timedelta(seconds=30),
    }
    values.update(updates)
    return A2ADispatchCommand(**values)


def _continuation_command() -> A2AContinuationCommand:
    return A2AContinuationCommand(
        command_id="continuation-1",
        transport_kind="relay",
        call_record_id="call-1",
        interaction_id="interaction-1",
        interaction_revision=1,
        answer_digest="answer",
        answers=[],
        binding_id="binding-1",
        binding_digest="binding-digest",
        requesting_subject_digest=sha256(b"user-1").hexdigest(),
        task_id="task-1",
        context_id="context-1",
        room_id="room-1",
        room_epoch=1,
        created_at=NOW,
    )


def _cancellation_command() -> A2ACancellationCommand:
    return A2ACancellationCommand(
        command_id="cancel-1",
        transport_kind="relay",
        call_record_id="call-1",
        reason="stop",
        created_at=NOW,
    )


class FakeRelayService:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.dispatches = []
        self.replies = []
        self.cancels = []

    async def send_to_hub(self, command):
        self.dispatches.append(command)
        return type(
            "Result",
            (),
            {"accepted": self.accepted, "task_id": "task-1"},
        )()

    async def reply_to_hub_task(self, command):
        self.replies.append(command)
        return self.accepted

    async def cancel_hub_task(self, command):
        self.cancels.append(command)
        return self.accepted


def _journal_and_sender(relay=None):
    store = InMemoryRelayCommandJournalStore()
    journal = RelayCommandJournal(store=store, receipt_factory=A2ADispatchReceipt)
    sender = RelayCommandSender(
        relay_service=relay or FakeRelayService(),
        store=store,
        receipt_factory=A2ADispatchReceipt,
    )
    return store, journal, sender


async def test_journal_persists_and_replays_dispatch():
    store, journal, _ = _journal_and_sender()
    command = _dispatch_command()
    assert await journal.persist_dispatch(command) == "accepted"
    assert await journal.persist_dispatch(command) == "replayed"
    conflicting = _continuation_command().model_copy(update={"command_id": "command-1"})
    assert await journal.persist_continuation(conflicting) == "conflict"


async def test_journal_inspect_returns_recorded_receipt():
    store, journal, sender = _journal_and_sender()
    command = _dispatch_command()
    await journal.persist_dispatch(command)
    await sender.send_dispatch(command)
    receipt = await journal.inspect("command-1")
    assert isinstance(receipt, A2ADispatchReceipt)
    assert receipt.outcome == "accepted"


async def test_journal_inspect_missing_receipt_is_uncertain():
    store, journal, _ = _journal_and_sender()
    receipt = await journal.inspect("missing")
    assert receipt.outcome == "delivery_uncertain"


async def test_sender_dispatch_maps_to_hub_dispatch_command():
    relay = FakeRelayService()
    _, journal, sender = _journal_and_sender(relay)
    receipt = await sender.send_dispatch(_dispatch_command())
    assert receipt.outcome == "accepted"
    assert relay.dispatches[0].hub_id == "hub-1"
    assert relay.dispatches[0].agent_id == "agent-1"
    assert relay.dispatches[0].room_id == "room-1"
    assert relay.dispatches[0].payload["task"] == "do work"


async def test_sender_dispatch_offline_is_uncertain():
    relay = FakeRelayService(accepted=False)
    _, journal, sender = _journal_and_sender(relay)
    receipt = await sender.send_dispatch(_dispatch_command())
    assert receipt.outcome == "delivery_uncertain"


async def test_sender_continuation_uses_registered_address():
    relay = FakeRelayService()
    _, journal, sender = _journal_and_sender(relay)
    sender._remember(
        _dispatch_command(),
        endpoint_scope="hub-1",
        agent_id="agent-1",
        message_id="message-1",
        local_agent_id="local-1",
        task_id="task-1",
        context_id="context-1",
    )
    receipt = await sender.send_continuation(_continuation_command())
    assert receipt.outcome == "accepted"
    assert relay.replies[0].hub_id == "hub-1"
    assert relay.replies[0].task_id == "task-1"


async def test_sender_cancellation_uses_registered_address():
    relay = FakeRelayService()
    _, journal, sender = _journal_and_sender(relay)
    sender._remember(
        _dispatch_command(),
        endpoint_scope="hub-1",
        agent_id="agent-1",
        message_id="message-1",
        local_agent_id="local-1",
        task_id="task-1",
        context_id="context-1",
    )
    receipt = await sender.send_cancellation(_cancellation_command())
    assert receipt.outcome == "accepted"
    assert relay.cancels[0].hub_id == "hub-1"
    assert relay.cancels[0].task_id == "task-1"
