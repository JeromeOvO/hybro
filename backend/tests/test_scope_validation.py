"""
Tests for scope validation and stale member handling.

Covers:
- _validate_canonical_mentions rejects invalid/unauthorized agents
- _resolve_explicit_target_scope rejects empty room_team, missing/empty saved groups
- Pre-persist scope validation: rejected messages never reach the database
- Legacy inline mentions persist-first behavior (pinned)
- _resolve_room_agent_refs marks private agents as inaccessible for non-owners
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.utils.time import utcnow
from models.agent import Agent, AgentStatus
from models.response import (
    RoomCenterUserMessageResponse,
    ScopeResolutionError,
)
from models.room import MessageContent, Room, RoomUserMessage
from models.room_services_models import ParseResult
from models.supervisor import (
    ActionType,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
    TrajectoryStatus,
)
from room.compat.runtime import RoomServices


@pytest.fixture
def room_center():
    rc = object.__new__(RoomServices)
    rc._store = AsyncMock()
    rc._store.get_agent_by_agent_id = AsyncMock(return_value=None)
    rc._store.get_agent_group_by_id = AsyncMock(return_value=None)
    rc._store.get_room_by_room_id = AsyncMock(return_value=None)
    # Backwards compatibility alias
    rc.database_service = rc._store
    rc.agent_service = MagicMock()
    rc.openai_service = MagicMock()
    rc.a2a_service = MagicMock()
    rc.delivery = MagicMock()
    rc.delivery.send_processing_status = AsyncMock()
    rc.remote_task_reader = MagicMock()
    rc.hitl_coordinator = MagicMock()
    rc.hitl_coordinator.get_pending_requests = AsyncMock(return_value=[])
    return rc


def _make_agent(
    agent_id,
    name="TestAgent",
    is_public=True,
    provider_id="owner-1",
    status=AgentStatus.active,
):
    agent = MagicMock(spec=Agent)
    agent.agent_id = agent_id
    agent.agent_status = status
    agent.is_public = is_public
    agent.provider_id = provider_id
    agent.agent_card = MagicMock()
    agent.agent_card.name = name
    return agent


def _make_room(room_id="room-1", owner_id="user-1", agent_set=None, extend_info=None):
    room = MagicMock(spec=Room)
    room.room_id = room_id
    room.room_owner_id = owner_id
    room.room_agent_set = agent_set or {}
    room.extend_info = extend_info or {"debateMode": False, "use_supervisor": False}
    room.room_name = "Test Room"
    room.processing_message_id = None
    return room


# =============================================================================
# _validate_canonical_mentions Tests
# =============================================================================


class TestValidateCanonicalMentions:
    @pytest.mark.asyncio
    async def test_valid_public_agent_returns_list(self, room_center):
        agent = _make_agent("agent-1", "Alpha")
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        result = await room_center._validate_canonical_mentions(
            ["agent-1"], sender_user_id="user-1"
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_nonexistent_agent_returns_error(self, room_center):
        room_center.database_service.get_agent_by_agent_id.return_value = None

        result = await room_center._validate_canonical_mentions(
            ["ghost-agent"], sender_user_id="user-1"
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.scope_resolution_error.code == "unauthorized_mention"

    @pytest.mark.asyncio
    async def test_inactive_agent_returns_error(self, room_center):
        agent = _make_agent("agent-1", status=AgentStatus.inactive)
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        result = await room_center._validate_canonical_mentions(
            ["agent-1"], sender_user_id="user-1"
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_private_agent_not_owned_returns_error(self, room_center):
        agent = _make_agent("agent-1", is_public=False, provider_id="other-user")
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        result = await room_center._validate_canonical_mentions(
            ["agent-1"], sender_user_id="user-1"
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.scope_resolution_error.code == "unauthorized_mention"

    @pytest.mark.asyncio
    async def test_private_agent_owned_by_sender_succeeds(self, room_center):
        agent = _make_agent("agent-1", is_public=False, provider_id="user-1")
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        result = await room_center._validate_canonical_mentions(
            ["agent-1"], sender_user_id="user-1"
        )
        assert isinstance(result, list)
        assert len(result) == 1


# =============================================================================
# _resolve_explicit_target_scope Tests
# =============================================================================


class TestResolveExplicitTargetScope:
    @pytest.mark.asyncio
    async def test_room_team_with_agents_returns_tuple(self, room_center):
        room = _make_room(agent_set={"a1": "Alpha"})
        agent = _make_agent("a1", "Alpha")
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        result = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "room_team",
            False,
            sender_user_id="user-1",
        )
        assert isinstance(result, tuple)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_room_team_filters_agents_that_cannot_accept_attachment(
        self, room_center
    ):
        room = _make_room(agent_set={"text": "Text", "image": "Image"})
        text_agent = _make_agent("text", "Text")
        text_agent.agent_card.default_input_modes = ["text"]
        image_agent = _make_agent("image", "Image")
        image_agent.agent_card.default_input_modes = ["image/*"]
        room_center.database_service.get_agent_by_agent_id.side_effect = {
            "text": text_agent,
            "image": image_agent,
        }.get

        selected, _, agents = await room_center._resolve_explicit_target_scope(
            room,
            "inspect this",
            "room_team",
            False,
            sender_user_id="user-1",
            required_input_modes=["image/png"],
        )

        assert selected == {"image": "Image"}
        assert agents == [image_agent]

    @pytest.mark.asyncio
    async def test_room_team_filters_open_capability_issues_before_llm_exposure(
        self, room_center
    ):
        room = _make_room(agent_set={"safe": "Safe", "broken": "Broken"})
        safe_agent = _make_agent("safe", "Safe")
        broken_agent = _make_agent("broken", "Broken")
        room_center.database_service.get_agent_by_agent_id.side_effect = {
            "safe": safe_agent,
            "broken": broken_agent,
        }.get
        room_center._capability_issue_reader = AsyncMock()
        room_center._capability_issue_reader.get_excluded_agent_ids.return_value = {
            "broken"
        }

        selected, _, agents = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "room_team",
            False,
            sender_user_id="user-1",
        )

        assert selected == {"safe": "Safe"}
        assert agents == [safe_agent]

    @pytest.mark.asyncio
    async def test_saved_group_filters_agents_that_cannot_accept_attachment(
        self, room_center
    ):
        room = _make_room()
        group = MagicMock()
        group.type = "custom"
        group.owner_id = "user-1"
        group.name = "Media"
        group.agents = ["text", "pdf"]
        room_center.database_service.get_agent_group_by_id.return_value = group
        text_agent = _make_agent("text", "Text")
        text_agent.agent_card.default_input_modes = ["text"]
        pdf_agent = _make_agent("pdf", "PDF")
        pdf_agent.agent_card.default_input_modes = ["application/pdf"]
        room_center.database_service.get_agent_by_agent_id.side_effect = {
            "text": text_agent,
            "pdf": pdf_agent,
        }.get

        selected, _, agents = await room_center._resolve_explicit_target_scope(
            room,
            "summarize this",
            "group-1",
            False,
            sender_user_id="user-1",
            required_input_modes=["application/pdf"],
        )

        assert selected == {"pdf": "PDF"}
        assert agents == [pdf_agent]

    @pytest.mark.asyncio
    async def test_room_team_empty_returns_scope_error(self, room_center):
        room = _make_room(agent_set={})

        result = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "room_team",
            False,
            sender_user_id="user-1",
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.scope_resolution_error.code == "empty_scope"

    @pytest.mark.asyncio
    async def test_missing_saved_group_returns_error(self, room_center):
        room = _make_room()
        room_center.database_service.get_agent_group_by_id.return_value = None

        result = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "nonexistent-group-id",
            False,
            sender_user_id="user-1",
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.scope_resolution_error.code == "group_not_usable"

    @pytest.mark.asyncio
    async def test_unauthorized_saved_group_returns_403(self, room_center):
        room = _make_room()
        group = MagicMock()
        group.type = "custom"
        group.owner_id = "other-user"
        group.agents = ["a1"]
        room_center.database_service.get_agent_group_by_id.return_value = group

        result = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "group-1",
            False,
            sender_user_id="user-1",
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_saved_group_returns_error(self, room_center):
        room = _make_room()
        group = MagicMock()
        group.type = "custom"
        group.owner_id = "user-1"
        group.agents = []
        group.name = "Empty Group"
        room_center.database_service.get_agent_group_by_id.return_value = group

        result = await room_center._resolve_explicit_target_scope(
            room,
            "hello",
            "group-1",
            False,
            sender_user_id="user-1",
        )
        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.success is False
        assert result.scope_resolution_error.code == "empty_scope"


@pytest.mark.asyncio
async def test_workflow_candidate_scope_rejects_unsupported_attachment_agent(
    room_center,
):
    text_agent = _make_agent("text", "Text")
    text_agent.agent_card.default_input_modes = ["text"]
    room_center.database_service.get_agent_by_agent_id.return_value = text_agent

    result = await room_center._resolve_selected_candidate_scope(
        ["text"],
        sender_user_id="user-1",
        required_input_modes=["application/pdf"],
    )

    assert isinstance(result, RoomCenterUserMessageResponse)
    assert result.success is False
    assert result.scope_resolution_error.code == "unauthorized_candidate_scope"


@pytest.mark.asyncio
async def test_workflow_candidate_scope_rejects_agent_with_capability_issue(
    room_center,
):
    agent = _make_agent("broken", "Broken")
    room_center.database_service.get_agent_by_agent_id.return_value = agent
    room_center._capability_issue_reader = AsyncMock()
    room_center._capability_issue_reader.get_excluded_agent_ids.return_value = {
        "broken"
    }

    result = await room_center._resolve_selected_candidate_scope(
        ["broken"],
        sender_user_id="user-1",
    )

    assert isinstance(result, RoomCenterUserMessageResponse)
    assert result.scope_resolution_error.code == "unauthorized_candidate_scope"


# =============================================================================
# Pre-persist scope validation (no message persisted on rejection)
# =============================================================================


class TestPrePersistScopeValidation:
    """Deterministic scope failures must reject before _persist_user_message is called."""

    @pytest.mark.asyncio
    async def test_invalid_mention_does_not_persist(self, room_center):
        room = _make_room()
        room_center.database_service.get_room_by_room_id.return_value = room
        room_center.database_service.get_agent_by_agent_id.return_value = None

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.message = MagicMock()
        request.message.message_content = MagicMock()
        request.message.message_content.message_text = "hello"
        request.message.message_content.message_attachments = None

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)

        result = await room_center.send_message_to_room(
            request,
            target_group="room_team",
            mentioned_agent_ids=["ghost-agent"],
        )

        assert result.success is False
        assert result.scope_resolution_error.code == "unauthorized_mention"
        room_center._persist_user_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_room_team_does_not_persist(self, room_center):
        room = _make_room(agent_set={})
        room_center.database_service.get_room_by_room_id.return_value = room

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.message = MagicMock()
        request.message.message_content = MagicMock()
        request.message.message_content.message_text = "hello"
        request.message.message_content.message_attachments = None

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)

        result = await room_center.send_message_to_room(
            request,
            target_group="room_team",
            mentioned_agent_ids=None,
        )

        assert result.success is False
        assert result.scope_resolution_error.code == "empty_scope"
        room_center._persist_user_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_saved_group_does_not_persist(self, room_center):
        room = _make_room()
        room_center.database_service.get_room_by_room_id.return_value = room
        room_center.database_service.get_agent_group_by_id.return_value = None

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.message = MagicMock()
        request.message.message_content = MagicMock()
        request.message.message_content.message_text = "hello"
        request.message.message_content.message_attachments = None

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)

        result = await room_center.send_message_to_room(
            request,
            target_group="nonexistent-group",
            mentioned_agent_ids=None,
        )

        assert result.success is False
        assert result.scope_resolution_error.code == "group_not_usable"
        room_center._persist_user_message.assert_not_called()


# =============================================================================
# Legacy inline mentions (pinned: persist-before-validate behavior)
# =============================================================================


class TestLegacyInlineMentionBehavior:
    """Legacy inline mentions (no mentioned_agent_ids) are NOT covered by
    reject-before-persist. This test class pins the explicit design decision."""

    @pytest.mark.asyncio
    async def test_legacy_inline_mention_drops_unknown_agent_silently(
        self, room_center
    ):
        """parse_agent_mentions silently drops agents not in room — no error."""
        text = "<@unknown-agent|Ghost> do something"
        agent_set = {"a1": "Alpha"}
        result = room_center.parse_agent_mentions(text, agent_set)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_pre_persist_skipped_when_no_canonical_mentions(self, room_center):
        """When mentioned_agent_ids is empty/None, _validate_canonical_mentions
        is NOT called. The code path for room_team goes through _resolve_explicit_target_scope
        instead, which runs pre-persist. Legacy inline mentions only run post-persist."""
        room = _make_room(agent_set={"a1": "Alpha"})
        agent = _make_agent("a1", "Alpha")
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        scope = await room_center._resolve_explicit_target_scope(
            room,
            "<@unknown|Ghost> hello",
            "room_team",
            False,
        )
        assert isinstance(scope, tuple)
        assert "a1" in scope[0]

    @pytest.mark.asyncio
    async def test_supervisor_inline_mention_is_planner_intent_not_hard_route(
        self, room_center
    ):
        room = _make_room(
            agent_set={"a1": "Alpha"},
            extend_info={"debateMode": False, "use_supervisor": True},
        )
        agent = _make_agent("a1", "Alpha")
        room_center.database_service.get_room_by_room_id.return_value = room
        room_center.database_service.get_agent_by_agent_id.return_value = agent
        room_center.database_service.get_room_memory_by_room_id = AsyncMock(
            return_value=None
        )

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.client_request_id = None
        request.message = RoomUserMessage(
            room_id="room-1",
            message_id="msg-inline-1",
            user_id="user-1",
            message_content=MessageContent(
                message_text="<@a1|Alpha> please help",
            ),
            extend_info={},
        )

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._materialize_room_quote = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)
        room_center._initialize_room_memory = AsyncMock(return_value=None)
        room_center.delivery.create_token = MagicMock(return_value=None)
        handle_mentions = AsyncMock()
        prepare_supervisor = AsyncMock(return_value=ParseResult(success=True))
        room_center._handle_mentions_flow = handle_mentions
        room_center._prepare_for_supervisor = prepare_supervisor

        result = await room_center.send_message_to_room(
            request,
            target_group="room_team",
            mentioned_agent_ids=None,
        )

        assert result.success is True
        handle_mentions.assert_not_awaited()
        assert prepare_supervisor.await_args.kwargs["explicit_mentions"] == [
            {
                "agent_id": "a1",
                "agent_name": "Alpha",
                "mention_text": "<@a1|Alpha>",
                "position": 0,
            }
        ]

    @pytest.mark.asyncio
    async def test_clarify_resume_preserves_explicit_mentions(self, room_center):
        room = _make_room(
            agent_set={"a1": "Alpha"},
            extend_info={
                "debateMode": False,
                "use_supervisor": True,
                "pending_clarification_message_id": "orig-msg",
            },
        )
        trajectory = SupervisorTrajectory(
            status=TrajectoryStatus.CLARIFYING,
            entries=[
                TrajectoryEntry(
                    step_number=1,
                    action=SupervisorAction(
                        action=ActionType.CLARIFY,
                        reasoning="need detail",
                        clarification_question="What should Alpha do?",
                    ),
                    started_at=utcnow(),
                )
            ],
        )
        original = MagicMock()
        original.extend_info = {
            "supervisor_trajectory": trajectory.model_dump(mode="json")
        }
        room_center.database_service.get_room_user_message_by_message_id.return_value = original
        room_center.database_service.update_room_user_message_by_message_id = (
            AsyncMock()
        )
        room_center.database_service.update_room_by_room_id = AsyncMock()
        user_message = RoomUserMessage(
            room_id="room-1",
            message_id="reply-msg",
            user_id="user-1",
            message_content=MessageContent(message_text="ask Alpha"),
            extend_info={},
        )
        mentions = [
            {
                "agent_id": "a1",
                "agent_name": "Alpha",
                "mention_text": "<@a1|Alpha>",
                "position": 0,
            }
        ]

        result = await room_center._prepare_clarify_resume(
            room=room,
            user_message=user_message,
            message_text="ask Alpha",
            pending_clarify_msg_id="orig-msg",
            agents=None,
            selected_agent_set={"a1": "Alpha"},
            is_debate_mode=False,
            room_memory=None,
            explicit_mentions=mentions,
        )

        assert result is True
        assert user_message.extend_info["explicit_mentions"] == mentions
        assert user_message.extend_info["room_config"]["explicit_mentions"] == mentions

    @pytest.mark.asyncio
    async def test_supervisor_inline_mention_preserves_clarify_resume(
        self, room_center
    ):
        room = _make_room(
            agent_set={"a1": "Alpha"},
            extend_info={
                "debateMode": False,
                "use_supervisor": True,
                "pending_clarification_message_id": "orig-msg",
            },
        )
        agent = _make_agent("a1", "Alpha")
        room_center.database_service.get_room_by_room_id.return_value = room
        room_center.database_service.get_agent_by_agent_id.return_value = agent
        room_center.database_service.get_room_memory_by_room_id = AsyncMock(
            return_value=None
        )

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.client_request_id = None
        request.message = RoomUserMessage(
            room_id="room-1",
            message_id="msg-inline-clarify",
            user_id="user-1",
            message_content=MessageContent(
                message_text="<@a1|Alpha> use the latest draft",
            ),
            extend_info={},
        )

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._materialize_room_quote = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)
        room_center._initialize_room_memory = AsyncMock(return_value=None)
        room_center.delivery.create_token = MagicMock(return_value=None)
        handle_mentions = AsyncMock()
        prepare_clarify = AsyncMock(return_value=True)
        prepare_supervisor = AsyncMock(return_value=ParseResult(success=True))
        room_center._handle_mentions_flow = handle_mentions
        room_center._prepare_clarify_resume = prepare_clarify
        room_center._prepare_for_supervisor = prepare_supervisor

        result = await room_center.send_message_to_room(
            request,
            target_group="room_team",
            mentioned_agent_ids=None,
        )

        assert result.success is True
        handle_mentions.assert_not_awaited()
        prepare_supervisor.assert_not_awaited()
        assert prepare_clarify.await_args.kwargs["pending_clarify_msg_id"] == "orig-msg"
        assert prepare_clarify.await_args.kwargs["explicit_mentions"] == [
            {
                "agent_id": "a1",
                "agent_name": "Alpha",
                "mention_text": "<@a1|Alpha>",
                "position": 0,
            }
        ]


# =============================================================================
# _resolve_room_agent_refs — inaccessible visibility
# =============================================================================


class TestResolveRoomAgentRefsVisibility:
    @pytest.mark.asyncio
    async def test_private_agent_marked_inaccessible_for_non_owner(self, room_center):
        agent = _make_agent(
            "priv-1", "PrivateBot", is_public=False, provider_id="owner-user"
        )
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        refs, status = await room_center._resolve_room_agent_refs(
            {"priv-1": "PrivateBot"}, viewer_user_id="viewer-user"
        )
        assert len(refs) == 1
        assert refs[0].availability == "inaccessible"

    @pytest.mark.asyncio
    async def test_private_agent_available_for_owner(self, room_center):
        agent = _make_agent(
            "priv-1", "PrivateBot", is_public=False, provider_id="owner-user"
        )
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        refs, status = await room_center._resolve_room_agent_refs(
            {"priv-1": "PrivateBot"}, viewer_user_id="owner-user"
        )
        assert len(refs) == 1
        assert refs[0].availability == "available"

    @pytest.mark.asyncio
    async def test_deleted_agent_marked_deleted(self, room_center):
        room_center.database_service.get_agent_by_agent_id.return_value = None

        refs, status = await room_center._resolve_room_agent_refs(
            {"gone-1": "GoneBot"}, viewer_user_id="viewer-user"
        )
        assert len(refs) == 1
        assert refs[0].availability == "deleted"
        assert status == "all_unavailable"

    @pytest.mark.asyncio
    async def test_inactive_agent_marked_inactive(self, room_center):
        agent = _make_agent("inact-1", "InactiveBot", status=AgentStatus.inactive)
        room_center.database_service.get_agent_by_agent_id.return_value = agent

        refs, status = await room_center._resolve_room_agent_refs(
            {"inact-1": "InactiveBot"}, viewer_user_id="viewer-user"
        )
        assert len(refs) == 1
        assert refs[0].availability == "inactive"

    @pytest.mark.asyncio
    async def test_mixed_agents_produce_degraded_status(self, room_center):
        async def get_agent(aid):
            if aid == "ok-1":
                return _make_agent("ok-1", "OkBot")
            return None

        room_center.database_service.get_agent_by_agent_id = AsyncMock(
            side_effect=get_agent
        )

        refs, status = await room_center._resolve_room_agent_refs(
            {"ok-1": "OkBot", "gone-1": "GoneBot"}, viewer_user_id="viewer-user"
        )
        assert status == "degraded"

    @pytest.mark.asyncio
    async def test_empty_agent_set_returns_empty(self, room_center):
        refs, status = await room_center._resolve_room_agent_refs(None)
        assert refs == []
        assert status == "empty"


# =============================================================================
# all_agents post-persist failure returns real message_id
# =============================================================================


class TestAllAgentsPostPersistMessageId:
    """When all_agents selector fails after message persistence, the error
    response must include the real message_id so the frontend doesn't rollback
    an already-persisted user message."""

    @pytest.mark.asyncio
    async def test_all_agents_exception_returns_persisted_message_id(self, room_center):
        room = _make_room(agent_set={"a1": "Alpha"})
        room_center.database_service.get_room_by_room_id.return_value = room

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.message = MagicMock()
        request.message.message_id = "msg-real-123"
        request.message.message_content = MagicMock()
        request.message.message_content.message_text = "hello"
        request.message.message_content.message_attachments = None
        request.message.quote = None
        request.message.extend_info = {}

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._materialize_room_quote = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)
        room_center._initialize_room_memory = AsyncMock(return_value=None)
        room_center.delivery.create_token = MagicMock()

        # Make _resolve_explicit_target_scope return an error (simulating selector failure)
        error_response = RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="Agent selection failed.",
            scope_resolution_error=ScopeResolutionError(
                code="empty_scope",
                message="Agent selection failed.",
            ),
            status_code=500,
        )
        room_center._resolve_explicit_target_scope = AsyncMock(
            return_value=error_response
        )
        result = await room_center.send_message_to_room(
            request,
            target_group="all_agents",
            mentioned_agent_ids=None,
        )

        assert result.success is False
        assert result.scope_resolution_error.code == "empty_scope"
        # The key assertion: message_id should be the real persisted ID, not None
        assert result.message_id == "msg-real-123"
        assert result.preflight_outcome == "failed"
        assert result.preflight_details == "Agent selection failed."
        room_center._persist_user_message.assert_called_once()


# =============================================================================
# client_request_id propagation to Execution preflight metadata
# =============================================================================


class TestClientRequestIdPropagation:
    """Verify client_request_id stays on the request message for execution preflight."""

    @pytest.mark.asyncio
    async def test_send_message_to_room_preserves_client_request_id_for_execution_preflight(
        self, room_center
    ):
        room = _make_room(agent_set={"a1": "Alpha"})
        room_center.database_service.get_room_by_room_id.return_value = room

        request = MagicMock()
        request.room_id = "room-1"
        request.user_id = "user-1"
        request.client_request_id = "cr-123"
        request.message = MagicMock()
        request.message.message_id = "msg-real-456"
        request.message.message_content = MagicMock()
        request.message.message_content.message_text = "hello"
        request.message.message_content.message_attachments = None
        request.message.quote = None
        request.message.extend_info = {}

        room_center._validate_send_message_request = MagicMock(return_value=None)
        room_center._resolve_and_apply_attachments = AsyncMock(return_value=None)
        room_center._materialize_room_quote = AsyncMock(return_value=None)
        room_center._persist_user_message = AsyncMock(return_value=True)
        room_center._initialize_room_memory = AsyncMock(return_value=None)
        room_center.delivery.create_token = MagicMock()

        # Make scope resolution return an error so the function returns early.
        error_response = RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="Agent selection failed.",
            scope_resolution_error=ScopeResolutionError(
                code="empty_scope",
                message="Agent selection failed.",
            ),
            status_code=500,
        )
        room_center._resolve_explicit_target_scope = AsyncMock(
            return_value=error_response
        )

        result = await room_center.send_message_to_room(
            request,
            target_group="all_agents",
            mentioned_agent_ids=None,
        )

        assert result.preflight_outcome == "failed"
        assert result.preflight_details == "Agent selection failed."
        assert request.message.client_request_id == "cr-123"

    @pytest.mark.asyncio
    async def test_parse_user_message_forwards_client_request_id_to_agent_message_generation(
        self, room_center
    ):
        """Regression: parse_user_message must not reference out-of-scope request."""
        room_center._generate_agent_messages_based_on_parsed_result = AsyncMock(
            return_value=[MagicMock()]
        )

        result = await room_center.parse_user_message(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            selected_agent_set={"a1": "Alpha"},
            user_id="user-1",
            is_debate_mode=False,
            auto_assign_agents=False,
            target_group="room_team",
            agents=None,
            conversation_context=None,
            token=None,
            client_request_id="cr-parse-1",
        )

        assert result.success is True
        room_center._generate_agent_messages_based_on_parsed_result.assert_awaited_once_with(
            {
                "message_type": "DIRECT_CHAT",
                "original_text": "hello",
                "needs_decomposition": False,
                "task_steps": [
                    {
                        "step_id": "step_1",
                        "agent_id": "a1",
                        "agent_name": "Alpha",
                        "task_content": "hello",
                        "dependencies": [],
                    }
                ],
            },
            "msg-1",
            "room-1",
            user_id="user-1",
            extend_info={
                "allowed_agent_ids": ["a1"],
                "target_group": "room_team",
                "is_direct_chat": True,
            },
            client_request_id="cr-parse-1",
        )

    @pytest.mark.asyncio
    async def test_parse_user_message_persists_modes_for_generated_steps(
        self, room_center
    ):
        room_center._generate_agent_messages_based_on_parsed_result = AsyncMock(
            return_value=[MagicMock()]
        )

        result = await room_center.parse_user_message(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="inspect this",
            selected_agent_set={"image": "Image"},
            user_id="user-1",
            target_group="room_team",
            required_input_modes=["image/png"],
        )

        assert result.success is True
        assert (
            room_center._generate_agent_messages_based_on_parsed_result.await_args.kwargs[
                "extend_info"
            ]["required_input_modes"]
            == ["image/png"]
        )
