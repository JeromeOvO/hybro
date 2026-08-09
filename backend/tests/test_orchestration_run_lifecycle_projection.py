from copy import deepcopy
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from common.a2a_constants import SSEProcessingStatus
from models.run import RunEventType, RunState


class InMemoryRunRepository:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = {doc["run_id"]: deepcopy(doc) for doc in docs or []}
        self.inserted: list[dict[str, Any]] = []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def find_one(self, query: dict[str, Any], **_kwargs) -> dict[str, Any] | None:
        run_id = query.get("run_id")
        if run_id is None or run_id not in self.docs:
            return None
        return deepcopy(self.docs[run_id])

    async def insert_one(self, document: dict[str, Any]) -> str:
        run_id = document["run_id"]
        if run_id in self.docs:
            raise DuplicateKeyError("duplicate run_id")
        self.docs[run_id] = deepcopy(document)
        self.inserted.append(deepcopy(document))
        return run_id

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> bool:
        run_id = query["run_id"]
        if run_id not in self.docs:
            return False
        fields = update.get("$set", {})
        self.docs[run_id].update(deepcopy(fields))
        self.updates.append((deepcopy(query), deepcopy(update)))
        return True


class InMemoryRunEventRepository:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = [deepcopy(event) for event in events or []]
        self.inserted: list[dict[str, Any]] = []

    async def find_one(
        self,
        query: dict[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
    ) -> dict[str, Any] | None:
        matches = [event for event in self.events if self._matches(event, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda event: event.get(key), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    async def insert_one(self, document: dict[str, Any]) -> str:
        for event in self.events:
            if (event["run_id"], event["seq"]) == (
                document["run_id"],
                document["seq"],
            ):
                raise DuplicateKeyError("duplicate run seq")
            if (
                document.get("causation_id")
                and event.get("run_id") == document.get("run_id")
                and event.get("type") == document.get("type")
                and event.get("causation_id") == document.get("causation_id")
            ):
                raise DuplicateKeyError("duplicate causation")
        self.events.append(deepcopy(document))
        self.inserted.append(deepcopy(document))
        return document["event_id"]

    def _matches(self, event: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            actual = event.get(key)
            if isinstance(expected, dict):
                if "$gt" in expected and not actual > expected["$gt"]:
                    return False
                continue
            if actual != expected:
                return False
        return True


@pytest.mark.asyncio
async def test_project_run_state_is_idempotent_by_causation_id(monkeypatch):
    import execution.run_lifecycle_service as mod
    from execution.run_lifecycle_service import run_lifecycle_service

    handler = AsyncMock()
    handler.project_run_state = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(mod, "run_command_handler", handler)

    result = await run_lifecycle_service.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="msg-1",
        target_state=RunState.COMPLETED,
        terminal_reason="done",
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )

    assert result == {"run_id": "run-1"}
    handler.project_run_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_run_state_reuses_existing_event_for_same_causation_id(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    existing_event = {
        "event_id": "evt-1",
        "run_id": "run-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_completed",
        "payload": {"error_code": None, "error_message": "done"},
        "causation_id": "orch-event-1",
        "ts": "2026-01-01T00:00:00Z",
    }

    run_repo = AsyncMock()
    event_repo = AsyncMock()
    event_repo.find_one.return_value = existing_event
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    result = await handler.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="msg-1",
        target_state=RunState.COMPLETED,
        terminal_reason="done",
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )

    assert result == {
        "event_id": "evt-1",
        "run_id": "run-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_completed",
        "payload": {"error_code": None, "error_message": "done"},
        "ts": "2026-01-01T00:00:00Z",
    }
    event_repo.find_one.assert_awaited_once_with(
        {
            "run_id": "run-1",
            "type": "run_completed",
            "causation_id": "orch-event-1",
        }
    )
    event_repo.insert_one.assert_not_awaited()
    run_repo.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_run_state_rejects_orchestration_status_before_repository_access(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler
    from models.orchestration import OrchestrationStatus

    run_repo = AsyncMock()
    event_repo = AsyncMock()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    with pytest.raises(TypeError, match="public RunState"):
        await handler.project_run_state(
            room_id="room-1",
            run_id="run-1",
            trigger_message_id="msg-1",
            target_state=OrchestrationStatus.RUNNING,
            terminal_reason=None,
            causation_id="orch-event-1",
            client_request_id="cr-1",
        )

    run_repo.find_one.assert_not_awaited()
    event_repo.find_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("processing_status", "target_state", "expected_event_type"),
    [
        (
            SSEProcessingStatus.PROCESSING,
            RunState.PROCESSING,
            RunEventType.RUN_RESUMED,
        ),
        (
            SSEProcessingStatus.AWAITING_INPUT,
            RunState.AWAITING_INPUT,
            RunEventType.RUN_AWAITING_INPUT,
        ),
    ],
)
async def test_project_run_state_binds_causation_when_head_is_already_at_target(
    monkeypatch,
    processing_status,
    target_state,
    expected_event_type,
):
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository()
    event_repo = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )
    await handler.record_processing_status(
        room_id="room-1",
        status=processing_status,
        message_id="run-1",
        client_request_id="cr-1",
    )

    first = await handler.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="run-1",
        target_state=target_state,
        terminal_reason=None,
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )
    second = await handler.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="run-1",
        target_state=target_state,
        terminal_reason=None,
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )

    assert first is not None
    assert second == first
    assert first["type"] == expected_event_type.value
    assert event_repo.events[-1]["causation_id"] == "orch-event-1"
    assert len(event_repo.events) == 3
    assert run_repo.docs["run-1"]["seq"] == first["seq"] == 3


