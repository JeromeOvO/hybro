from __future__ import annotations

import pytest

from common.dto import AgentInfo, CreateRoomRequest, MembershipSeed, UserMessageInput
from tests.test_room_facade import NOW, _facade


@pytest.mark.asyncio
async def test_golden_room_create_membership_provenance_cases():
    facade, _, _, _, _ = _facade(
        agents=[
            AgentInfo(agent_id="a1", name="Agent One"),
            AgentInfo(agent_id="a2", name="Agent Two"),
        ],
        ids=["room-1"],
    )

    room = await facade.create_room(
        CreateRoomRequest(
            owner_id="owner",
            owner_name="Owner",
            room_name="Golden Room",
            membership_seed=MembershipSeed(mode="manual", agent_ids=["a1", "a2"]),
        )
    )

    assert room.room_id == "room-1"
    assert room.room_name == "Golden Room"
    assert dict(room.agent_set) == {"a1": "Agent One", "a2": "Agent Two"}
    assert room.membership_origin == "manual"
    assert room.membership_origin_status == "manual"


@pytest.mark.asyncio
async def test_golden_user_message_dispatch_root_matches_message_id():
    facade, _, messages, _, _ = _facade(
        room_docs=[
            {
                "room_id": "r1",
                "room_name": "Room",
                "room_owner_id": "owner",
                "room_owner_name": "Owner",
                "room_agent_set": {},
            }
        ],
        ids=["user-message-1"],
    )

    saved = await facade.save_user_message(
        "r1",
        UserMessageInput(
            room_id="r1",
            sender_id="owner",
            sender_name="Owner",
            message_text="Hello",
            client_request_id="client-1",
        ),
    )

    assert saved.message_id == "user-message-1"
    assert saved.dispatch_root_message_id == "user-message-1"
    assert messages.user_messages["user-message-1"]["client_request_id"] == "client-1"


@pytest.mark.asyncio
async def test_golden_delete_history_and_ownership_behaviors():
    facade, _, messages, _, _ = _facade(
        room_docs=[
            {
                "room_id": "r1",
                "room_name": "Room",
                "room_owner_id": "owner",
                "room_owner_name": "Owner",
                "room_agent_set": {"a1": "Agent One"},
            }
        ],
        agents=[AgentInfo(agent_id="a1", name="Agent One", hub_id="hub-1")],
    )
    messages.user_messages["u1"] = {
        "room_id": "r1",
        "message_id": "u1",
        "message_type": "user",
        "user_id": "owner",
        "message_content": {"message_text": "Hello"},
        "message_created_at": NOW,
    }
    messages.agent_messages["a1"] = {
        "room_id": "r1",
        "message_id": "a1",
        "message_type": "agent",
        "agent_id": "a1",
        "related_message_id": "u1",
        "message_content": {"message_text": "Hi"},
        "message_created_at": NOW,
    }

    assert await facade.verify_room_agent_membership("r1", "a1") is True
    assert await facade.verify_room_hub_ownership("r1", "hub-1") is True
    assert [message.message_id for message in await facade.get_messages_for_room("r1")] == [
        "u1",
        "a1",
    ]
    assert await facade.delete_room("r1", "not-owner") is False
    assert await facade.delete_room("r1", "owner") is True
    assert messages.deleted_rooms == ["r1"]
