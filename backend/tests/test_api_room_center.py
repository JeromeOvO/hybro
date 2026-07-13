"""
Unit tests for Room Center API endpoints.

Tests cover:
- Room creation
- Room settings inquiry
- Room ownership verification
- Room updates (agent set, name, extend info)
- Message creation and retrieval
- Authorization checks
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from agent.protocols import AgentSuggestion, AgentSuggestionResult
from api.room_center import (
    create_new_room,
    inquiry_active_runs,
    inquiry_room_messages,
    inquiry_room_setting,
    inquiry_rooms_by_room_owner_id,
    send_message,
    suggest_agents,
    update_room_agent_set,
    update_room_extend_info,
    update_room_name,
    verify_room_ownership,
)
from common.dto import ExecutionAck
from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)
from models.room import MessageContent, RoomAgentMessage, RoomUserMessage
from room.compat.runtime import RoomServices
from room.route_adapter import RoomRouteAdapter as RoomCenter

# =============================================================================
# Room Ownership Verification Tests
# =============================================================================


def _legacy_runtime_with_update_spy() -> RoomServices:
    runtime = RoomServices()
    runtime.update_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(success=True, error=None)
    )
    return runtime


def _legacy_agent_message(task: Task) -> RoomAgentMessage:
    return RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        agent_id="agent-1",
        related_message_id="user-message-1",
        message_content=MessageContent(message_task=task),
    )


def _persisted_runtime_message(runtime: RoomServices) -> dict:
    request = runtime.update_agent_message_by_message_id.await_args.args[0]
    return request.message.model_dump(mode="json")


def _agent_text_message(text: str, *, metadata: dict | None = None) -> Message:
    return Message(
        message_id="agent-output",
        role=MessageRole.AGENT,
        parts=[Part(root=TextPart(text=text))],
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_legacy_runtime_task_branch_projects_before_persistence():
    private_sentinel = "PRIVATE_SENTINEL_legacy_full_task"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(id="existing-task", status=TaskStatus(state=TaskState.working))
    )
    remote_task = Task(
        id="remote-task",
        contextId="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=_agent_text_message(private_sentinel),
        ),
        history=[
            Message(
                message_id="private-user-history",
                role=MessageRole.USER,
                parts=[Part(root=TextPart(text=private_sentinel))],
            ),
            _agent_text_message(
                "Public final history",
                metadata={"private": private_sentinel},
            ),
        ],
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="response",
                parts=[Part(root=TextPart(text="Public artifact text"))],
                metadata={"private": private_sentinel},
            )
        ],
        metadata={"private": private_sentinel},
    )

    assert await runtime.handle_a2a_response_for_room(room_agent_message, remote_task)

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    assert persisted_task["status"]["state"] == "completed"
    assert persisted_task["status"]["message"] is None
    assert persisted_task["metadata"] is None
    assert persisted_task["history"] is None
    assert persisted_task["artifacts"][0]["metadata"] is None
    assert "Public final history" not in json.dumps(persisted_task)
    assert "Public artifact text" in json.dumps(persisted_task)
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_legacy_runtime_message_branch_discards_non_completed_remote_text():
    private_sentinel = "PRIVATE_SENTINEL_legacy_message_branch"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(id="remote-task", status=TaskStatus(state=TaskState.working))
    )

    assert await runtime.handle_a2a_response_for_room(
        room_agent_message,
        _agent_text_message(private_sentinel),
    )

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    assert persisted_task["status"]["state"] == "working"
    assert persisted_task["history"] in (None, [])
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_legacy_runtime_message_branch_sanitizes_completed_public_output():
    private_sentinel = "PRIVATE_SENTINEL_legacy_completed_message_metadata"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(id="remote-task", status=TaskStatus(state=TaskState.completed))
    )

    assert await runtime.handle_a2a_response_for_room(
        room_agent_message,
        _agent_text_message(
            "Public final message",
            metadata={"private": private_sentinel},
        ),
    )

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    assert persisted_task["status"]["state"] == "completed"
    assert persisted_task["history"] is None
    assert persisted_task["artifacts"][0]["name"] == "response"
    assert "Public final message" in json.dumps(persisted_task)
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_legacy_runtime_status_update_persists_public_status_only():
    private_sentinel = "PRIVATE_SENTINEL_legacy_status_update"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(
            id="remote-task",
            contextId="remote-context",
            status=TaskStatus(state=TaskState.working),
            history=[_agent_text_message(private_sentinel)],
            artifacts=[
                Artifact(
                    artifact_id="artifact-1",
                    name="partial",
                    parts=[Part(root=TextPart(text=private_sentinel))],
                )
            ],
            metadata={"private": private_sentinel},
        )
    )
    status_update = TaskStatusUpdateEvent(
        id="remote-task",
        contextId="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=_agent_text_message(private_sentinel),
        ),
    )

    assert await runtime.handle_a2a_response_for_room(
        room_agent_message,
        status_update,
    )

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    assert persisted_task["status"]["state"] == "completed"
    assert persisted_task["status"]["message"] is None
    assert persisted_task["history"] in (None, [])
    assert persisted_task["artifacts"] in (None, [])
    assert persisted_task["metadata"] is None
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_legacy_runtime_artifact_update_discards_non_completed_artifacts():
    private_sentinel = "PRIVATE_SENTINEL_legacy_working_artifact"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(id="remote-task", status=TaskStatus(state=TaskState.working))
    )
    artifact_update = TaskArtifactUpdateEvent(
        id="remote-task",
        artifact=Artifact(
            artifact_id="artifact-1",
            name="partial",
            parts=[Part(root=TextPart(text=private_sentinel))],
        ),
    )

    assert await runtime.handle_a2a_response_for_room(
        room_agent_message,
        artifact_update,
    )

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    assert persisted_task["status"]["state"] == "working"
    assert persisted_task["artifacts"] in (None, [])
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_legacy_runtime_artifact_update_sanitizes_completed_artifacts():
    private_sentinel = "PRIVATE_SENTINEL_legacy_completed_artifact_bytes"
    runtime = _legacy_runtime_with_update_spy()
    room_agent_message = _legacy_agent_message(
        Task(id="remote-task", status=TaskStatus(state=TaskState.completed))
    )
    artifact_update = TaskArtifactUpdateEvent(
        id="remote-task",
        artifact=Artifact(
            artifact_id="artifact-1",
            name="final-file",
            parts=[
                Part(root=TextPart(text="Public artifact text")),
                Part(
                    root=FilePart(
                        file=FileContent(
                            bytes=private_sentinel,
                            mimeType="text/plain",
                            name="result.txt",
                        ),
                        metadata={"private": private_sentinel},
                    )
                )
            ],
            metadata={"private": private_sentinel},
        ),
    )

    assert await runtime.handle_a2a_response_for_room(
        room_agent_message,
        artifact_update,
    )

    persisted = _persisted_runtime_message(runtime)
    persisted_task = persisted["message_content"]["message_task"]
    artifact = persisted_task["artifacts"][0]
    raw_part = artifact["parts"][0]
    part = raw_part.get("root", raw_part)
    assert artifact["metadata"] is None
    assert part["text"] == "Public artifact text"
    assert all(part.get("kind") != "file" for part in artifact["parts"])
    assert private_sentinel not in json.dumps(persisted)


class TestRoomCenterAdapter:
    @pytest.mark.asyncio
    async def test_fails_before_bind_when_service_unbound(self):
        center = RoomCenter(room_services=None)

        with pytest.raises(
            RuntimeError,
            match=r"RoomRouteAdapter\.bind_facade\(\) not called - startup incomplete",
        ):
            await center.create_new_room(MagicMock())

    @pytest.mark.asyncio
    async def test_delegates_to_bound_room_services(self):
        service = MagicMock()
        service._bound = True
        service.create_new_room = AsyncMock(return_value="created")
        center = RoomCenter(room_services=service)

        assert await center.create_new_room(MagicMock()) == "created"
        service.create_new_room.assert_awaited_once()


class TestVerifyRoomOwnership:
    """Tests for verify_room_ownership helper function."""

    @pytest.mark.asyncio
    async def test_raises_400_when_room_id_empty(self, mock_user):
        """Should raise 400 when room_id is empty."""
        with pytest.raises(HTTPException) as exc_info:
            await verify_room_ownership("", mock_user, MagicMock())

        assert exc_info.value.status_code == 400
        assert "room_id is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_when_room_not_found(self, mock_user, mock_db_service):
        """Should raise 404 when room doesn't exist."""
        mock_db_service.get_room_by_room_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await verify_room_ownership("nonexistent-room", mock_user, mock_db_service)

        assert exc_info.value.status_code == 404
        assert "Room not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_403_when_user_not_owner(
        self, mock_user, mock_user_2, mock_db_service, sample_room
    ):
        """Should raise 403 when user is not the room owner."""
        # Room owned by mock_user, but mock_user_2 is trying to access
        mock_db_service.get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await verify_room_ownership(
                sample_room.room_id, mock_user_2, mock_db_service
            )

        assert exc_info.value.status_code == 403
        assert "permission" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_passes_when_user_is_owner(
        self, mock_user, mock_db_service, sample_room
    ):
        """Should pass without exception when user is the owner."""
        mock_db_service.get_room_by_room_id.return_value = sample_room

        # Should not raise
        await verify_room_ownership(sample_room.room_id, mock_user, mock_db_service)


