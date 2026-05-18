"""
Unit tests for RoomCenter (room_services.py) -- pure logic methods.

Tests cover:
- _looks_like_agent_id heuristic
- _normalize_room_agent_set canonical shape detection
- parse_agent_mentions extraction
- extract_agent_message_content per-agent routing
- _validate_send_message_request input validation
"""

import ast
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from common.dto import RoomInfo
from models.request import RoomCenterRoomSettingRequest
from services.room_services import RoomServices


@pytest.fixture
def room_center():
    """Create a RoomCenter with mocked dependencies."""
    rc = object.__new__(RoomServices)
    rc.database_service = MagicMock()
    rc.agent_service = MagicMock()
    rc.openai_service = MagicMock()
    rc.a2a_service = MagicMock()
    rc.room_memory_service = MagicMock()
    rc.sse_manager = MagicMock()
    rc.task_service = MagicMock()
    return rc


_ROOT = Path(__file__).resolve().parents[1]
_ROOM_SERVICES_PATH = _ROOT / "services" / "room_services.py"


def _room_services_function(function_name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(
        _ROOM_SERVICES_PATH.read_text(), filename=str(_ROOM_SERVICES_PATH)
    )
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
    ]
    assert matches, f"{function_name} not found"
    return matches[0]


def _room_services_call_line(
    function_name: str,
    call_name: str,
    *snippets: str,
    occurrence: int = 1,
) -> int:
    matches: list[tuple[int, str]] = []
    for node in ast.walk(_room_services_function(function_name)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = None
        if name != call_name:
            continue
        expression = ast.unparse(node)
        if all(snippet in expression for snippet in snippets):
            matches.append((node.lineno, expression))
    matches.sort()
    assert len(matches) >= occurrence, (
        f"{function_name}.{call_name} with {snippets!r} occurrence "
        f"{occurrence} not found; matches={matches}"
    )
    return matches[occurrence - 1][0]


def _matching_room_services_call(
    function_name: str,
    call_name: str,
    *snippets: str,
    occurrence: int = 1,
) -> ast.Call:
    matches: list[ast.Call] = []
    for node in ast.walk(_room_services_function(function_name)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = None
        if name != call_name:
            continue
        expression = ast.unparse(node)
        if all(snippet in expression for snippet in snippets):
            matches.append(node)
    matches.sort(key=lambda node: node.lineno)
    assert len(matches) >= occurrence, (
        f"{function_name}.{call_name} with {snippets!r} occurrence "
        f"{occurrence} not found; matches="
        f"{[(node.lineno, ast.unparse(node)) for node in matches]}"
    )
    return matches[occurrence - 1]


def _body_containing_statement(
    function: ast.AsyncFunctionDef,
    statement: ast.stmt,
) -> list[ast.stmt]:
    for node in ast.walk(function):
        bodies: list[list[ast.stmt]] = []
        for attr in ("body", "orelse", "finalbody"):
            body = getattr(node, attr, None)
            if isinstance(body, list) and all(isinstance(item, ast.stmt) for item in body):
                bodies.append(body)
        if isinstance(node, ast.Try):
            bodies.extend(handler.body for handler in node.handlers)
        if isinstance(node, ast.Match):
            bodies.extend(case.body for case in node.cases)
        for body in bodies:
            if any(item is statement for item in body):
                return body
    raise AssertionError(f"body containing statement at line {statement.lineno} not found")


def _statement_containing_call(
    function: ast.AsyncFunctionDef,
    call: ast.Call,
) -> ast.stmt:
    statements = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.stmt)
        and node.lineno <= call.lineno <= getattr(node, "end_lineno", node.lineno)
    ]
    assert statements, f"statement containing call at line {call.lineno} not found"
    statements.sort(
        key=lambda node: (
            getattr(node, "end_lineno", node.lineno) - node.lineno,
            -node.lineno,
        )
    )
    return statements[0]


def _statement_owning_body(
    function: ast.AsyncFunctionDef,
    body: list[ast.stmt],
) -> ast.stmt | None:
    if body is function.body:
        return None
    for node in ast.walk(function):
        if not isinstance(node, ast.stmt):
            continue
        bodies: list[list[ast.stmt]] = []
        for attr in ("body", "orelse", "finalbody"):
            candidate = getattr(node, attr, None)
            if isinstance(candidate, list) and all(
                isinstance(item, ast.stmt) for item in candidate
            ):
                bodies.append(candidate)
        if isinstance(node, ast.Try):
            bodies.extend(handler.body for handler in node.handlers)
        if isinstance(node, ast.Match):
            bodies.extend(case.body for case in node.cases)
        if any(candidate is body for candidate in bodies):
            return node
    raise AssertionError("owner for branch body not found")


def _preceding_path_statements(
    function: ast.AsyncFunctionDef,
    emit_statement: ast.stmt,
) -> list[ast.stmt]:
    path_statements: list[ast.stmt] = []
    current_statement: ast.stmt | None = emit_statement
    while current_statement is not None:
        body = _body_containing_statement(function, current_statement)
        emit_index = next(
            index
            for index, statement in enumerate(body)
            if statement is current_statement
        )
        path_statements.extend(body[:emit_index])
        current_statement = _statement_owning_body(function, body)
    return path_statements


