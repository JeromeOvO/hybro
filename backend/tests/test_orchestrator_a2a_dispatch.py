from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from execution.orchestrator.a2a_runtime.dispatch import (
    DirectA2ADispatchAdapter,
    RelayA2ADispatchAdapter,
)
from execution.orchestrator.a2a_runtime.errors import AmbiguousRemoteEffectError
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryObservationConflictStore,
    InMemoryObservationInboxStore,
)
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ACancellationCommand,
    A2AContinuationCommand,
    A2ADispatchCommand,
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from execution.orchestrator.models import TextPart

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW


class Journal:
    def __init__(self, order, *, outcome="accepted"):
        self.order = order
        self.outcome = outcome

    async def persist_dispatch(self, command):
        self.order.append(("journal", command.command_id))
        return self.outcome

    async def persist_continuation(self, command):
        return "accepted"

    async def persist_cancellation(self, command):
        return "accepted"

    async def inspect(self, command_id):
        return A2ADispatchReceipt(outcome="accepted")


class Sender:
    def __init__(self, order):
        self.order = order

    async def send_dispatch(self, command):
        self.order.append(("send", command.command_id))
        return A2ADispatchReceipt(outcome="accepted")

    async def send_continuation(self, command):
        return A2ADispatchReceipt(outcome="accepted")

    async def send_cancellation(self, command):
        return A2ADispatchReceipt(outcome="accepted")


def command():
    return A2ADispatchCommand(
        command_id="command-1",
        call_record_id=ledger_record().call_record_id,
        invocation_id="call-1",
        message_id="message-1",
        binding_id="binding-1",
        agent_id="agent-1",
        endpoint_scope="relay-scope",
        transport_kind="relay",
        task="work",
        materialized_resources=[],
        room_id="room-1",
        room_epoch=1,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )


class DirectClient:
    def __init__(self, stream=None):
        self.calls = []
        self.stream = stream

    async def send(self, value):
        self.calls.append(("send", value.command_id))
        return A2ADispatchReceipt(outcome="accepted", task_id="task-1")

    async def start_poll(self, value):
        self.calls.append(("start-poll", value.command_id))
        return A2ADispatchReceipt(outcome="accepted", task_id="task-1")

    async def open_stream(self, value):
        self.calls.append(("stream", value.command_id))
        return self.stream

    async def inspect(self, value):
        self.calls.append(("inspect", value.command_id))
        return A2ADispatchReceipt(outcome="accepted", task_id="task-1")

    async def continue_task(self, value):
        self.calls.append(("continue", value.command_id))
        return A2ADispatchReceipt(outcome="accepted")

    async def inspect_continuation(self, value):
        self.calls.append(("inspect-continuation", value.command_id))
        return A2ADispatchReceipt(outcome="accepted")

    async def cancel(self, value):
        self.calls.append(("cancel", value.command_id))
        return A2ADispatchReceipt(outcome="accepted")

    async def inspect_cancellation(self, value):
        self.calls.append(("inspect-cancel", value.command_id))
        return A2ADispatchReceipt(outcome="accepted")


def continuation_command():
    return A2AContinuationCommand(
        command_id="continuation-1",
        transport_kind="direct",
        call_record_id=ledger_record().call_record_id,
        interaction_id="interaction-1",
        interaction_revision=1,
        answer_digest="answer",
        answers=[],
        binding_id="binding-1",
        binding_digest="binding-digest",
        requesting_subject_digest="subject",
        task_id="task-1",
        context_id="context-1",
        room_id="room-1",
        room_epoch=1,
        created_at=NOW,
    )


def cancellation_command():
    return A2ACancellationCommand(
        command_id="cancel-1",
        transport_kind="direct",
        call_record_id=ledger_record().call_record_id,
        reason="stop",
        created_at=NOW,
    )


