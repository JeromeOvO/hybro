from __future__ import annotations

from common.dto.agent import AgentInfo
from execution.adapters.authorization import MembershipAuthorizationRefresh

from ._orchestrator_a2a_helpers import binding


class FakeRoomOwnership:
    def __init__(self, member=True):
        self.member = member

    async def verify_room_agent_membership(self, room_id, agent_id):
        return self.member


class FakeRegistry:
    def __init__(self, info):
        self.info = info

    async def get_agent(self, agent_id):
        return self.info


def _info(*, status="active", public=True, provider_id=None) -> AgentInfo:
    return AgentInfo(
        agent_id="agent-1",
        name="Agent",
        description="",
        url="https://agent.example/a2a",
        provider_id=provider_id,
        status=status,
        capabilities=[],
        source="cloud",
        is_public=public,
        raw_card={},
    )


async def test_authorized_when_member_active_and_visible():
    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info(provider_id="user-1")),
        room_ownership=FakeRoomOwnership(member=True),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=1,
        resource_refs=[],
    )
    assert outcome == "authorized"


async def test_denied_when_not_member():
    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info()),
        room_ownership=FakeRoomOwnership(member=False),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=1,
        resource_refs=[],
    )
    assert outcome == "denied"


async def test_denied_when_inactive():
    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info(status="inactive")),
        room_ownership=FakeRoomOwnership(member=True),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=1,
        resource_refs=[],
    )
    assert outcome == "denied"


async def test_denied_when_private_and_not_owner():
    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info(public=False, provider_id="other")),
        room_ownership=FakeRoomOwnership(member=True),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=1,
        resource_refs=[],
    )
    assert outcome == "denied"


async def test_transient_failure_on_connection_error():
    class BrokenOwnership:
        async def verify_room_agent_membership(self, room_id, agent_id):
            raise ConnectionError("down")

    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info()),
        room_ownership=BrokenOwnership(),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=1,
        resource_refs=[],
    )
    assert outcome == "transient_failure"


async def test_denied_when_room_epoch_mismatch():
    adapter = MembershipAuthorizationRefresh(
        agents=FakeRegistry(_info()),
        room_ownership=FakeRoomOwnership(member=True),
    )
    outcome = await adapter.authorize(
        binding=binding(),
        requesting_subject_id="user-1",
        room_id="room-1",
        room_epoch=999,
        resource_refs=[],
    )
    assert outcome == "denied"
