"""Container discriminator and the chat write guard.

A Session stores its conversation in a container with
``owner_product="session"``. Chat history must not list it, chat write routes
must refuse it, and chat read routes keep serving it so the owner's session
page can render the record.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api_gateway.routes.files_routes import upload_file
from api_gateway.routes.room_routes import (
    PinnedRoomOrderUpdate,
    RoomHistoryUpdate,
    delete_room_history_item,
    inquiry_room_messages,
    inquiry_room_setting,
    reorder_pinned_rooms,
    send_message,
    update_room_agent_set,
    update_room_history_item,
)
from api_gateway.routes.sse_routes import cancel_message
from common.auth import ClerkUser
from common.dto import (
    AgentInfo,
    CreateRoomRequest,
    MembershipSeed,
    RoomInfo,
)
from conversation.repository.mongo import RoomMongoRepository
from conversation.translators import create_room_doc, room_info_from_doc
from models.room import MessageContent, Room, RoomUserMessage
from tests.test_room_facade import NOW, _facade

OWNER = "owner-1"


def _user() -> ClerkUser:
    return ClerkUser(user_id=OWNER, session_id="s-1", claims={})


def _room(owner_product: str = "session", **overrides) -> Room:
    values = {
        "room_id": "room-1",
        "room_name": "Session",
        "room_owner_id": OWNER,
        "room_owner_name": "Owner",
        "owner_product": owner_product,
        "is_pinned": True,
        "pin_order": 1.0,
    }
    values.update(overrides)
    return Room(**values)


def _store(room: Room) -> MagicMock:
    store = MagicMock()
    store.get_room_by_room_id = AsyncMock(return_value=room)
    return store


def _json_request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


def _assert_session_refused(exc_info) -> None:
    assert exc_info.value.status_code == 409
    assert "session" in str(exc_info.value.detail).lower()


# -- Discriminator ------------------------------------------------------------


def test_models_default_to_chat_product():
    assert Room(
        room_name="r", room_owner_id="o", room_owner_name="O"
    ).owner_product == ("chat")
    assert (
        CreateRoomRequest(
            owner_id="o",
            owner_name="O",
            room_name="r",
            membership_seed=MembershipSeed(mode="manual"),
        ).owner_product
        == "chat"
    )
    assert RoomInfo(room_id="r", room_name="r", owner_id="o").owner_product == "chat"


def test_create_room_doc_and_room_info_thread_owner_product():
    doc = create_room_doc(
        room_id="r1",
        owner_id="o",
        owner_name="O",
        room_name="r",
        agent_set={},
        created_at=NOW,
        membership_origin="manual",
        membership_origin_status="manual",
        owner_product="session",
    )

    assert doc["owner_product"] == "session"
    assert room_info_from_doc(doc).owner_product == "session"
    # Rooms created before the discriminator existed are chat rooms.
    doc.pop("owner_product")
    assert room_info_from_doc(doc).owner_product == "chat"


@pytest.mark.asyncio
async def test_conversation_facade_creates_session_container():
    facade, rooms, _, _, _ = _facade(
        agents=[AgentInfo(agent_id="a1", name="Agent One")],
        ids=["room-session"],
    )

    room = await facade.create_room(
        CreateRoomRequest(
            owner_id=OWNER,
            owner_name="Owner",
            room_name="Client: plan a trip",
            membership_seed=MembershipSeed(mode="manual", agent_ids=["a1"]),
            owner_product="session",
        )
    )

    assert room.owner_product == "session"
    assert rooms.created_docs[-1]["owner_product"] == "session"


@pytest.mark.asyncio
async def test_chat_history_query_excludes_session_containers():
    collection = SimpleNamespace(aggregate=AsyncMock(return_value=[]))
    repository = RoomMongoRepository(SimpleNamespace(collection=lambda _n: collection))

    await repository.get_history_by_owner(OWNER)

    match = collection.aggregate.await_args.args[0][0]["$match"]
    assert match["owner_product"] == {"$ne": "session"}
    assert match["room_owner_id"] == OWNER


# -- Write routes refuse session containers -----------------------------------


@pytest.mark.asyncio
async def test_send_message_refuses_session_container():
    engine = MagicMock()
    engine.execute = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await send_message(
            _json_request(
                {
                    "room_id": "room-1",
                    "client_request_id": "cr-1",
                    "mode": "direct",
                    "message": {"message_content": {"message_text": "hi"}},
                    "agent_scope": {"source": "room_default"},
                }
            ),
            user=_user(),
            store=_store(_room()),
            engine=engine,
            center=MagicMock(),
        )

    _assert_session_refused(exc_info)
    engine.execute.assert_not_called()


@pytest.mark.asyncio
async def test_update_room_agent_set_refuses_session_container():
    center = MagicMock()
    center.update_room_agent_set = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await update_room_agent_set(
            _json_request({"room_id": "room-1", "room_agent_ids": ["a2"]}),
            user=_user(),
            store=_store(_room()),
            center=center,
        )

    _assert_session_refused(exc_info)
    center.update_room_agent_set.assert_not_called()


@pytest.mark.asyncio
async def test_update_room_agent_set_still_serves_chat_rooms():
    center = MagicMock()
    center.update_room_agent_set = AsyncMock(return_value={"success": True})

    result = await update_room_agent_set(
        _json_request({"room_id": "room-1", "room_agent_ids": ["a2"]}),
        user=_user(),
        store=_store(_room("chat")),
        center=center,
    )

    assert result == {"success": True}


@pytest.mark.asyncio
async def test_history_patch_refuses_session_container():
    center = MagicMock()
    center.update_room_name = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await update_room_history_item(
            "room-1",
            RoomHistoryUpdate(title="Renamed"),
            user=_user(),
            store=_store(_room()),
            center=center,
        )

    _assert_session_refused(exc_info)
    center.update_room_name.assert_not_called()


@pytest.mark.asyncio
async def test_pinned_order_refuses_session_container():
    center = MagicMock()
    center.inquiry_rooms_by_room_owner_id = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status_code=200,
            error=None,
            room_list=[SimpleNamespace(room_id="room-1", is_pinned=True)],
        )
    )
    center.update_room_history_fields = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await reorder_pinned_rooms(
            PinnedRoomOrderUpdate(room_ids=["room-1"]),
            user=_user(),
            store=_store(_room()),
            center=center,
        )

    _assert_session_refused(exc_info)
    center.update_room_history_fields.assert_not_called()


@pytest.mark.asyncio
async def test_history_delete_refuses_session_container():
    center = MagicMock()
    center.delete_room_by_room_id = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await delete_room_history_item(
            "room-1", user=_user(), store=_store(_room()), center=center
        )

    _assert_session_refused(exc_info)
    center.delete_room_by_room_id.assert_not_called()


@pytest.mark.asyncio
async def test_sse_cancel_refuses_session_container():
    db = MagicMock()
    db.get_room_user_message_by_message_id = AsyncMock(
        return_value=RoomUserMessage(
            room_id="room-1",
            message_id="msg-1",
            user_id=OWNER,
            message_content=MessageContent(message_text="hi"),
        )
    )
    db.get_room_by_room_id = AsyncMock(return_value=_room())
    engine = MagicMock()
    engine.cancel = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await cancel_message("msg-1", _user(), db=db, engine=engine)

    _assert_session_refused(exc_info)
    engine.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_file_upload_refuses_session_container():
    ownership = MagicMock()
    ownership.get_room_owner = AsyncMock(return_value=OWNER)
    storage = MagicMock()
    storage.upload = AsyncMock()
    upload = MagicMock()
    upload.read = AsyncMock(return_value=b"x")
    with pytest.raises(HTTPException) as exc_info:
        await upload_file(
            upload,
            room_id="room-1",
            user=_user(),
            storage=storage,
            room_ownership=ownership,
            store=_store(_room()),
        )

    _assert_session_refused(exc_info)
    storage.upload.assert_not_called()


# -- Read routes stay open ----------------------------------------------------


@pytest.mark.asyncio
async def test_read_routes_serve_session_containers():
    engine = MagicMock()
    engine.get_runs_for_room = AsyncMock(return_value=[])
    center = MagicMock()
    center.inquiry_room_setting = AsyncMock(
        return_value=SimpleNamespace(success=True, active_runs=None)
    )
    center.inquiry_room_messages_by_room_id = AsyncMock(return_value={"success": True})
    store = _store(_room())

    setting = await inquiry_room_setting(
        _json_request({"room_id": "room-1"}),
        user=_user(),
        store=store,
        engine=engine,
        center=center,
    )
    messages = await inquiry_room_messages(
        _json_request({"room_id": "room-1"}),
        user=_user(),
        store=store,
        center=center,
    )

    assert setting.success is True
    assert messages == {"success": True}