class EventStream:
    def __init__(self, events, *, fail_after=False, block=False):
        self.events = list(events)
        self.fail_after = fail_after
        self.block = block
        self.closed = []
        self.close_event = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        if self.block:
            await self.close_event.wait()
            raise StopAsyncIteration
        if self.fail_after:
            self.fail_after = False
            raise AmbiguousRemoteEffectError("stream process died")
        raise StopAsyncIteration

    async def close(self, *, reason):
        self.closed.append(reason)
        self.close_event.set()


async def ingress_for_stream():
    ledger = InMemoryAgentCallLedgerStore()
    await ledger.insert(ledger_record())
    inbox = InMemoryObservationInboxStore()
    return A2AObservationIngress(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    ), inbox


def stream_event(*, terminal=False):
    return NormalizedA2AObservation(
        observation_id="terminal-1" if terminal else "working-1",
        source_kind="direct",
        source_identity="direct:terminal-1" if terminal else "direct:working-1",
        binding_scope="endpoint",
        event_kind="terminal" if terminal else "working",
        status="completed" if terminal else None,
        observed_at=NOW,
        task_id="task-1",
        context_id="context-1",
    )


async def test_direct_sync_poll_continuation_and_cancel_paths_are_correlated():
    client = DirectClient()
    adapter = DirectA2ADispatchAdapter(client)
    direct = command().model_copy(
        update={"transport_kind": "direct", "direct_mode": "sync"}
    )
    assert (await adapter.dispatch(direct)).task_id == "task-1"
    poll = direct.model_copy(update={"direct_mode": "poll"})
    assert (await adapter.dispatch(poll)).task_id == "task-1"
    assert (await adapter.inspect(direct)).outcome == "accepted"
    continuation = continuation_command()
    await adapter.continue_task(continuation)
    await adapter.inspect_continuation(continuation)
    cancellation = cancellation_command()
    await adapter.cancel(cancellation)
    await adapter.inspect_cancellation(cancellation)
    assert client.calls == [
        ("send", "command-1"),
        ("start-poll", "command-1"),
        ("inspect", "command-1"),
        ("continue", "continuation-1"),
        ("inspect-continuation", "continuation-1"),
        ("cancel", "cancel-1"),
        ("inspect-cancel", "cancel-1"),
    ]


async def test_direct_stream_events_enter_durable_ingress_before_terminal_receipt():
    ingress, inbox = await ingress_for_stream()
    stream = EventStream([stream_event(), stream_event(terminal=True)])
    client = DirectClient(stream)
    adapter = DirectA2ADispatchAdapter(client, observations=ingress)
    direct = command().model_copy(
        update={"transport_kind": "direct", "direct_mode": "stream"}
    )
    receipt = await adapter.dispatch(direct)
    assert receipt.outcome == "terminal"
    assert (await inbox.load("working-1")).room_id == "room-1"
    assert (await inbox.load("terminal-1")).room_epoch == 1
    assert stream.closed == ["terminal"]


async def test_direct_stream_wrong_call_identity_surfaces_contract_error():
    ingress, _ = await ingress_for_stream()
    wrong = stream_event().model_copy(update={"call_record_id": "other-call"})
    stream = EventStream([wrong])
    adapter = DirectA2ADispatchAdapter(DirectClient(stream), observations=ingress)
    direct = command().model_copy(
        update={"transport_kind": "direct", "direct_mode": "stream"}
    )
    with pytest.raises(ValueError, match="call identity changed"):
        await adapter.dispatch(direct)
    assert stream.closed == ["process_death"]