# =============================================================================
# Room Creation Tests
# =============================================================================


class TestCreateNewRoom:
    """Tests for create_new_room endpoint."""

    @pytest.mark.asyncio
    async def test_creates_room_with_user_as_owner(self, mock_user, mock_room_center):
        """Should create room with authenticated user as owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_name": "Test Room",
                "room_owner_name": "Test User",
                "room_agent_set": {"agent-1": "Agent One"},
                "extend_info": {"debateMode": True, "use_supervisor": True},
            }
        )

        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room_id="new-room-id",
            status_code=200,
        )
        mock_room_center.create_new_room.return_value = expected_response

        response = await create_new_room(
            mock_request, mock_user, center=mock_room_center
        )

        assert response.success is True
        assert response.room_id == "new-room-id"

        # Verify the request was made with user's ID as owner
        call_args = mock_room_center.create_new_room.call_args[0][0]
        assert call_args.room_owner_id == mock_user.user_id
        assert call_args.extend_info == {"debateMode": True, "use_supervisor": True}

    @pytest.mark.asyncio
    async def test_creates_room_with_agent_group(self, mock_user, mock_room_center):
        """Should create room with applied_from_group when specified."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_name": "Group Room",
                "room_owner_name": "Test User",
                "room_agent_set": {},
                "applied_from_group": "group-123",
            }
        )

        expected_response = RoomCenterRoomSettingResponse(
            success=True, room_id="new-room-id"
        )
        mock_room_center.create_new_room.return_value = expected_response

        await create_new_room(mock_request, mock_user, center=mock_room_center)

        call_args = mock_room_center.create_new_room.call_args[0][0]
        assert call_args.applied_from_group == "group-123"