def _path_calls(statement: ast.stmt) -> list[ast.Call]:
    if isinstance(statement, ast.If) and any(
        isinstance(node, ast.Return) for node in ast.walk(statement)
    ):
        return [node for node in ast.walk(statement.test) if isinstance(node, ast.Call)]
    return [node for node in ast.walk(statement) if isinstance(node, ast.Call)]


def _assert_room_service_before(
    function_name: str,
    before_call: str,
    before_snippets: tuple[str, ...],
    emit_snippets: tuple[str, ...],
    *,
    before_occurrence: int = 1,
    emit_occurrence: int = 1,
) -> None:
    function = _room_services_function(function_name)
    emit_call = _matching_room_services_call(
        function_name,
        "_emit_processing_status_event",
        *emit_snippets,
        occurrence=emit_occurrence,
    )
    emit_statement = _statement_containing_call(function, emit_call)
    path_statements = _preceding_path_statements(function, emit_statement)
    candidates: list[tuple[int, str]] = []
    for statement in path_statements:
        for node in _path_calls(statement):
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            else:
                name = None
            expression = ast.unparse(node)
            if name == before_call and all(
                snippet in expression for snippet in before_snippets
            ):
                candidates.append((node.lineno, expression))
    candidates.sort()
    assert len(candidates) >= before_occurrence, (
        f"{function_name}.{before_call} with {before_snippets!r} occurrence "
        f"{before_occurrence} not found in same path before emit line "
        f"{emit_call.lineno}; candidates={candidates}"
    )
    assert candidates[before_occurrence - 1][0] < emit_call.lineno


def test_send_message_failure_call_01_side_effects_before_failed_processing_status():
    _assert_room_service_before(
        "send_message_to_room",
        "_initialize_room_memory",
        (),
        ("Failed to initialize room memory",),
    )


def test_send_message_failure_call_02_side_effects_before_failed_processing_status():
    _assert_room_service_before(
        "send_message_to_room",
        "_resolve_explicit_target_scope",
        (),
        ("Agent selection failed",),
    )


def test_send_message_canceled_side_effects_before_canceled_processing_status():
    _assert_room_service_before(
        "send_message_to_room",
        "parse_user_message",
        (),
        ("SSEProcessingStatus.CANCELED",),
    )


def test_send_message_failure_call_03_side_effects_before_failed_processing_status():
    _assert_room_service_before(
        "send_message_to_room",
        "parse_user_message",
        (),
        ("Failed to parse user message",),
    )


def test_no_agents_fallback_side_effects_before_completed_processing_status():
    _assert_room_service_before(
        "_handle_no_agents_fallback",
        "add_room_agent_message",
        (),
        ("SSEProcessingStatus.COMPLETED",),
    )


@pytest.mark.asyncio
async def test_room_services_processing_status_uses_bound_execution_emitter():
    svc = object.__new__(RoomServices)
    emitter = AsyncMock(return_value=None)

    svc.bind_execution_event_deps(processing_status_emitter=emitter)

    await svc._send_processing_status("room-1", "msg-1", "cr-1")

    emitter.assert_awaited_once()
    assert emitter.await_args.kwargs["room_id"] == "room-1"
    assert emitter.await_args.kwargs["message_id"] == "msg-1"
    assert emitter.await_args.kwargs["lifecycle_message_id"] == "msg-1"
    assert emitter.await_args.kwargs["client_request_id"] == "cr-1"


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
    svc.database_service = MagicMock()
    svc.database_service.get_active_runs_by_room_id = AsyncMock(return_value=[])
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
async def test_delete_room_does_not_cleanup_when_requester_is_not_owner():
    svc = object.__new__(RoomServices)
    svc.database_service = MagicMock()
    svc.database_service.get_active_runs_by_room_id = AsyncMock(return_value=[])
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
    svc.database_service = MagicMock()
    svc.database_service.get_active_runs_by_room_id = AsyncMock(return_value=[])
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

    @pytest.mark.parametrize("value", [
        "550e8400-e29b-41d4-a716-446655440000",
        "550e8400e29b41d4a716446655440000",
    ])
    def test_recognizes_valid_uuids(self, value):
        assert RoomServices._looks_like_agent_id(value) is True

    @pytest.mark.parametrize("value", [
        "MyAgent",
        "agent-name",
        "",
        "not-a-uuid-at-all",
    ])
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
        data = {
            "550e8400e29b41d4a716446655440000": "660e8400e29b41d4a716446655440000"
        }
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
            {"agent_id": "a1", "agent_name": "Alpha", "mention_text": "<@a1|Alpha>", "position": 0},
            {"agent_id": "a2", "agent_name": "Beta", "mention_text": "<@a2|Beta>", "position": 22},
        ]

        result = room_center.extract_agent_message_content(text, "a1", "Alpha", mentions)
        assert "write code" in result
        assert "<@" not in result

    def test_returns_clean_text_when_agent_not_mentioned(self, room_center):
        """Agent not in mentions gets full text with all mentions stripped."""
        text = "<@a1|Alpha> do something"
        mentions = [
            {"agent_id": "a1", "agent_name": "Alpha", "mention_text": "<@a1|Alpha>", "position": 0},
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