async def test_direct_stream_input_required_stops_with_interaction_receipt():
    """A mid-stream input-required frame ends the stream: the Agent's request
    is the invocation's durable result and must reach the kernel instead of
    being polled away as a still-working task."""
    ingress, inbox = await ingress_for_stream()
    interaction = NormalizedA2AObservation(
        observation_id="input-1",
        source_kind="direct",
        source_identity="direct:input-1",
        binding_scope="endpoint",
        event_kind="input_required",
        observed_at=NOW,
        task_id="task-1",
        context_id="context-1",
        content=[TextPart(text="Send the client name and coverage limit.")],
    )
    stream = EventStream([interaction])
    adapter = DirectA2ADispatchAdapter(DirectClient(stream), observations=ingress)
    direct = command().model_copy(
        update={"transport_kind": "direct", "direct_mode": "stream"}
    )

    receipt = await adapter.dispatch(direct)

    assert receipt.outcome == "interaction"
    assert receipt.interaction_observation is not None
    assert (
        receipt.interaction_observation.content[0].text
        == "Send the client name and coverage limit."
    )
    assert (await inbox.load("input-1")).room_id == "room-1"
    assert stream.closed == ["interaction"]


async def test_direct_stream_process_death_closes_and_recovers_by_inspection():
    ingress, _ = await ingress_for_stream()
    stream = EventStream([stream_event()], fail_after=True)
    client = DirectClient(stream)
    adapter = DirectA2ADispatchAdapter(client, observations=ingress)
    direct = command().model_copy(
        update={"transport_kind": "direct", "direct_mode": "stream"}
    )
    assert (await adapter.dispatch(direct)).outcome == "delivery_uncertain"
    assert stream.closed == ["process_death"]
    assert (await adapter.inspect(direct)).outcome == "accepted"


async def test_direct_stream_closes_on_deadline_and_cancellation():
    ingress, _ = await ingress_for_stream()
    deadline_stream = EventStream([], block=True)
    adapter = DirectA2ADispatchAdapter(
        DirectClient(deadline_stream), observations=ingress
    )
    deadline_command = command().model_copy(
        update={
            "transport_kind": "direct",
            "direct_mode": "stream",
            "deadline_at": datetime.now(UTC) + timedelta(milliseconds=10),
        }
    )
    assert (await adapter.dispatch(deadline_command)).outcome == "delivery_uncertain"
    assert deadline_stream.closed == ["deadline"]

    cancel_stream = EventStream([], block=True)
    client = DirectClient(cancel_stream)
    adapter = DirectA2ADispatchAdapter(client, observations=ingress)
    active = asyncio.create_task(
        adapter.dispatch(
            command().model_copy(
                update={"transport_kind": "direct", "direct_mode": "stream"}
            )
        )
    )
    while not client.calls:
        await asyncio.sleep(0)
    await adapter.cancel(cancellation_command())
    assert (await active).outcome == "delivery_uncertain"
    assert "cancelled" in cancel_stream.closed


async def test_direct_sync_terminal_receipt_preserves_terminal_observation():
    observation = stream_event(terminal=True)

    class TerminalClient(DirectClient):
        async def send(self, value):
            return A2ADispatchReceipt(
                outcome="terminal", terminal_observation=observation
            )

    receipt = await DirectA2ADispatchAdapter(TerminalClient()).dispatch(
        command().model_copy(update={"transport_kind": "direct", "direct_mode": "sync"})
    )
    assert receipt.terminal_observation == observation


async def test_relay_journals_exact_command_before_send():
    order = []
    adapter = RelayA2ADispatchAdapter(journal=Journal(order), sender=Sender(order))
    receipt = await adapter.dispatch(command())
    assert receipt.outcome == "accepted"
    assert order == [("journal", "command-1"), ("send", "command-1")]


async def test_relay_journal_exact_replay_keeps_command_identity_and_inspection():
    order = []
    journal = Journal(order, outcome="replayed")
    adapter = RelayA2ADispatchAdapter(journal=journal, sender=Sender(order))
    first = command()
    receipt = await adapter.dispatch(first)
    inspected = await adapter.inspect(first)
    assert receipt.outcome == "accepted"
    assert inspected.outcome == "accepted"
    assert order == [("journal", first.command_id), ("send", first.command_id)]
