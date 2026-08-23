"""Unit tests for orchestrator tool artifacts and SSE parts projection."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import Artifact, RoomArtifactPart, TaskState
from container import (
    _project_orchestrator_agent_activity,
    _resolve_orchestrator_tool_artifacts,
)
from execution.orchestrator.models import (
    AssistantMessage,
    FrozenToolCatalogEntry,
    FrozenToolCatalogSnapshot,
    OrchestratorEvent,
    OrchestratorRunState,
    RunRequestSnapshot,
    ToolBatchEntry,
    ToolBindingRef,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


@pytest.mark.asyncio
async def test_resolve_orchestrator_tool_artifacts_with_room_files_metadata():
    runtime = MagicMock()
    file_storage = MagicMock()
    file_storage.get_for_room_file = AsyncMock(
        return_value={
            "file_id": "file-123",
            "file_name": "scene_forest.png",
            "mime_type": "image/png",
            "size_bytes": 10240,
            "sha256": "abc123sha",
        }
    )
    runtime.file_storage = file_storage

    run = MagicMock()
    run.room_id = "room-1"

    result = ToolResult(
        call_id="call-1",
        tool_name="image_gen",
        status="completed",
        content=[],
        artifact_refs=["/api/v1/files/file-123/content"],
    )

    artifacts, sse_parts = await _resolve_orchestrator_tool_artifacts(
        runtime=runtime,
        run=run,
        label="Image Generator Agent",
        task_id="task-1",
        result=result,
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert isinstance(artifact, Artifact)
    assert artifact.name == "scene_forest.png"
    assert len(artifact.parts) == 1
    part = artifact.parts[0].root
    assert isinstance(part, RoomArtifactPart)
    assert part.kind == "file"
    assert part.file.uri == "/api/v1/files/file-123/content"
    assert part.file.name == "scene_forest.png"
    assert part.file.mime_type == "image/png"
    assert part.metadata["file_id"] == "file-123"
    assert part.metadata["size_bytes"] == 10240

    assert len(sse_parts) == 1
    sse = sse_parts[0]
    assert sse["kind"] == "file"
    assert sse["file"]["uri"] == "/api/v1/files/file-123/content"
    assert sse["metadata"]["file_id"] == "file-123"


@pytest.mark.asyncio
async def test_project_orchestrator_agent_activity_emits_artifacts_and_sse_parts():
    now = datetime.now(UTC)
    tool_entry = FrozenToolCatalogEntry(
        binding=ToolBindingRef(binding_id="b1", binding_digest="d1"),
        definition=ToolDefinition(
            name="image_generator",
            label="Image Generator Agent",
            description="Generates images",
            input_schema={},
            execution_mode="sequential",
            side_effect_level="external",
        ),
    )
    catalog = FrozenToolCatalogSnapshot(
        catalog_id="cat-1",
        entries=[tool_entry],
        created_at=now,
    )

    call = ToolCall(
        call_id="c1",
        tool_name="image_generator",
        arguments={"task": "Draw a cat in the garden"},
    )
    assistant_msg = AssistantMessage(
        message_id="a1",
        content=[],
        tool_calls=[call],
        finish_reason="tool_calls",
        usage=None,
        created_at=now,
    )

    result = ToolResult(
        call_id="c1",
        tool_name="img_tool",
        status="completed",
        content=[],
        artifact_refs=["/api/v1/files/img-456/content"],
    )

    batch_entry = ToolBatchEntry(
        call_id="c1",
        assistant_message_id="a1",
        source_index=0,
        tool_name="img_tool",
        state="terminal",
        buffered_terminal_result=result,
    )

    run = MagicMock(spec=OrchestratorRunState)
    run.run_id = "run-1"
    run.room_id = "room-100"
    run.client_request_id = "cr-1"
    run.tool_catalog = catalog
    run.transcript = [assistant_msg]
    run.tool_batches = [MagicMock(entries=[batch_entry])]
    run.request = RunRequestSnapshot(
        request_fingerprint="fp1",
        room_epoch=1,
        user_message_id="u1",
        requesting_subject_id="sub-1",
    )

    runtime = MagicMock()
    runtime.run_store = MagicMock()
    runtime.run_store.load = AsyncMock(return_value=run)
    runtime.binding_store = MagicMock()
    runtime.binding_store.load = AsyncMock(
        return_value=MagicMock(agent_id="agent-image")
    )
    runtime.file_storage = MagicMock()
    runtime.file_storage.get_for_room_file = AsyncMock(
        return_value={
            "file_id": "img-456",
            "file_name": "cat.png",
            "mime_type": "image/png",
        }
    )

    message_store = MagicMock()
    message_store.upsert_room_agent_message = AsyncMock()

    delivery = MagicMock()
    delivery.send_task_update = AsyncMock()

    event = OrchestratorEvent(
        event_id="e1",
        event_type="message_completed",
        session_id="s1",
        run_id="run-1",
        room_id="room-100",
        room_epoch=1,
        sequence=1,
        state_version=1,
        causation_id="cause-1",
        created_at=now,
        payload={"call_id": "c1"},
    )

    await _project_orchestrator_agent_activity(
        event=event,
        runtime=runtime,
        message_store=message_store,
        delivery=delivery,
    )

    message_store.upsert_room_agent_message.assert_awaited_once()
    saved_doc = message_store.upsert_room_agent_message.call_args[0][0]
    task = saved_doc.message_content.message_task
    assert task.status.state == TaskState.completed
    assert len(task.artifacts) == 1
    assert task.artifacts[0].name == "cat.png"
    assert task.artifacts[0].parts[0].root.file.uri == "/api/v1/files/img-456/content"

    delivery.send_task_update.assert_awaited_once()
    kwargs = delivery.send_task_update.call_args.kwargs
    assert kwargs["room_id"] == "room-100"
    assert kwargs["status"] == "completed"
    assert kwargs["parts"] is not None
    assert len(kwargs["parts"]) == 1
    assert kwargs["parts"][0]["file"]["uri"] == "/api/v1/files/img-456/content"
    assert kwargs["parts"][0]["metadata"]["file_id"] == "img-456"
