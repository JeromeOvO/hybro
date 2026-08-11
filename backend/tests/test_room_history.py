from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api_gateway.routes.room_routes import (
    PinnedRoomOrderUpdate,
    delete_room_history_item,
    get_room_history,
    reorder_pinned_rooms,
)
from common.auth import ClerkUser
from execution.repository.mongo import RunMongoRepository
from room.facade import RoomFacade
from room.repository.mongo import RoomMongoRepository


@pytest.mark.asyncio
async def test_bulk_room_status_uses_one_priority_aggregation():
    rows = [
        {"run_id": "working-old", "room_id": "r1", "state": "processing"},
        {"run_id": "failed-new", "room_id": "r2", "state": "failed"},
        {"run_id": "waiting-old", "room_id": "r3", "state": "awaiting_input"},
    ]
    collection = SimpleNamespace(aggregate=AsyncMock(return_value=rows))
    mongo = SimpleNamespace(collection=lambda _name: collection)
    repository = RunMongoRepository(mongo)

    result = await repository.get_latest_for_rooms(["r1", "r2", "r3"])

    assert result == rows
    collection.aggregate.assert_awaited_once()
    pipeline = collection.aggregate.await_args.args[0]
    assert pipeline[0] == {
        "$match": {
            "room_id": {"$in": ["r1", "r2", "r3"]},
            "state": {"$in": ["queued", "processing", "awaiting_input"]},
        }
    }
    branches = pipeline[1]["$addFields"]["_history_status_priority"]["$switch"][
        "branches"
    ]
    assert [(branch["case"]["$eq"][1], branch["then"]) for branch in branches] == [
        ("awaiting_input", 3),
        ("processing", 2),
        ("queued", 1),
    ]
    assert pipeline[2] == {
        "$sort": {
            "room_id": 1,
            "_history_status_priority": -1,
            "updated_at": -1,
        }
    }


@pytest.mark.asyncio
async def test_room_activity_touch_is_monotonic():
    activity = datetime(2026, 8, 10, tzinfo=UTC)
    collection = SimpleNamespace(update_one=AsyncMock(return_value=True))
    mongo = SimpleNamespace(collection=lambda _name: collection)
    repository = RoomMongoRepository(mongo)

    assert await repository.touch_activity("r1", activity) is True
    collection.update_one.assert_awaited_once_with(
        {
            "room_id": "r1",
            "$or": [
                {"lifecycle_state": "active"},
                {"lifecycle_state": {"$exists": False}},
            ],
        },
        {"$max": {"last_activity_at": activity}},
    )


@pytest.mark.asyncio
async def test_history_repository_projects_sorts_and_limits_by_effective_activity():
    collection = SimpleNamespace(aggregate=AsyncMock(return_value=[]))
    mongo = SimpleNamespace(collection=lambda _name: collection)
    repository = RoomMongoRepository(mongo)

    assert await repository.get_history_by_owner("owner", limit=250) == []
    collection.aggregate.assert_awaited_once_with(
        [
            {
                "$match": {
                    "room_owner_id": "owner",
                    "$or": [
                        {"lifecycle_state": "active"},
                        {"lifecycle_state": {"$exists": False}},
                    ],
                }
            },
            {
                "$set": {
                    "_history_activity_at": {
                        "$ifNull": ["$last_activity_at", "$room_created_at"]
                    }
                }
            },
            {
                "$sort": {
                    "is_pinned": -1,
                    "pin_order": 1,
                    "_history_activity_at": -1,
                }
            },
            {"$limit": 100},
            {
                "$project": {
                    "_id": 0,
                    "room_id": 1,
                    "room_name": 1,
                    "room_owner_id": 1,
                    "room_owner_name": 1,
                    "room_created_at": 1,
                    "last_activity_at": 1,
                    "is_pinned": 1,
                    "pin_order": 1,
                }
            },
        ]
    )


@pytest.mark.asyncio
async def test_saving_messages_touches_durable_room_activity():
    now = datetime(2026, 8, 10, tzinfo=UTC)
    room_repository = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value={
                "room_id": "r1",
                "room_name": "Room",
                "room_owner_id": "owner",
                "room_owner_name": "Owner",
            }
        ),
        update_fields=AsyncMock(return_value={}),
    )
    message_repository = SimpleNamespace(save_user_message=AsyncMock(return_value="m1"))
    facade = RoomFacade(
        repository=room_repository,
        message_repository=message_repository,
        agent_registry=AsyncMock(),
        membership_source=AsyncMock(),
        id_factory=lambda: "m1",
        now=lambda: now,
    )

    from common.dto import UserMessageInput

    await facade.save_user_message(
        "r1",
        UserMessageInput(
            room_id="r1",
            message_text="Hello",
            sender_id="owner",
        ),
    )

    room_repository.update_fields.assert_awaited_once_with(
        "r1", {"last_activity_at": now}
    )


@pytest.mark.asyncio
async def test_streaming_agent_message_updates_do_not_touch_room_activity():
    room_repository = SimpleNamespace(touch_activity=AsyncMock(return_value=True))
    message_repository = SimpleNamespace(
        update_agent_message=AsyncMock(return_value=True)
    )
    facade = RoomFacade(
        repository=room_repository,
        message_repository=message_repository,
        agent_registry=AsyncMock(),
        membership_source=AsyncMock(),
        id_factory=lambda: "m1",
        now=lambda: datetime(2026, 8, 10, tzinfo=UTC),
    )
    message = SimpleNamespace(
        room_id="r1",
        model_dump=lambda **_kwargs: {"room_id": "r1", "message_content": {}},
    )

    assert await facade.update_agent_message("m1", message) is True

    message_repository.update_agent_message.assert_awaited_once_with(
        "m1", {"room_id": "r1", "message_content": {}}
    )
    room_repository.touch_activity.assert_not_awaited()