@pytest.mark.asyncio
async def test_run_lifecycle_adapter_delegates_project_run_state():
    from execution.run_lifecycle import RunLifecycleAdapter

    command_handler = AsyncMock()
    command_handler.project_run_state = AsyncMock(return_value={"run_id": "run-1"})
    adapter = RunLifecycleAdapter(
        command_handler=command_handler,
        run_repository=AsyncMock(),
    )

    result = await adapter.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )

    assert result == {"run_id": "run-1"}
    command_handler.project_run_state.assert_awaited_once_with(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-event-1",
        client_request_id="cr-1",
        terminal_summary=None,
    )


@pytest.mark.asyncio
async def test_project_run_state_retains_public_run_id_when_trigger_differs(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository()
    event_repo = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    result = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-running-1",
        client_request_id="cr-1",
    )

    run_doc = run_repo.docs["public-run-1"]
    assert "trigger-msg-1" not in run_repo.docs
    assert run_doc["run_id"] == "public-run-1"
    assert run_doc["trigger_message_id"] == "trigger-msg-1"
    assert run_doc["parent_message_id"] == "trigger-msg-1"
    assert run_doc["client_request_id"] == "cr-1"
    assert run_doc["state"] == RunState.PROCESSING.value
    assert run_doc["seq"] == result["seq"] == 2
    assert isinstance(run_doc["started_at"], datetime)
    assert isinstance(run_doc["updated_at"], datetime)
    assert result["run_id"] == "public-run-1"
    assert event_repo.events[-1]["run_id"] == "public-run-1"
    assert event_repo.events[-1]["type"] == RunEventType.RUN_STARTED.value
    assert event_repo.events[-1]["causation_id"] == "orch-running-1"


@pytest.mark.asyncio
async def test_project_run_state_causation_replay_does_not_advance_seq(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository()
    event_repo = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    first = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.COMPLETED,
        terminal_reason="done",
        causation_id="orch-terminal-1",
        client_request_id="cr-1",
    )
    second = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.COMPLETED,
        terminal_reason="done",
        causation_id="orch-terminal-1",
        client_request_id="cr-1",
    )

    assert second == first
    assert len(event_repo.events) == 2
    assert len(event_repo.inserted) == 2
    assert run_repo.docs["public-run-1"]["seq"] == first["seq"] == 2
    assert event_repo.events[-1]["type"] == RunEventType.RUN_COMPLETED.value
    assert event_repo.events[-1]["causation_id"] == "orch-terminal-1"