# =============================================================================
# Room Settings Inquiry Tests
# =============================================================================


class TestInquiryRoomSetting:
    """Tests for inquiry_room_setting endpoint."""

    @pytest.mark.asyncio
    async def test_returns_room_settings_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo
        from models.response import ActiveRunRef

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room_id=sample_room.room_id,
            room=sample_room,
        )
        patch_room_center_deps[
            "room_center"
        ].inquiry_room_setting.return_value = expected_response
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="m1",
                agent_id="a1",
                seq=1,
            )
        ]

        response = await inquiry_room_setting(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.room_id == sample_room.room_id
        assert response.active_runs == [
            ActiveRunRef(
                run_id="run-1",
                state="processing",
                trigger_message_id="m1",
                agent_id="a1",
                seq=1,
            )
        ]
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.assert_awaited_once_with(sample_room.room_id)

    @pytest.mark.asyncio
    async def test_inquiry_room_setting_degrades_when_active_run_lookup_fails(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps[
            "room_center"
        ].inquiry_room_setting.return_value = RoomCenterRoomSettingResponse(
            success=True,
            room_id=sample_room.room_id,
            room=sample_room,
        )
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.side_effect = RuntimeError("runs unavailable")

        response = await inquiry_room_setting(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.room_id == sample_room.room_id
        assert response.active_runs == []

    @pytest.mark.asyncio
    async def test_inquiry_room_setting_uses_requested_room_id_for_active_run_lookup(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps[
            "room_center"
        ].inquiry_room_setting.return_value = RoomCenterRoomSettingResponse(
            success=True,
            room=sample_room,
        )
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="m1",
                agent_id="a1",
                seq=1,
            )
        ]

        response = await inquiry_room_setting(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs is not None
        assert response.active_runs[0].run_id == "run-1"
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.assert_awaited_once_with(sample_room.room_id)

    @pytest.mark.asyncio
    async def test_inquiry_room_setting_ignores_mismatched_response_room_id_for_active_run_lookup(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps[
            "room_center"
        ].inquiry_room_setting.return_value = RoomCenterRoomSettingResponse(
            success=True,
            room_id="other-room",
            room=sample_room,
        )
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="m1",
                agent_id="a1",
                seq=1,
            )
        ]

        response = await inquiry_room_setting(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs is not None
        assert response.active_runs[0].run_id == "run-1"
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.assert_awaited_once_with(sample_room.room_id)

    @pytest.mark.asyncio
    async def test_raises_403_for_non_owner(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should raise 403 when user is not the owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        mock_db_service.get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await inquiry_room_setting(
                mock_request,
                mock_user_2,
                store=mock_db_service,
                engine=MagicMock(),
                center=MagicMock(),
            )

        assert exc_info.value.status_code == 403


# =============================================================================
# Active runs inquiry (lightweight reconcile)
# =============================================================================


class TestInquiryActiveRuns:
    """Tests for inquiry_active_runs endpoint."""

    @pytest.mark.asyncio
    async def test_returns_active_runs_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo
        from models.response import RoomCenterActiveRunsResponse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={"room_id": sample_room.room_id, "trigger_message_id": "m1"}
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="msg-active",
                agent_id="a1",
                seq=1,
            )
        ]
        patch_room_center_deps[
            "room_center"
        ].inquiry_active_runs.return_value = RoomCenterActiveRunsResponse(
            success=True,
            room_id=sample_room.room_id,
            active_runs=[],
            turn_completion_kind="synthesis",
        )

        response = await inquiry_active_runs(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.room_id == sample_room.room_id
        assert response.active_runs is not None
        assert len(response.active_runs) == 1
        assert response.active_runs[0].run_id == "run-1"
        assert response.turn_completion_kind == "synthesis"
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.assert_awaited_once_with(sample_room.room_id)
        patch_room_center_deps["room_center"].inquiry_active_runs.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_active_runs_without_trigger_without_room_center_lookup(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="msg-active",
                agent_id="a1",
                seq=1,
            )
        ]

        response = await inquiry_active_runs(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs is not None
        assert response.active_runs[0].run_id == "run-1"
        assert response.turn_completion_kind is None
        patch_room_center_deps["room_center"].inquiry_active_runs.assert_not_awaited()
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.assert_awaited_once_with(sample_room.room_id)

    @pytest.mark.asyncio
    async def test_suppresses_turn_completion_kind_when_requested_trigger_is_active(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo

        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={"room_id": sample_room.room_id, "trigger_message_id": "m1"}
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="m1",
                agent_id="a1",
                seq=1,
            )
        ]

        response = await inquiry_active_runs(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs is not None
        assert response.active_runs[0].trigger_message_id == "m1"
        assert response.turn_completion_kind is None
        patch_room_center_deps["room_center"].inquiry_active_runs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inquiry_active_runs_degrades_when_completion_kind_lookup_fails(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from common.dto import RunInfo

        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={"room_id": sample_room.room_id, "trigger_message_id": "m1"}
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [
            RunInfo(
                run_id="run-1",
                room_id=sample_room.room_id,
                state="processing",
                trigger_message_id="msg-active",
                agent_id="a1",
                seq=1,
            )
        ]
        patch_room_center_deps[
            "room_center"
        ].inquiry_active_runs.side_effect = RuntimeError("completion kind unavailable")

        response = await inquiry_active_runs(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs is not None
        assert response.active_runs[0].run_id == "run-1"
        assert response.turn_completion_kind is None

    @pytest.mark.asyncio
    async def test_inquiry_active_runs_degrades_when_execution_lookup_fails(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        from models.response import RoomCenterActiveRunsResponse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={"room_id": sample_room.room_id, "trigger_message_id": "m1"}
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps[
            "execution_engine"
        ].get_runs_for_room.side_effect = RuntimeError("runs unavailable")
        patch_room_center_deps[
            "room_center"
        ].inquiry_active_runs.return_value = RoomCenterActiveRunsResponse(
            success=True,
            room_id=sample_room.room_id,
            active_runs=[],
            turn_completion_kind="synthesis",
        )

        response = await inquiry_active_runs(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        assert response.active_runs == []
        assert response.turn_completion_kind == "synthesis"

    @pytest.mark.asyncio
    async def test_raises_403_for_non_owner(
        self, mock_user_2, mock_db_service, sample_room
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        mock_db_service.get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await inquiry_active_runs(
                mock_request,
                mock_user_2,
                store=mock_db_service,
                engine=MagicMock(),
                center=MagicMock(),
            )

        assert exc_info.value.status_code == 403


# =============================================================================
# Room List by Owner Tests
# =============================================================================


class TestInquiryRoomsByRoomOwnerId:
    """Tests for inquiry_rooms_by_room_owner_id endpoint."""

    @pytest.mark.asyncio
    async def test_returns_rooms_for_own_user_id(
        self, mock_user, mock_room_center, sample_room
    ):
        """Should return rooms when requesting own rooms."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_owner_id": mock_user.user_id})

        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room_list=[sample_room],
        )
        mock_room_center.inquiry_rooms_by_room_owner_id.return_value = expected_response

        response = await inquiry_rooms_by_room_owner_id(
            mock_request, mock_user, center=mock_room_center
        )

        assert response.success is True
        assert len(response.room_list) == 1

    @pytest.mark.asyncio
    async def test_raises_403_for_other_user_rooms(self, mock_user):
        """Should raise 403 when requesting another user's rooms."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_owner_id": "other-user-id"})

        with pytest.raises(HTTPException) as exc_info:
            await inquiry_rooms_by_room_owner_id(
                mock_request, mock_user, center=MagicMock()
            )

        assert exc_info.value.status_code == 403


# =============================================================================
# Room Update Tests
# =============================================================================


class TestUpdateRoomAgentSet:
    """Tests for update_room_agent_set endpoint."""

    @pytest.mark.asyncio
    async def test_updates_agent_set_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update agent set when user is owner."""
        new_agent_set = {"agent-2": "Agent Two", "agent-3": "Agent Three"}

        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "room_agent_set": new_agent_set,
            }
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps[
            "room_center"
        ].update_room_agent_set.return_value = expected_response

        response = await update_room_agent_set(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True

        # Verify requesting_user_id is passed for visibility validation
        call_args = patch_room_center_deps[
            "room_center"
        ].update_room_agent_set.call_args[0][0]
        assert call_args.requesting_user_id == mock_user.user_id


class TestUpdateRoomName:
    """Tests for update_room_name endpoint."""

    @pytest.mark.asyncio
    async def test_updates_room_name_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update room name when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "room_name": "New Room Name",
            }
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps[
            "room_center"
        ].update_room_name.return_value = expected_response

        response = await update_room_name(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        call_args = patch_room_center_deps["room_center"].update_room_name.call_args[0][
            0
        ]
        assert call_args.room_name == "New Room Name"


class TestUpdateRoomExtendInfo:
    """Tests for update_room_extend_info endpoint."""

    @pytest.mark.asyncio
    async def test_updates_extend_info_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update extend info when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "extend_info": {"custom_field": "custom_value"},
            }
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps[
            "room_center"
        ].update_room_extend_info.return_value = expected_response

        response = await update_room_extend_info(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True
        call_args = patch_room_center_deps[
            "room_center"
        ].update_room_extend_info.call_args[0][0]
        assert call_args.extend_info == {"custom_field": "custom_value"}


class TestUpdateEndpointsRejectNonOwner:
    """Non-owner is rejected for all update endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint_fn,payload",
        [
            (update_room_agent_set, {"room_id": "test-room-001", "room_agent_set": {}}),
            (update_room_name, {"room_id": "test-room-001", "room_name": "X"}),
            (update_room_extend_info, {"room_id": "test-room-001", "extend_info": {}}),
        ],
    )
    async def test_rejects_non_owner(
        self, mock_user_2, mock_db_service, sample_room, endpoint_fn, payload
    ):
        """All update endpoints should raise 403 for non-owners."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value=payload)
        mock_db_service.get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await endpoint_fn(
                mock_request,
                mock_user_2,
                store=mock_db_service,
                center=MagicMock(),
            )

        assert exc_info.value.status_code == 403


# =============================================================================
# Message Tests
# =============================================================================


class TestInquiryRoomMessages:
    """Tests for inquiry_room_messages endpoint."""

    @pytest.mark.asyncio
    async def test_returns_messages_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should return messages when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
            }
        )

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomMessageResponse(
            success=True,
            message_list=[],
        )
        patch_room_center_deps[
            "room_center"
        ].inquiry_room_messages_by_room_id.return_value = expected_response

        response = await inquiry_room_messages(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=patch_room_center_deps["room_center"],
        )

        assert response.success is True

    @pytest.mark.asyncio
    async def test_returns_public_user_message_payload_without_private_extend_info(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        private_sentinel = "PRIVATE_SENTINEL_user_extend_info_history_boundary"
        public_extend_info = {
            "quoted_text": "Public quoted excerpt",
            "quoted_sender_name": "Agent One",
            "quote_id": "quote-public-001",
            "turn_completion_kind": "synthesis",
        }
        user_message = RoomUserMessage(
            room_id=sample_room.room_id,
            message_id="user-msg-privacy-001",
            user_id=mock_user.user_id,
            client_request_id="client-request-top-level-001",
            message_content=MessageContent(message_text="Please review the quote"),
            extend_info={
                **public_extend_info,
                "client_request_id": private_sentinel,
                "orchestration": True,
                "orchestration_run_id": private_sentinel,
                "orchestration_status": private_sentinel,
                "candidate_scope_snapshot_id": private_sentinel,
                "candidate_agent_ids": [private_sentinel],
                "supervisor_trajectory": {
                    "status": "running",
                    "entries": [{"prompt": private_sentinel}],
                },
                "agent_registry": [{"agent_id": private_sentinel}],
                "conversation_context": private_sentinel,
                "room_config": {"explicit_mentions": [private_sentinel]},
                "dispatch_strategy": private_sentinel,
                "dispatch_payload_refs": {"payload": private_sentinel},
                "resolved_dispatch_resource_payloads": [
                    {"resource": private_sentinel}
                ],
                "orchestration_recovery": {"prompt": private_sentinel},
                "prompt": private_sentinel,
            },
        )
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = (
            sample_room
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[user_message])
        facade.get_agent_messages_for_room = AsyncMock(return_value=[])
        runtime = RoomServices()
        runtime.bind_facade(facade)
        runtime.bind_object_storage(
            SimpleNamespace(get_presigned_url=AsyncMock(return_value="unused"))
        )
        center = RoomCenter(room_services=runtime)

        response = await inquiry_room_messages(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=center,
        )

        assert response.success is True
        assert response.message_list is not None
        public_user = response.message_list[0]
        assert public_user.message_type == "user"
        assert public_user.client_request_id == "client-request-top-level-001"
        assert public_user.extend_info == public_extend_info
        assert private_sentinel not in json.dumps(response.model_dump(mode="json"))

    @pytest.mark.asyncio
    async def test_returns_public_agent_message_payload_without_private_dispatch_text(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        private_sentinel = "PRIVATE_SENTINEL_actual_room_runtime_boundary"
        public_label = "Requesting Insurer"
        client_request_id = "cr-insurer-001"
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = (
            sample_room
        )
        final_artifact = Artifact(
            artifact_id="artifact-final",
            name="response",
            parts=[Part(root=TextPart(text="Public final result"))],
        )
        remote_task = Task(
            id="remote-task",
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(
                    message_id="private-status",
                    role=MessageRole.USER,
                    parts=[Part(root=TextPart(text=private_sentinel))],
                ),
            ),
            history=[
                Message(
                    message_id="private-history",
                    role=MessageRole.USER,
                    parts=[Part(root=TextPart(text=private_sentinel))],
                ),
                Message(
                    message_id="public-history",
                    role=MessageRole.AGENT,
                    parts=[Part(root=TextPart(text="Public final result"))],
                ),
            ],
            artifacts=[final_artifact],
            metadata={
                "hitl_request_id": private_sentinel,
                "prompt": private_sentinel,
                "hitl_prompt": private_sentinel,
                "choices": [private_sentinel],
                "hitl_choices": [private_sentinel],
            },
        )
        local_task = Task(
            id="local-hitl-task",
            contextId="local-hitl-context",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[final_artifact],
            metadata={
                "hitl_request_id": "local-hitl-request",
                "hitl_prompt": "Choose the approved option",
                "hitl_prompt_type": "choice",
                "hitl_choices": ["Approve", "Reject"],
                "user_answer": "Approve",
            },
        )
        supervisor_task = Task(
            id="local-supervisor-hitl-task",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[final_artifact],
            metadata={"hitl_request_id": "local-supervisor-hitl-request"},
        )
        remote_message = RoomAgentMessage(
            room_id=sample_room.room_id,
            message_id="agent-msg-remote-spoof",
            agent_id="insurer-agent",
            related_message_id=sample_user_message.message_id,
            message_content=MessageContent(
                message_text=private_sentinel,
                message_task=remote_task,
            ),
            task_content=private_sentinel,
        )
        local_message = RoomAgentMessage(
            room_id=sample_room.room_id,
            message_id="agent-msg-insurer-001",
            agent_id="insurer-agent",
            related_message_id=sample_user_message.message_id,
            client_request_id=client_request_id,
            message_content=MessageContent(message_task=local_task),
            extend_info={"public_task_label": public_label},
        )
        supervisor_message = RoomAgentMessage(
            room_id=sample_room.room_id,
            message_id="supervisor-msg-clarify-001",
            agent_id="supervisor",
            related_message_id=sample_user_message.message_id,
            message_content=MessageContent(message_task=supervisor_task),
            extend_info={"public_task_label": "Clarifying request"},
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[])
        facade.get_agent_messages_for_room = AsyncMock(
            return_value=[remote_message, local_message, supervisor_message]
        )
        runtime = RoomServices()
        runtime.bind_facade(facade)
        runtime.bind_object_storage(
            SimpleNamespace(get_presigned_url=AsyncMock(return_value="unused"))
        )
        runtime.bind_store(
            SimpleNamespace(
                get_hitl_request=AsyncMock(
                    side_effect=lambda request_id: (
                        {
                            "request_id": "local-hitl-request",
                            "room_id": sample_room.room_id,
                            "source": "agent",
                            "agent_id": "insurer-agent",
                            "display_message_id": local_message.message_id,
                            "continuation_message_id": local_message.message_id,
                            "prompt": "Choose the approved option",
                            "prompt_type": "choice",
                            "choices": ["Approve", "Reject"],
                            "a2a_task_id": "local-hitl-task",
                            "a2a_context_id": "local-hitl-context",
                            "status": "responded",
                            "user_input": "Approve",
                        }
                        if request_id == "local-hitl-request"
                        else (
                            {
                                "request_id": "local-supervisor-hitl-request",
                                "room_id": sample_room.room_id,
                                "source": "supervisor",
                                "display_message_id": supervisor_message.message_id,
                                "prompt": "Which market should be prioritized?",
                                "prompt_type": "text",
                                "group_id": "supervisor-group-1",
                                "group_total": 2,
                                "group_index": 0,
                                "status": "responded",
                                "user_input": "California",
                            }
                            if request_id == "local-supervisor-hitl-request"
                            else None
                        )
                    )
                )
            )
        )
        center = RoomCenter(room_services=runtime)

        response = await inquiry_room_messages(
            mock_request,
            mock_user,
            store=patch_room_center_deps["db_service"],
            center=center,
        )

        assert response.success is True
        assert response.message_list is not None
        by_id = {message.message_id: message for message in response.message_list}
        remote_public = by_id[remote_message.message_id]
        local_public = by_id[local_message.message_id]
        supervisor_public = by_id[supervisor_message.message_id]
        assert remote_public.message_content.message_text == "Public final result"
        assert remote_public.message_content.message_task.metadata is None
        assert local_public.client_request_id == client_request_id
        assert local_public.message_content.message_task.metadata == {
            "hitl_request_id": "local-hitl-request",
            "hitl_prompt": "Choose the approved option",
            "hitl_prompt_type": "choice",
            "hitl_choices": ["Approve", "Reject"],
            "hitl_a2a_task_id": "local-hitl-task",
            "hitl_a2a_context_id": "local-hitl-context",
            "user_answer": "Approve",
        }
        assert local_public.extend_info == {
            "public_task_label": public_label,
            "hitl_request_id": "local-hitl-request",
        }
        assert supervisor_public.message_content.message_task.metadata == {
            "hitl_request_id": "local-supervisor-hitl-request",
            "hitl_prompt": "Which market should be prioritized?",
            "hitl_prompt_type": "text",
            "hitl_choices": None,
            "hitl_group_id": "supervisor-group-1",
            "hitl_group_total": 2,
            "hitl_group_index": 0,
            "user_answer": "California",
        }
        assert supervisor_public.extend_info == {
            "public_task_label": "Clarifying request",
            "hitl_request_id": "local-supervisor-hitl-request",
        }
        assert private_sentinel not in json.dumps(response.model_dump(mode="json"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "request_overrides",
        [
            {"agent_id": "other-agent"},
            {"a2a_task_id": "other-task"},
            {"a2a_context_id": "other-context"},
        ],
    )
    async def test_trusted_hitl_projection_fails_closed_on_identity_mismatch(
        self,
        request_overrides,
    ):
        private_sentinel = "PRIVATE_SENTINEL_mismatched_hitl_request"
        runtime = RoomServices()
        runtime.bind_store(
            SimpleNamespace(
                get_hitl_request=AsyncMock(
                    return_value={
                        "request_id": "local-hitl-request",
                        "room_id": "room-1",
                        "source": "agent",
                        "agent_id": "agent-1",
                        "display_message_id": "agent-message-1",
                        "a2a_task_id": "remote-task",
                        "a2a_context_id": "remote-context",
                        "prompt": private_sentinel,
                        "prompt_type": "choice",
                        "choices": [private_sentinel],
                        **request_overrides,
                    }
                )
            )
        )
        task = Task(
            id="remote-task",
            contextId="remote-context",
            status=TaskStatus(state=TaskState.input_required),
            metadata={"hitl_request_id": "local-hitl-request"},
        )
        agent_message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-message-1",
            agent_id="agent-1",
            message_content=MessageContent(message_task=task),
        )

        trusted_metadata, trusted_request_id = await runtime._trusted_hitl_projection(
            agent_message,
            task,
        )

        assert trusted_metadata is None
        assert trusted_request_id is None


class TestSendMessage:
    """Tests for send_message endpoint."""

    @pytest.mark.asyncio
    async def test_sends_message_and_triggers_processing(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should send message and trigger background processing."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "message_target_mode": "room_default",
                "client_request_id": "c7c9a000-0000-4000-8000-000000000001",
            }
        )

        mock_background_tasks = MagicMock()

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )
        patch_room_center_deps[
            "execution_engine"
        ].execute.return_value = expected_response

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is True
        assert response.message_id == "new-message-id"

        # Verify background task was added
        mock_background_tasks.add_task.assert_called_once()
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.room_id == sample_room.room_id
        assert execution_request.sender_id == mock_user.user_id
        assert execution_request.target_group == "room_team"
        assert execution_request.message_target_mode == "room_default"
        assert execution_request.mentioned_agent_ids is None

    @pytest.mark.asyncio
    async def test_send_message_rejects_missing_message_target_mode_without_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000099",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "message_target_mode is required" in response.error

    @pytest.mark.asyncio
    async def test_send_message_rejects_legacy_target_group(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000098",
                "target_group": "room_team",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group is no longer supported" in response.error

    @pytest.mark.asyncio
    async def test_send_message_rejects_unknown_message_target_mode(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000097",
                "message_target_mode": "room_team",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "message_target_mode must be one of" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_requires_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000096",
                "message_target_mode": "saved_group",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is required" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_rejects_malformed_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000091",
                "message_target_mode": "saved_group",
                "target_group_id": {"id": "group-123"},
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is required" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reserved_id", ["room_team", "all_agents"])
    async def test_send_message_saved_group_rejects_reserved_target_group_id(
        self,
        reserved_id,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000090",
                "message_target_mode": "saved_group",
                "target_group_id": reserved_id,
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id cannot be a reserved target group" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_uses_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000095",
                "message_target_mode": "saved_group",
                "target_group_id": " group-123 ",
            }
        )
        mock_background_tasks = MagicMock()
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is True
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.target_group == "group-123"
        assert execution_request.target_group_id == "group-123"
        assert execution_request.message_target_mode == "saved_group"

    @pytest.mark.asyncio
    async def test_send_message_all_agents_uses_explicit_mode(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000094",
                "message_target_mode": "all_agents",
            }
        )
        mock_background_tasks = MagicMock()
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is True
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.target_group == "all_agents"
        assert execution_request.message_target_mode == "all_agents"

    @pytest.mark.asyncio
    async def test_send_message_rejects_target_group_id_for_non_saved_group(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000093",
                "message_target_mode": "room_default",
                "target_group_id": "group-123",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_group_id", [0, []])
    async def test_send_message_rejects_falsy_target_group_id_for_non_saved_group(
        self,
        target_group_id,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000089",
                "message_target_mode": "room_default",
                "target_group_id": target_group_id,
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_rejects_target_group_id_with_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000092",
                "mentioned_agent_ids": ["agent-1"],
                "target_group_id": "group-123",
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_rejects_falsy_target_group_id_with_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000088",
                "mentioned_agent_ids": ["agent-1"],
                "target_group_id": 0,
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mentioned_agent_ids",
        ["agent-1", [1], [" "], {"agent_id": "agent-1"}],
    )
    async def test_send_message_rejects_malformed_mentioned_agent_ids(
        self,
        mentioned_agent_ids,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "client_request_id": "c7c9a000-0000-4000-8000-000000000087",
                "mentioned_agent_ids": mentioned_agent_ids,
            }
        )
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room

        response = await send_message(
            mock_request,
            MagicMock(),
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.status_code == 400
        assert (
            "mentioned_agent_ids must be a list of non-empty strings" in response.error
        )
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_trigger_processing_on_failure(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should not trigger processing when message creation fails."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "message_target_mode": "room_default",
                "client_request_id": "c7c9a000-0000-4000-8000-000000000002",
            }
        )

        mock_background_tasks = MagicMock()

        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        expected_response = ExecutionAck(
            success=False,
            error="Failed to create message",
        )
        patch_room_center_deps[
            "execution_engine"
        ].execute.return_value = expected_response

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is False
        mock_background_tasks.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_trigger_processing_when_ack_says_skip(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should not start orchestration when execution already emitted terminal status."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "message_target_mode": "room_default",
                "client_request_id": "c7c9a000-0000-4000-8000-000000000012",
            }
        )
        mock_background_tasks = MagicMock()
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
            should_start_orchestration=False,
        )

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is True
        assert response.message_id == "new-message-id"
        mock_background_tasks.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_active_run_rejection_does_not_enqueue_background_task(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "room_id": sample_room.room_id,
                "message": sample_user_message.model_dump(),
                "message_target_mode": "room_default",
                "client_request_id": "c7c9a000-0000-4000-8000-000000000777",
            }
        )
        mock_background_tasks = MagicMock()
        patch_room_center_deps[
            "db_service"
        ].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            room_id=sample_room.room_id,
            success=False,
            error="This room is already processing another message. Please retry shortly.",
            status_code=409,
            should_start_orchestration=False,
        )

        response = await send_message(
            mock_request,
            mock_background_tasks,
            mock_user,
            store=patch_room_center_deps["db_service"],
            engine=patch_room_center_deps["execution_engine"],
        )

        assert response.success is False
        assert response.status_code == 409
        mock_background_tasks.add_task.assert_not_called()


# =============================================================================
# Agent Suggestion Tests
# =============================================================================


class TestSuggestAgents:
    """Tests for suggest_agents endpoint."""

    @pytest.mark.asyncio
    async def test_returns_suggestions_for_valid_message(self):
        """Should return agent suggestions for valid message."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "message_text": "Help me write some code",
                "top_k": 3,
            }
        )

        mock_selection_service = MagicMock()
        mock_selection_service.suggest_agents = AsyncMock(
            return_value=AgentSuggestionResult(
                suggested_agents=[
                    AgentSuggestion(
                        agent_id="agent-1",
                        name="Agent 1",
                        reason="Match",
                        score=0.9,
                    ),
                    AgentSuggestion(
                        agent_id="agent-2",
                        name="Agent 2",
                        reason="Match",
                        score=0.8,
                    ),
                ]
            )
        )

        response = await suggest_agents(
            mock_request,
            selection_service=mock_selection_service,
        )

        assert response["success"] is True
        assert response["suggested_agents"] == [
            {
                "agent_id": "agent-1",
                "name": "Agent 1",
                "reason": "Match",
                "score": 0.9,
            },
            {
                "agent_id": "agent-2",
                "name": "Agent 2",
                "reason": "Match",
                "score": 0.8,
            },
        ]

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_message(self):
        """Should return error when message_text is empty."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "message_text": "",
                "top_k": 3,
            }
        )

        response = await suggest_agents(mock_request, selection_service=MagicMock())

        assert response["success"] is False
        assert response["status_code"] == 400

    @pytest.mark.asyncio
    async def test_handles_service_error(self):
        """Should handle errors from agent selection service."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "message_text": "Test message",
                "top_k": 3,
            }
        )

        mock_selection_service = MagicMock()
        mock_selection_service.suggest_agents = AsyncMock(
            side_effect=Exception("Service error")
        )

        response = await suggest_agents(
            mock_request,
            selection_service=mock_selection_service,
        )

        assert response["success"] is False
        assert response["status_code"] == 500
