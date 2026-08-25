from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from a2a_adapter.orchestrator_direct_client import (
    DirectCallAddress,
    OrchestratorDirectA2AClient,
    endpoint_scope_digest,
)
from a2a_adapter.task_status import (
    build_completed_text_task,
    build_failed_text_task,
    build_task_status,
)
from common.types import (
    Artifact,
    DataPart,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TextPart,
)
from execution.orchestrator.a2a_runtime.errors import (
    RecoverableAdapterError,
    StaleRoomEpochError,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ACancellationCommand,
    A2AContinuationCommand,
    A2ADispatchCommand,
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from execution.orchestrator.models import ToolResult

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


async def test_send_inline_data_artifact_reaches_observation_with_text():
    """Agents attach structured documents (JSON data parts) alongside a text
    summary; both must reach the kernel or it cannot read the real document
    and re-dispatches the same Agent until the budget runs out."""
    status = build_task_status(TaskState.completed)
    status.message = Message(
        role=MessageRole.AGENT,
        parts=[TextPart(text="Quote ready, see attached document.")],
        message_id="msg-1",
    )
    artifact = Artifact(
        artifact_id="artifact-1",
        name="cyber_quote",
        parts=[DataPart(data={"premium": "USD 35,700", "limit": 5000000})],
    )
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=status,
        artifacts=[artifact],
    )

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    receipt = await _client(FakeSdk(send=send)).send(_dispatch_command())

    observation = receipt.terminal_observation
    assert observation is not None
    texts = [part.text for part in observation.content if part.kind == "text"]
    assert texts == ["Quote ready, see attached document."]
    data = [part.data for part in observation.content if part.kind == "data"]
    assert data == [{"premium": "USD 35,700", "limit": 5000000}]


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


async def test_open_stream_accumulates_artifact_refs_into_terminal_tool_result():
    """artifact-update materializes bytes; terminal status-update must keep refs."""
    image_bytes = b"png-binary-for-stream-accumulation"
    encoded_b64 = base64.b64encode(image_bytes).decode()

    class EpochOwner:
        async def commit(self, **kwargs):
            return "/api/v1/files/stream-file-1/content"

    async def stream(card, message, kwargs):
        yield {
            "result": {
                "append": False,
                "artifact": {
                    "artifactId": "art-img",
                    "name": "cover.png",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "name": "cover.png",
                                "mimeType": "image/png",
                                "bytes": encoded_b64,
                            },
                        }
                    ],
                },
                "contextId": "ctx-1",
                "kind": "artifact-update",
                "lastChunk": True,
                "taskId": "task-1",
            }
        }
        yield {
            "result": {
                "contextId": "ctx-1",
                "final": True,
                "kind": "status-update",
                "status": {
                    "message": {
                        "parts": [{"kind": "text", "text": "cover ready"}],
                        "role": "agent",
                    },
                    "state": "completed",
                },
                "taskId": "task-1",
            }
        }

    client = _client(FakeSdk(stream=stream), epoch_owner=EpochOwner())
    stream_obj = await client.open_stream(_dispatch_command())
    events = [event async for event in stream_obj]
    await stream_obj.close(reason="terminal")

    assert len(events) == 2
    assert events[0].artifact_refs == ["/api/v1/files/stream-file-1/content"]
    assert events[1].event_kind == "terminal"
    assert events[1].artifact_refs == ["/api/v1/files/stream-file-1/content"]

    tool_result = ToolResult(
        call_id="inv-1",
        tool_name="agent_tool",
        status="completed",
        content=list(events[1].content or []),
        artifact_refs=list(events[1].artifact_refs or []),
        error_code=None,
        error_message=None,
    )
    assert tool_result.artifact_refs == ["/api/v1/files/stream-file-1/content"]


async def test_send_reraises_recoverable_materialization_errors():
    encoded_b64 = base64.b64encode(b"png-bytes").decode()
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=build_task_status(TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="art-1",
                name="cover.png",
                parts=[
                    Part(
                        root=FilePart(
                            file=FileContent(
                                name="cover.png",
                                mime_type="image/png",
                                bytes=encoded_b64,
                            )
                        )
                    )
                ],
            )
        ],
    )

    class StaleEpochOwner:
        async def commit(self, **kwargs):
            raise StaleRoomEpochError("artifact Room epoch is no longer active")

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    client = _client(FakeSdk(send=send), epoch_owner=StaleEpochOwner())
    with pytest.raises(StaleRoomEpochError, match="no longer active"):
        await client.send(_dispatch_command())

    class TransientOwner:
        async def commit(self, **kwargs):
            raise RecoverableAdapterError(
                "Room artifact owner is temporarily unavailable"
            )

    client = _client(FakeSdk(send=send), epoch_owner=TransientOwner())
    with pytest.raises(RecoverableAdapterError, match="temporarily unavailable"):
        await client.send(_dispatch_command())