def _user(user_id: str = "owner") -> ClerkUser:
    return ClerkUser(user_id=user_id, session_id="session", claims={})


def _room(
    room_id: str,
    *,
    owner_id: str = "owner",
    pinned: bool = False,
    pin_order: float | None = None,
    activity_day: int = 1,
):
    activity = datetime(2026, 8, activity_day, tzinfo=UTC)
    return SimpleNamespace(
        room_id=room_id,
        room_name=f"Room {room_id}",
        room_owner_id=owner_id,
        room_created_at=activity,
        last_activity_at=activity,
        is_pinned=pinned,
        pin_order=pin_order,
    )


@pytest.mark.asyncio
async def test_history_uses_repository_order_and_bulk_status():
    rooms = [
        _room("pinned-one", pinned=True, pin_order=1, activity_day=1),
        _room("pinned-two", pinned=True, pin_order=2, activity_day=2),
        _room("recent-new", activity_day=3),
        _room("recent-old", activity_day=1),
    ]
    center = SimpleNamespace(
        inquiry_room_history_by_owner_id=AsyncMock(
            return_value=SimpleNamespace(success=True, room_list=rooms)
        )
    )
    engine = SimpleNamespace(
        get_latest_runs_for_rooms=AsyncMock(
            return_value={
                "pinned-one": SimpleNamespace(state="awaiting_input"),
                "recent-new": SimpleNamespace(state="completed"),
                "recent-old": SimpleNamespace(state="failed"),
            }
        )
    )

    response = await get_room_history(user=_user(), engine=engine, center=center)

    assert [item.room_id for item in response.items] == [
        "pinned-one",
        "pinned-two",
        "recent-new",
        "recent-old",
    ]
    assert [item.status for item in response.items] == [
        "awaiting_input",
        "idle",
        "idle",
        "idle",
    ]
    engine.get_latest_runs_for_rooms.assert_awaited_once_with(
        ["pinned-one", "pinned-two", "recent-new", "recent-old"]
    )
    request = center.inquiry_room_history_by_owner_id.await_args.args[0]
    assert request.room_owner_id == "owner"
    assert request.requesting_user_id == "owner"


@pytest.mark.asyncio
async def test_reorder_requires_complete_owned_pinned_set_before_writing():
    rooms = [
        _room("p1", pinned=True, pin_order=1),
        _room("p2", pinned=True, pin_order=2),
    ]
    center = SimpleNamespace(
        inquiry_rooms_by_room_owner_id=AsyncMock(
            return_value=SimpleNamespace(success=True, room_list=rooms)
        ),
        update_room_history_fields=AsyncMock(),
    )
    store = SimpleNamespace(get_room_by_room_id=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await reorder_pinned_rooms(
            payload=PinnedRoomOrderUpdate(room_ids=["p1"]),
            user=_user(),
            store=store,
            center=center,
        )

    assert exc_info.value.status_code == 409
    store.get_room_by_room_id.assert_not_awaited()
    center.update_room_history_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_reorder_persists_complete_manual_order_after_ownership_checks():
    rooms = [
        _room("p1", pinned=True, pin_order=1),
        _room("p2", pinned=True, pin_order=2),
    ]
    store = SimpleNamespace(
        get_room_by_room_id=AsyncMock(side_effect=[rooms[1], rooms[0]])
    )
    center = SimpleNamespace(
        inquiry_rooms_by_room_owner_id=AsyncMock(
            return_value=SimpleNamespace(success=True, room_list=rooms)
        ),
        update_room_history_fields=AsyncMock(
            return_value=SimpleNamespace(success=True)
        ),
    )

    result = await reorder_pinned_rooms(
        payload=PinnedRoomOrderUpdate(room_ids=["p2", "p1"]),
        user=_user(),
        store=store,
        center=center,
    )

    assert result == {"success": True}
    requests = [
        call.args[0] for call in center.update_room_history_fields.await_args_list
    ]
    assert [(request.room_id, request.pin_order) for request in requests] == [
        ("p2", 1.0),
        ("p1", 2.0),
    ]
    assert all(request.requesting_user_id == "owner" for request in requests)


@pytest.mark.asyncio
async def test_delete_history_checks_ownership_and_delegates_to_deletion_service():
    room = _room("r1")
    store = SimpleNamespace(get_room_by_room_id=AsyncMock(return_value=room))
    center = SimpleNamespace(
        delete_room_by_room_id=AsyncMock(return_value=SimpleNamespace(success=True))
    )

    result = await delete_room_history_item(
        "r1", user=_user(), store=store, center=center
    )

    assert result == {"success": True}
    request = center.delete_room_by_room_id.await_args.args[0]
    assert request.room_id == "r1"
    assert request.requesting_user_id == "owner"

    with pytest.raises(HTTPException) as exc_info:
        await delete_room_history_item(
            "r1", user=_user("other"), store=store, center=center
        )
    assert exc_info.value.status_code == 403
    assert center.delete_room_by_room_id.await_count == 1