@pytest.mark.asyncio
async def test_terminal_projection_does_not_append_on_terminal_head():
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository(
        [
            {
                "run_id": "public-run-1",
                "room_id": "room-1",
                "trigger_message_id": "trigger-msg-1",
                "state": RunState.COMPLETED.value,
                "seq": 4,
            }
        ]
    )
    event_repo = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    repaired = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.COMPLETED,
        terminal_reason="done",
        causation_id="orchestration-terminal-repair:public-run-1:completed",
    )

    assert repaired is None
    assert run_repo.docs["public-run-1"]["state"] == RunState.COMPLETED.value
    assert run_repo.docs["public-run-1"]["seq"] == 4
    assert event_repo.events == []


@pytest.mark.asyncio
async def test_project_run_state_terminal_projection_keeps_head_consistent(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository()
    event_repo = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-running-1",
        client_request_id="cr-1",
    )
    terminal = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.FAILED,
        terminal_reason="planner failed",
        causation_id="orch-terminal-1",
        client_request_id="cr-1",
        terminal_summary={
            "code": "orchestration_failed",
            "recommended_next_action": "retry_or_fail",
        },
    )

    run_doc = run_repo.docs["public-run-1"]
    assert run_doc["state"] == RunState.FAILED.value
    assert run_doc["seq"] == terminal["seq"] == 3
    assert run_doc["error_code"] == "FAILED"
    assert run_doc["error_message"] == "planner failed"
    assert run_doc["terminal_summary"] == {
        "code": "orchestration_failed",
        "recommended_next_action": "retry_or_fail",
    }
    assert isinstance(run_doc["ended_at"], datetime)
    assert isinstance(run_doc["updated_at"], datetime)
    assert terminal["payload"] == {
        "error_code": "FAILED",
        "error_message": "planner failed",
        "terminal_summary": {
            "code": "orchestration_failed",
            "recommended_next_action": "retry_or_fail",
        },
    }


@pytest.mark.asyncio
async def test_project_run_state_replay_repairs_stale_head_without_appending(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    existing_event = {
        "event_id": "evt-existing",
        "run_id": "public-run-1",
        "room_id": "room-1",
        "seq": 3,
        "type": RunEventType.RUN_FAILED.value,
        "payload": {
            "error_code": "FAILED",
            "error_message": "planner failed",
            "terminal_summary": {"code": "orchestration_failed"},
        },
        "causation_id": "orch-terminal-1",
        "ts": "2026-01-01T00:00:00Z",
    }
    run_repo = InMemoryRunRepository(
        [
            {
                "run_id": "public-run-1",
                "room_id": "room-1",
                "trigger_message_id": "trigger-msg-1",
                "client_request_id": "cr-1",
                "state": RunState.PROCESSING.value,
                "seq": 2,
            }
        ]
    )
    event_repo = InMemoryRunEventRepository([existing_event])
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    result = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.FAILED,
        terminal_reason="planner failed",
        causation_id="orch-terminal-1",
        client_request_id="cr-1",
        terminal_summary={"code": "orchestration_failed"},
    )

    assert result["event_id"] == "evt-existing"
    assert event_repo.inserted == []
    assert run_repo.docs["public-run-1"]["state"] == RunState.FAILED.value
    assert run_repo.docs["public-run-1"]["seq"] == 3
    assert run_repo.docs["public-run-1"]["ended_at"] == existing_event["ts"]
    assert run_repo.docs["public-run-1"]["terminal_summary"] == {
        "code": "orchestration_failed"
    }