async def test_send_malformed_artifact_stays_terminal_failure():
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=build_task_status(TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="art-1",
                name="broken.png",
                parts=[
                    Part(
                        root=FilePart(
                            file=FileContent(
                                name="broken.png",
                                mime_type="image/png",
                                bytes="not-valid-base64@@@",
                            )
                        )
                    )
                ],
            )
        ],
    )

    class EpochOwner:
        async def commit(self, **kwargs):
            raise AssertionError("commit must not run for invalid base64")

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    receipt = await _client(FakeSdk(send=send), epoch_owner=EpochOwner()).send(
        _dispatch_command()
    )
    assert receipt.outcome == "terminal"
    assert receipt.terminal_observation is not None
    assert receipt.terminal_observation.status == "failed"
    assert receipt.terminal_observation.error_code == "artifact_materialization_failed"


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


async def test_continue_task_input_required_builds_interaction_receipt():
    status = build_task_status(TaskState.input_required)
    status.message = Message(
        role=MessageRole.AGENT,
        parts=[TextPart(text="How many days will you stay?")],
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

    client = _client(FakeSdk(send=send))
    client._remember(
        _dispatch_command(),
        task_id="task-1",
        context_id="ctx-1",
    )
    receipt = await client.continue_task(_continuation_command())

    assert receipt.outcome == "interaction"
    observation = receipt.interaction_observation
    assert observation is not None
    assert observation.event_kind == "input_required"
    assert observation.content[0].text == "How many days will you stay?"


async def test_extract_interaction_spec_requires_status_message_metadata():
    """History-only metadata must not be treated as the live challenge.

    After HITL continuation the prior status message moves into history. Using
    that stale interaction_id would re-park / resend the answered challenge
    instead of waiting for the Agent's next typed status.message.
    """
    from a2a_adapter.orchestrator_direct_client import _extract_interaction_spec
    from common.a2a_constants import HYBRO_A2A_INTERACTION_METADATA_KEY

    interaction = {
        "schema_version": 1,
        "interaction_id": "travel-planner:hist-1",
        "questions": [
            {
                "question_id": "travel-details:hist-1",
                "interaction_kind": "questionnaire",
                "prompt": "How many days?",
                "answer_kind": "text",
                "required": True,
            }
        ],
    }
    history_message = Message(
        role=MessageRole.AGENT,
        parts=[TextPart(text="How many days?")],
        message_id="hist-msg",
        metadata={HYBRO_A2A_INTERACTION_METADATA_KEY: interaction},
    )
    status = build_task_status(TaskState.input_required)
    status.message = None
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=status,
        history=[history_message],
        artifacts=None,
    )

    assert _extract_interaction_spec(task) is None


async def test_continue_task_preserves_endpoint_scope_for_later_rounds():
    """Continuation commands have no endpoint_scope. Remembering them must not
    wipe the dispatch address, or the next HITL round returns delivery_uncertain.
    """
    task = build_completed_text_task(
        task_id="task-1", text="continued", context_id="ctx-1"
    )
    send_count = 0

    async def send(card, message, kwargs):
        nonlocal send_count
        send_count += 1
        return {"kind": "task", "result": _task_dict(task)}

    client = _client(FakeSdk(send=send))
    client._remember(
        _dispatch_command(),
        task_id="task-1",
        context_id="ctx-1",
    )
    first = await client.continue_task(_continuation_command())
    second = await client.continue_task(_continuation_command())

    assert first.outcome == "terminal"
    assert second.outcome == "terminal"
    assert send_count == 2


async def test_continue_task_refreshes_poisoned_address_from_resolver():
    task = build_completed_text_task(
        task_id="task-1", text="continued", context_id="ctx-1"
    )

    async def send(card, message, kwargs):
        return {"kind": "task", "result": _task_dict(task)}

    async def resolve(call_record_id: str):
        assert call_record_id == "call-1"
        return {
            "task_id": "task-1",
            "context_id": "ctx-1",
            "endpoint_scope": "https://agent.example/a2a",
            "agent_id": "agent-1",
        }

    client = _client(FakeSdk(send=send), call_resolver=resolve)
    client._addresses["call-1"] = DirectCallAddress(
        call_record_id="call-1",
        task_id="task-1",
        context_id="ctx-1",
        endpoint_scope=None,
        agent_id=None,
    )
    receipt = await client.continue_task(_continuation_command())
    assert receipt.outcome == "terminal"


async def test_unknown_task_identity_returns_delivery_uncertain():
    async def cancel(card, task_id, kwargs):
        return True

    client = _client(FakeSdk(cancel=cancel))
    receipt = await client.cancel(_cancellation_command())
    assert receipt.outcome == "delivery_uncertain"
