from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from a2a_adapter.orchestrator_direct_client import (
    OrchestratorDirectA2AClient,
    endpoint_scope_digest,
)
from a2a_adapter.task_status import (
    build_completed_text_task,
    build_failed_text_task,
    build_task_status,
)
from common.types import Message, MessageRole, Task, TaskState, TextPart
from execution.orchestrator.a2a_runtime.models import (
    A2ACancellationCommand,
    A2AContinuationCommand,
    A2ADispatchCommand,
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)

NOW = datetime.now(UTC)


def _card() -> dict:
    return {"name": "Agent", "url": "https://agent.example/a2a", "version": "1.0.0"}


def _task_dict(task: Task) -> dict:
    return task.model_dump(mode="json", by_alias=True)


def _dispatch_command(**updates) -> A2ADispatchCommand:
    values = {
        "command_id": "command-1",
        "call_record_id": "call-1",
        "invocation_id": "inv-1",
        "message_id": "message-1",
        "binding_id": "binding-1",
        "agent_id": "agent-1",
        "endpoint_scope": "https://agent.example/a2a",
        "transport_kind": "direct",
        "direct_mode": "sync",
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
        transport_kind="direct",
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
        transport_kind="direct",
        call_record_id="call-1",
        reason="stop",
        created_at=NOW,
    )


class FakeSdk:
    def __init__(self, *, send=None, fetch=None, cancel=None, stream=None):
        self.send = send
        self.fetch = fetch
        self.cancel = cancel
        self.stream = stream
        self.send_calls = []
        self.fetch_calls = []
        self.cancel_calls = []

    async def send_message(self, card, message, **kwargs):
        self.send_calls.append((card, message, kwargs))
        return await self.send(card, message, kwargs)

    async def fetch_remote_task(self, card, task_id, **kwargs):
        self.fetch_calls.append((card, task_id, kwargs))
        return await self.fetch(card, task_id, kwargs)

    async def cancel_remote_task(self, card, task_id, **kwargs):
        self.cancel_calls.append((card, task_id, kwargs))
        return await self.cancel(card, task_id, kwargs)

    async def fetch_agent_card(self, url, **kwargs):
        return _card()

    def stream_message(self, card, message, **kwargs):
        return self.stream(card, message, kwargs)


def _client(sdk: FakeSdk, **kwargs) -> OrchestratorDirectA2AClient:
    return OrchestratorDirectA2AClient(
        send_message=sdk.send_message,
        stream_message=sdk.stream_message,
        cancel_remote_task=sdk.cancel_remote_task,
        fetch_remote_task=sdk.fetch_remote_task,
        fetch_agent_card=sdk.fetch_agent_card,
        receipt_factory=A2ADispatchReceipt,
        observation_factory=NormalizedA2AObservation,
        **kwargs,
    )


async def test_send_terminal_builds_terminal_receipt_and_observation():
    task = build_completed_text_task(task_id="task-1", text="done", context_id="ctx-1")

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    sdk = FakeSdk(send=send)
    receipt = await _client(sdk).send(_dispatch_command())

    assert isinstance(receipt, A2ADispatchReceipt)
    assert receipt.outcome == "terminal"
    assert receipt.task_id == "task-1"
    assert receipt.terminal_observation is not None
    obs = receipt.terminal_observation
    assert obs.event_kind == "terminal"
    assert obs.status == "completed"
    assert obs.binding_scope == endpoint_scope_digest("https://agent.example/a2a")


async def test_send_nonterminal_returns_accepted_with_task_identity():
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=build_task_status(TaskState.working),
        artifacts=None,
    )

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    receipt = await _client(FakeSdk(send=send)).send(_dispatch_command())
    assert receipt.outcome == "accepted"
    assert receipt.task_id == "task-1"
    assert receipt.context_id == "ctx-1"