@pytest.mark.asyncio
async def test_project_run_state_active_replay_repairs_started_at_without_appending(
    monkeypatch,
):
    from execution.run_command_handler import RunCommandHandler

    existing_event = {
        "event_id": "evt-existing",
        "run_id": "public-run-1",
        "room_id": "room-1",
        "seq": 2,
        "type": RunEventType.RUN_STARTED.value,
        "payload": {},
        "causation_id": "orch-running-1",
        "ts": "2026-01-01T00:00:00Z",
    }
    run_repo = InMemoryRunRepository(
        [
            {
                "run_id": "public-run-1",
                "room_id": "room-1",
                "trigger_message_id": "trigger-msg-1",
                "client_request_id": "cr-1",
                "state": RunState.QUEUED.value,
                "seq": 1,
                "started_at": None,
            }
        ]
    )
    event_repo = InMemoryRunEventRepository([existing_event])
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    result = await handler.project_run_state(
        room_id="room-1",
        run_id="public-run-1",
        trigger_message_id="trigger-msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-running-1",
        client_request_id="cr-1",
    )

    assert result["event_id"] == "evt-existing"
    assert event_repo.inserted == []
    assert run_repo.docs["public-run-1"]["state"] == RunState.PROCESSING.value
    assert run_repo.docs["public-run-1"]["seq"] == 2
    assert run_repo.docs["public-run-1"]["started_at"] == existing_event["ts"]
    assert run_repo.docs["public-run-1"]["updated_at"] == existing_event["ts"]


@pytest.mark.asyncio
async def test_run_lifecycle_indexes_include_projection_causation_unique_index():
    from container import _ensure_run_lifecycle_indexes

    class IndexCollection:
        def __init__(self) -> None:
            self.create_index_calls: list[
                tuple[list[tuple[str, int]], dict[str, Any]]
            ] = []

        async def create_index(self, keys, **kwargs):
            self.create_index_calls.append((deepcopy(keys), deepcopy(kwargs)))
            return kwargs["name"]

    class IndexMongo:
        def __init__(self) -> None:
            self.collections: dict[str, IndexCollection] = {}

        def collection(self, name: str) -> IndexCollection:
            if name not in self.collections:
                self.collections[name] = IndexCollection()
            return self.collections[name]

    mongo = IndexMongo()

    await _ensure_run_lifecycle_indexes(mongo=mongo)

    assert (
        [
            ("run_id", 1),
            ("type", 1),
            ("causation_id", 1),
        ],
        {
            "unique": True,
            "name": "run_type_causation_unique",
            "partialFilterExpression": {"causation_id": {"$type": "string"}},
        },
    ) in mongo.collections["run_events"].create_index_calls
    assert (
        [
            ("terminal_projection.pending", 1),
            ("terminal_projection.next_attempt_at", 1),
            ("ts", 1),
        ],
        {
            "name": "pending_terminal_projection",
            "unique": False,
            "partialFilterExpression": {"terminal_projection.pending": True},
        },
    ) in mongo.collections["run_events"].create_index_calls


class RacingTerminalRunEventRepository(InMemoryRunEventRepository):
    def __init__(self, *, opposing: bool = False) -> None:
        super().__init__()
        self.opposing = opposing
        self.raced = False

    async def insert_one(self, document: dict[str, Any]) -> str:
        if document["type"] == RunEventType.RUN_FAILED.value and not self.raced:
            self.raced = True
            winner = deepcopy(document)
            winner["event_id"] = "canonical-winner"
            if self.opposing:
                winner["type"] = RunEventType.RUN_CANCELED.value
                winner["terminal_projection"]["canonical_status"] = "canceled"
            self.events.append(winner)
            raise DuplicateKeyError("duplicate run seq")
        return await super().insert_one(document)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opposing", "expect_replay"),
    [(False, True), (True, False)],
)
async def test_causationless_run_seq_race_reads_canonical_terminal_winner(
    opposing,
    expect_replay,
):
    from execution.run_command_handler import RunCommandHandler

    run_repo = InMemoryRunRepository()
    event_repo = RacingTerminalRunEventRepository(opposing=opposing)
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )

    outcome = await handler.write_processing_status(
        room_id="room-1",
        status="failed",
        message_id="run-1",
        details="failed",
    )

    if expect_replay:
        assert outcome.status == "replayed"
        assert outcome.payload is not None
        assert outcome.payload["event_id"] == "canonical-winner"
        assert outcome.payload["type"] == RunEventType.RUN_FAILED.value
    else:
        assert outcome.status == "conflict"
        assert event_repo.events[-1]["type"] == RunEventType.RUN_CANCELED.value
        assert run_repo.docs["run-1"]["state"] == RunState.CANCELED.value
