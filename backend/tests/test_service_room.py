"""
Unit tests for RoomCenter (room_runtime.py) -- pure logic methods.

Tests cover:
- _looks_like_agent_id heuristic
- _normalize_room_agent_set canonical shape detection
- parse_agent_mentions extraction
- extract_agent_message_content per-agent routing
- _validate_send_message_request input validation
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import MessageCommitted, RoomInfo
from models.request import RoomCenterRoomSettingRequest, RoomCenterUserMessageRequest
from models.room import MessageContent, Room, RoomUserMessage
from room.compat.runtime import RoomServices


@pytest.fixture
def room_center():
    """Create a RoomCenter with mocked dependencies."""
    rc = object.__new__(RoomServices)
    rc._store = MagicMock()
    # Backwards compatibility alias
    rc.database_service = rc._store
    rc.agent_service = MagicMock()
    rc.openai_service = MagicMock()
    rc.a2a_service = MagicMock()
    rc.delivery = MagicMock()
    rc.remote_task_reader = MagicMock()
    return rc


_ROOT = Path(__file__).resolve().parents[1]


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.internal_events = []
        self.wait_flags = []

    async def emit_internal(
        self,
        event,
        *,
        wait_for_local_handlers: bool = False,
        broadcast: bool = True,
    ) -> None:
        self.internal_events.append(event)
        self.wait_flags.append(wait_for_local_handlers)


def test_room_services_bind_store_sets_runtime_store():
    svc = object.__new__(RoomServices)
    store = object()

    svc.bind_store(store)

    assert svc._store is store


@pytest.mark.asyncio
async def test_room_services_delegated_methods_fail_before_bind():
    svc = object.__new__(RoomServices)
    svc._facade = None
    svc._bound = False

    with pytest.raises(
        RuntimeError,
        match=r"RoomServices\.bind_facade\(\) not called - startup incomplete",
    ):
        await svc.create_new_room(RoomCenterRoomSettingRequest(room_name="Room"))


@pytest.mark.asyncio
async def test_room_services_bind_facade_delegates_room_lifecycle_methods():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._bound = False
    svc._facade = None
    facade = AsyncMock()
    facade.create_room.return_value = RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="owner",
        owner_name="Owner",
        agent_ids=["a1"],
        agent_set={"a1": "Agent One"},
    )
    facade.get_room.return_value = facade.create_room.return_value
    facade.list_rooms_for_owner.return_value = [facade.create_room.return_value]
    facade.replace_membership.return_value = facade.create_room.return_value
    facade.update_room.return_value = facade.create_room.return_value
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True

    svc.bind_facade(facade)
    svc.bind_context_memory(
        SimpleNamespace(delete_room_memory=AsyncMock(return_value=True))
    )
    svc._s3_service = SimpleNamespace(delete_prefix=AsyncMock())

    create_response = await svc.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner",
            room_owner_name="Owner",
            room_agent_set={"a1": "Agent One"},
            extend_info={"debateMode": True, "use_supervisor": True},
            requesting_user_id="owner",
        )
    )
    inquiry_response = await svc.inquiry_room_setting(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )
    list_response = await svc.inquiry_rooms_by_room_owner_id(
        RoomCenterRoomSettingRequest(room_owner_id="owner")
    )
    replace_response = await svc.update_room_agent_set(
        RoomCenterRoomSettingRequest(
            room_id="r1",
            room_agent_set={"a1": "Agent One"},
            requesting_user_id="owner",
        )
    )
    rename_response = await svc.update_room_name(
        RoomCenterRoomSettingRequest(room_id="r1", room_name="Renamed")
    )
    extend_response = await svc.update_room_extend_info(
        RoomCenterRoomSettingRequest(room_id="r1", extend_info={"x": 1})
    )
    delete_response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )

    assert create_response.success is True
    assert create_response.room.room_id == "r1"
    assert inquiry_response.room.room_agent_set == {"a1": "Agent One"}
    assert list_response.room_list[0].room_id == "r1"
    assert replace_response.success is True
    assert rename_response.success is True
    assert extend_response.success is True
    assert delete_response.success is True
    facade.create_room.assert_awaited_once()
    create_request = facade.create_room.await_args.args[0]
    assert create_request.extend_info == {"debateMode": True, "use_supervisor": True}
    facade.get_room.assert_awaited()
    facade.list_rooms_for_owner.assert_awaited_once_with("owner")
    facade.replace_membership.assert_awaited_once()
    facade.update_room.assert_any_await("r1", {"room_name": "Renamed"})
    facade.update_room.assert_any_await("r1", {"extend_info": {"x": 1}})
    facade.delete_room.assert_awaited_once_with("r1", "owner")


@pytest.mark.asyncio
async def test_room_services_active_runs_response_is_room_metadata_only():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        side_effect=AssertionError("legacy room store should not be used")
    )
    svc._store.get_room_user_message_by_message_id = AsyncMock(
        side_effect=AssertionError("legacy message store should not be used")
    )
    svc._store.get_active_runs_by_room_id = AsyncMock(
        side_effect=AssertionError("legacy active-run store should not be used")
    )
    svc._bound = False
    svc._facade = None
    facade = AsyncMock()
    facade.get_room.return_value = RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="owner",
        owner_name="Owner",
    )
    facade.get_turn_completion_kind.return_value = "synthesis"

    svc.bind_facade(facade)

    response = await svc.inquiry_active_runs(
        RoomCenterRoomSettingRequest(
            room_id="r1",
            trigger_message_id="trigger-1",
        )
    )

    assert response.success is True
    assert response.room_id == "r1"
    assert response.active_runs == []
    assert response.turn_completion_kind == "synthesis"
    facade.get_room.assert_awaited_once_with("r1")
    facade.get_turn_completion_kind.assert_awaited_once_with("trigger-1")
    svc._store.get_room_by_room_id.assert_not_awaited()
    svc._store.get_room_user_message_by_message_id.assert_not_awaited()
    svc._store.get_active_runs_by_room_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_services_room_setting_returns_room_metadata_only():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_active_runs_by_room_id = AsyncMock(
        side_effect=AssertionError("legacy active-run store should not be used")
    )
    svc._bound = False
    svc._facade = None
    facade = AsyncMock()
    facade.get_room.return_value = RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="owner",
        owner_name="Owner",
    )
    svc.bind_facade(facade)

    response = await svc.inquiry_room_setting(
        RoomCenterRoomSettingRequest(room_id="r1")
    )

    assert response.success is True
    assert response.active_runs is None
    facade.get_room.assert_awaited_once_with("r1")
    svc._store.get_active_runs_by_room_id.assert_not_awaited()


def test_room_services_migrated_crud_methods_do_not_keep_legacy_store_branches():
    forbidden_by_method = {
        "inquiry_room_setting": {"get_room_by_room_id", "update_room_by_room_id"},
        "inquiry_active_runs": {
            "get_room_by_room_id",
            "get_room_user_message_by_message_id",
        },
        "inquiry_rooms_by_room_owner_id": {"get_rooms_by_room_owner_id"},
        "update_room_agent_set": {"get_room_by_room_id", "update_room_by_room_id"},
        "update_room_name": {"get_room_by_room_id", "update_room_by_room_id"},
        "update_room_extend_info": {"get_room_by_room_id", "update_room_by_room_id"},
    }
    source = _ROOT / "room" / "compat" / "runtime.py"
    tree = ast.parse(source.read_text())
    methods = {
        item.name: item
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RoomServices"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    violations: list[str] = []
    for method_name, forbidden_attrs in forbidden_by_method.items():
        method = methods[method_name]
        for node in ast.walk(method):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                violations.append(f"{method_name}:{node.lineno}: {node.attr}")

    assert not violations, (
        "Migrated methods still use legacy store branches:\n" + "\n".join(violations)
    )


@pytest.mark.asyncio
async def test_room_services_persist_user_message_emits_message_committed_event():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.add_room_user_message = AsyncMock(
        side_effect=AssertionError("legacy message store should not be used")
    )
    svc._bound = False
    svc._facade = None
    publisher = RecordingEventPublisher()
    facade = AsyncMock()
    facade.persist_user_message.return_value = True
    svc.bind_facade(facade)
    svc.bind_message_event_publisher(publisher)
    user_message = RoomUserMessage(
        room_id="r1",
        message_id="u1",
        message_content=MessageContent(message_text="hello"),
    )

    assert (
        await svc._persist_user_message(
            user_message,
            room_agent_set={"a1": "Agent One"},
        )
        is True
    )

    facade.persist_user_message.assert_awaited_once_with(user_message)
    svc._store.add_room_user_message.assert_not_awaited()
    assert len(publisher.internal_events) == 1
    event = publisher.internal_events[0]
    assert isinstance(event, MessageCommitted)
    assert event.room_id == "r1"
    assert event.message_id == "u1"
    assert event.message_type == "user"
    assert event.agent_id is None
    assert event.room_agent_set == {"a1": "Agent One"}
    assert publisher.wait_flags == [True]


@pytest.mark.asyncio
async def test_room_services_persist_user_message_does_not_emit_on_failure():
    svc = object.__new__(RoomServices)
    svc._bound = False
    svc._facade = None
    publisher = RecordingEventPublisher()
    facade = AsyncMock()
    facade.persist_user_message.return_value = False
    svc.bind_facade(facade)
    svc.bind_message_event_publisher(publisher)
    user_message = RoomUserMessage(
        room_id="r1",
        message_id="u1",
        message_content=MessageContent(message_text="hello"),
    )

    assert await svc._persist_user_message(user_message, room_agent_set={}) is False

    assert publisher.internal_events == []
    assert publisher.wait_flags == []


@pytest.mark.asyncio
async def test_room_services_persist_message_to_room_passes_room_agent_set_to_user_commit_event():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="r1",
            room_name="Room",
            room_owner_id="owner",
            room_owner_name="Owner",
            room_agent_set={"a1": "Canonical Agent"},
            extend_info={},
        )
    )
    svc._bound = False
    svc._facade = None
    svc.delivery = MagicMock()
    svc.delivery.create_token.return_value = object()
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._resolve_explicit_target_scope = AsyncMock()
    svc._materialize_room_quote = AsyncMock(return_value=None)
    publisher = RecordingEventPublisher()
    facade = AsyncMock()
    facade.persist_user_message.return_value = True
    svc.bind_facade(facade)
    svc.bind_message_event_publisher(publisher)
    user_message = RoomUserMessage(
        room_id="r1",
        message_id="u1",
        user_id="user-1",
        message_content=MessageContent(
            message_text="Please ask <@a1|Stale Name> for help"
        ),
    )

    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="r1",
            user_id="user-1",
            message=user_message,
        ),
        target_group="all_agents",
    )

    assert response.success is True
    assert context is not None
    event = publisher.internal_events[0]
    assert isinstance(event, MessageCommitted)
    assert event.room_agent_set == {"a1": "Canonical Agent"}


@pytest.mark.asyncio
async def test_room_services_quote_materialization_delegates_to_room_facade():
    svc = object.__new__(RoomServices)
    svc._bound = False
    svc._facade = None
    facade = AsyncMock()
    facade.materialize_quote.return_value = None
    svc.bind_facade(facade)
    room = Room(
        room_id="r1",
        room_name="Room",
        room_owner_id="owner",
        room_owner_name="Owner",
    )
    user_message = RoomUserMessage(
        room_id="r1",
        message_id="u1",
        message_content=MessageContent(message_text="hello"),
    )
    request = MagicMock()

    assert await svc._materialize_room_quote(room, request, user_message) is None
    facade.materialize_quote.assert_awaited_once_with(
        room=room,
        request=request,
        user_message=user_message,
    )


def test_room_services_migrated_message_methods_do_not_call_legacy_store():
    forbidden_by_method = {
        "_persist_user_message": {"add_room_user_message"},
        "update_agent_message_by_message_id": {
            "get_room_agent_message_by_message_id",
            "update_room_agent_message_by_message_id",
        },
        "inquiry_user_messages_by_room_id": {"get_room_user_messages_by_room_id"},
        "inquiry_agent_messages_by_room_id": {
            "get_room_agent_messages_by_room_id",
            "update_room_agent_message_by_message_id",
        },
        "inquiry_agent_message_by_message_id": {
            "get_room_agent_message_by_message_id",
        },
        "inquiry_user_message_by_message_id": {
            "get_room_user_message_by_message_id",
        },
        "inquiry_agent_messages_by_related_message_id": {
            "get_room_agent_messages_by_related_message_id",
        },
    }
    source = _ROOT / "room" / "compat" / "runtime.py"
    tree = ast.parse(source.read_text())
    methods = {
        item.name: item
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RoomServices"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    violations: list[str] = []
    for method_name, forbidden_attrs in forbidden_by_method.items():
        method = methods[method_name]
        for node in ast.walk(method):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                violations.append(f"{method_name}:{node.lineno}: {node.attr}")

    assert not violations, (
        "Migrated message methods still use legacy store:\n" + "\n".join(violations)
    )


@pytest.mark.asyncio
async def test_delete_room_does_not_cleanup_when_requester_is_not_owner():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_active_runs_by_room_id = AsyncMock(return_value=[])
    svc._bound = False
    svc._facade = None
    svc._s3_service = SimpleNamespace(delete_prefix=AsyncMock())
    facade = AsyncMock()
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True
    svc.bind_facade(facade)
    memory_manager = SimpleNamespace(delete_room_memory=AsyncMock(return_value=True))
    svc.bind_context_memory(memory_manager)

    response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="intruder")
    )

    assert response.success is False
    assert response.status_code == 403
    assert response.error == "Forbidden"
    facade.delete_room.assert_not_awaited()
    memory_manager.delete_room_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_room_success_when_post_delete_context_memory_cleanup_fails():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_active_runs_by_room_id = AsyncMock(return_value=[])
    svc._bound = False
    svc._facade = None
    svc._s3_service = SimpleNamespace(delete_prefix=AsyncMock())
    facade = AsyncMock()
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True
    svc.bind_facade(facade)
    svc.bind_context_memory(
        SimpleNamespace(delete_room_memory=AsyncMock(return_value=False))
    )

    response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )

    assert response.success is True
    assert response.status_code == 200
    assert response.error is None
    facade.delete_room.assert_awaited_once_with("r1", "owner")


# =============================================================================
# _looks_like_agent_id Tests
# =============================================================================


class TestLooksLikeAgentId:
    """Tests for UUID-style agent ID detection."""

    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "550e8400e29b41d4a716446655440000",
        ],
    )
    def test_recognizes_valid_uuids(self, value):
        assert RoomServices._looks_like_agent_id(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "MyAgent",
            "agent-name",
            "",
            "not-a-uuid-at-all",
        ],
    )
    def test_rejects_non_uuid_strings(self, value):
        assert RoomServices._looks_like_agent_id(value) is False

    def test_rejects_non_string(self):
        assert RoomServices._looks_like_agent_id(123) is False
        assert RoomServices._looks_like_agent_id(None) is False


# =============================================================================
# _normalize_room_agent_set Tests
# =============================================================================


class TestNormalizeRoomAgentSet:
    """Tests for room_agent_set normalization."""

    def test_returns_empty_for_none(self, room_center):
        assert room_center._normalize_room_agent_set(None) == {}

    def test_returns_empty_for_empty(self, room_center):
        assert room_center._normalize_room_agent_set({}) == {}

    def test_preserves_correct_shape(self, room_center):
        """Keys are UUIDs, values are names -- already canonical."""
        data = {"550e8400e29b41d4a716446655440000": "MyAgent"}
        result = room_center._normalize_room_agent_set(data)
        assert result == data

    def test_flips_inverted_shape(self, room_center):
        """Keys are names, values are UUIDs -- needs flipping."""
        data = {"MyAgent": "550e8400e29b41d4a716446655440000"}
        result = room_center._normalize_room_agent_set(data)
        assert "550e8400e29b41d4a716446655440000" in result
        assert result["550e8400e29b41d4a716446655440000"] == "MyAgent"

    def test_handles_ambiguous_data(self, room_center):
        """When keys and values both look like IDs, preserves original."""
        data = {"550e8400e29b41d4a716446655440000": "660e8400e29b41d4a716446655440000"}
        result = room_center._normalize_room_agent_set(data)
        assert result == data


# =============================================================================
# parse_agent_mentions Tests
# =============================================================================


class TestParseAgentMentions:
    """Tests for @agent mention parsing."""

    def test_parses_single_mention(self, room_center):
        text = "Hello <@agent-1|AgentOne> please help"
        agent_set = {"agent-1": "AgentOne"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 1
        assert result[0]["agent_id"] == "agent-1"
        assert result[0]["agent_name"] == "AgentOne"
        assert result[0]["mention_text"] == "<@agent-1|AgentOne>"

    def test_parses_multiple_mentions(self, room_center):
        text = "<@a1|Alpha> do X and <@a2|Beta> do Y"
        agent_set = {"a1": "Alpha", "a2": "Beta"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 2
        assert result[0]["agent_id"] == "a1"
        assert result[1]["agent_id"] == "a2"

    def test_ignores_unknown_agent(self, room_center):
        """Agent not in room should be silently ignored."""
        text = "<@unknown|Ghost> do something"
        agent_set = {}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert len(result) == 0

    def test_returns_empty_for_no_mentions(self, room_center):
        text = "Just a normal message with no mentions"
        result = room_center.parse_agent_mentions(text, {"a1": "Alpha"})
        assert result == []

    def test_preserves_position_order(self, room_center):
        text = "<@b|Beta> then <@a|Alpha>"
        agent_set = {"a": "Alpha", "b": "Beta"}
        result = room_center.parse_agent_mentions(text, agent_set)

        assert result[0]["agent_id"] == "b"
        assert result[1]["agent_id"] == "a"


# =============================================================================
# extract_agent_message_content Tests
# =============================================================================


class TestExtractAgentMessageContent:
    """Tests for per-agent message content extraction."""

    def test_extracts_content_for_mentioned_agent(self, room_center):
        text = "<@a1|Alpha> write code. <@a2|Beta> review it."
        mentions = [
            {
                "agent_id": "a1",
                "agent_name": "Alpha",
                "mention_text": "<@a1|Alpha>",
                "position": 0,
            },
            {
                "agent_id": "a2",
                "agent_name": "Beta",
                "mention_text": "<@a2|Beta>",
                "position": 22,
            },
        ]

        result = room_center.extract_agent_message_content(
            text, "a1", "Alpha", mentions
        )
        assert "write code" in result
        assert "<@" not in result

    def test_returns_clean_text_when_agent_not_mentioned(self, room_center):
        """Agent not in mentions gets full text with all mentions stripped."""
        text = "<@a1|Alpha> do something"
        mentions = [
            {
                "agent_id": "a1",
                "agent_name": "Alpha",
                "mention_text": "<@a1|Alpha>",
                "position": 0,
            },
        ]

        result = room_center.extract_agent_message_content(text, "a2", "Beta", mentions)
        assert "<@" not in result
        assert "do something" in result


# =============================================================================
# _validate_send_message_request Tests
# =============================================================================


class TestValidateSendMessageRequest:
    """Tests for send_message input validation."""

    def test_returns_none_for_valid_request(self, room_center):
        req = MagicMock()
        req.room_id = "room-001"
        req.message = MagicMock()
        assert room_center._validate_send_message_request(req) is None

    def test_returns_error_when_room_id_missing(self, room_center):
        req = MagicMock()
        req.room_id = None
        req.message = MagicMock()
        result = room_center._validate_send_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400

    def test_returns_error_when_message_missing(self, room_center):
        req = MagicMock()
        req.room_id = "room-001"
        req.message = None
        result = room_center._validate_send_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400

    def test_returns_error_when_message_text_exceeds_max_length(self, room_center):
        """SDR 2.10: Messages exceeding MAX_MESSAGE_LENGTH should be rejected."""
        from models.room import MAX_MESSAGE_LENGTH, MessageContent, RoomUserMessage

        oversized_message = RoomUserMessage(
            room_id="room-001",
            message_id="msg-001",
            message_content=MessageContent(message_text="x" * (MAX_MESSAGE_LENGTH + 1)),
        )
        req = MagicMock()
        req.room_id = "room-001"
        req.message = oversized_message
        result = room_center._validate_send_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400
        assert "maximum length" in result.error.lower()

    def test_accepts_message_at_max_length(self, room_center):
        """Messages exactly at MAX_MESSAGE_LENGTH should be accepted."""
        from models.room import MAX_MESSAGE_LENGTH, MessageContent, RoomUserMessage

        ok_message = RoomUserMessage(
            room_id="room-001",
            message_id="msg-002",
            message_content=MessageContent(message_text="x" * MAX_MESSAGE_LENGTH),
        )
        req = MagicMock()
        req.room_id = "room-001"
        req.message = ok_message
        result = room_center._validate_send_message_request(req)
        assert result is None