async def test_send_input_required_builds_interaction_receipt():
    """An immediate input-required answer is the invocation's durable result:
    the receipt carries the Agent's request so the kernel can decide whether
    to satisfy it from context instead of polling the still-open task."""
    status = build_task_status(TaskState.input_required)
    status.message = Message(
        role=MessageRole.AGENT,
        parts=[TextPart(text="I need the client name and coverage limit.")],
        message_id="msg-1",
    )
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=status,
        artifacts=None,
    )

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    receipt = await _client(FakeSdk(send=send)).send(_dispatch_command())

    assert receipt.outcome == "interaction"
    assert receipt.task_id == "task-1"
    assert receipt.terminal_observation is None
    observation = receipt.interaction_observation
    assert observation is not None
    assert observation.event_kind == "input_required"
    assert observation.status is None
    assert observation.content[0].text == ("I need the client name and coverage limit.")


async def test_start_poll_requests_nonblocking_and_returns_accepted():
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=build_task_status(TaskState.submitted),
        artifacts=None,
    )

    async def send(card, message, kwargs):
        assert kwargs["blocking"] is False
        return {"kind": "task", "result": _task_dict(task)}

    receipt = await _client(FakeSdk(send=send)).start_poll(_dispatch_command())
    assert receipt.outcome == "accepted"
    assert receipt.task_id == "task-1"


async def test_open_stream_yields_normalized_observations():
    completed = build_completed_text_task(
        task_id="task-1", text="done", context_id="ctx-1"
    )

    async def stream(card, message, kwargs):
        yield {"result": {"task": _task_dict(completed)}}

    client = _client(FakeSdk(stream=stream))
    stream_obj = await client.open_stream(_dispatch_command())
    events = [event async for event in stream_obj]
    assert len(events) == 1
    assert isinstance(events[0], NormalizedA2AObservation)
    assert events[0].event_kind == "terminal"
    await stream_obj.close(reason="terminal")


async def test_open_stream_assigns_per_frame_identity_and_registers_address():
    working = Task(
        id="task-1",
        context_id="ctx-1",
        status=build_task_status(TaskState.working),
        artifacts=None,
    )

    async def stream(card, message, kwargs):
        yield {"result": {"task": _task_dict(working)}}
        yield {"result": {"task": _task_dict(working)}}

    client = _client(FakeSdk(stream=stream))
    command = _dispatch_command()
    stream_obj = await client.open_stream(command)
    events = [event async for event in stream_obj]
    await stream_obj.close(reason="done")

    assert len(events) == 2
    assert events[0].observation_id != events[1].observation_id
    assert events[0].source_identity != events[1].source_identity
    assert events[0].cursor == "1"
    assert events[1].cursor == "2"
    assert client._addresses[command.call_record_id].task_id == "task-1"
    assert client._addresses[command.call_record_id].context_id == "ctx-1"


async def test_inspect_terminal_uses_resolved_task_identity():
    task = build_failed_text_task(
        task_id="task-1", error_text="boom", context_id="ctx-1"
    )

    async def fetch(card, task_id, kwargs):
        return task

    client = _client(FakeSdk(fetch=fetch))
    client._remember(_dispatch_command(), task_id="task-1", context_id="ctx-1")
    receipt = await client.inspect(_dispatch_command())
    assert receipt.outcome == "terminal"
    assert receipt.terminal_observation.status == "failed"


async def test_cancel_acknowledged_returns_accepted():
    async def cancel(card, task_id, kwargs):
        return True

    client = _client(FakeSdk(cancel=cancel))
    client._remember(_dispatch_command(), task_id="task-1", context_id="ctx-1")
    receipt = await client.cancel(_cancellation_command())
    assert receipt.outcome == "accepted"
    assert receipt.task_id == "task-1"


async def test_continue_task_uses_command_task_and_context():
    task = build_completed_text_task(
        task_id="task-1", text="continued", context_id="ctx-1"
    )

    async def send(card, message, kwargs):
        assert message.task_id == "task-1"
        assert message.context_id == "context-1"
        return {"kind": "task", "result": _task_dict(task)}

    client = _client(FakeSdk(send=send))
    client._remember(
        _dispatch_command(),
        task_id="task-1",
        context_id="ctx-1",
    )
    receipt = await client.continue_task(_continuation_command())
    assert receipt.outcome == "terminal"


async def test_unknown_task_identity_returns_delivery_uncertain():
    async def cancel(card, task_id, kwargs):
        return True

    client = _client(FakeSdk(cancel=cancel))
    receipt = await client.cancel(_cancellation_command())
    assert receipt.outcome == "delivery_uncertain"
