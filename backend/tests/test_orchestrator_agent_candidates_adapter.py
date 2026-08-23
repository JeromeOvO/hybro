from __future__ import annotations

from common.dto.agent import AgentInfo
from execution.adapters.agent_candidates import AgentServiceCandidateSource


class FakeRegistry:
    def __init__(self, infos):
        self.infos = {info.agent_id: info for info in infos}

    async def get_agents_by_ids(self, agent_ids):
        return [
            self.infos[agent_id] for agent_id in agent_ids if agent_id in self.infos
        ]


class FakeExclusion:
    def __init__(self, excluded=frozenset()):
        self.excluded = excluded

    async def get_excluded_agent_ids(self):
        return self.excluded


def _info(
    agent_id,
    *,
    status="active",
    public=True,
    provider_id=None,
    hub_id=None,
    url="https://agent.example/a2a",
    raw_card=None,
) -> AgentInfo:
    card = {
        "name": f"Agent {agent_id}",
        "description": "description",
        "url": url,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": True},
    }
    if raw_card:
        card.update(raw_card)
    return AgentInfo(
        agent_id=agent_id,
        name=card["name"],
        description=card["description"],
        url=card["url"],
        provider_id=provider_id,
        status=status,
        capabilities=list(card.get("capabilities", {}).keys()),
        source="hub" if hub_id else "cloud",
        hub_id=hub_id,
        is_hub_online=True,
        is_public=public,
        public_url=None,
        raw_card=card,
    )


async def test_active_direct_agent_produces_candidate():
    source = AgentServiceCandidateSource(
        agents=FakeRegistry([_info("agent-1")]),
        exclusion_reader=FakeExclusion(),
    )
    candidates = await source.list_candidates(
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        candidate_agent_ids=["agent-1"],
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.agent_id == "agent-1"
    assert candidate.active is True
    assert candidate.authorized is True
    assert candidate.excluded is False
    assert candidate.transport_kind == "direct"
    assert candidate.endpoint_scope == "https://agent.example/a2a"
    assert "stream" in candidate.direct_capabilities


async def test_inactive_or_private_or_excluded_flags_are_surface():
    source = AgentServiceCandidateSource(
        agents=FakeRegistry(
            [
                _info("inactive", status="inactive"),
                _info("private", public=False, provider_id="other"),
                _info("excluded"),
            ]
        ),
        exclusion_reader=FakeExclusion(excluded=frozenset({"excluded"})),
    )
    candidates = await source.list_candidates(
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        candidate_agent_ids=["inactive", "private", "excluded"],
    )
    by_id = {candidate.agent_id: candidate for candidate in candidates}
    assert by_id["inactive"].active is False
    assert by_id["private"].authorized is False
    assert by_id["excluded"].excluded is True


async def test_hub_agent_uses_relay_transport_and_hub_scope():
    source = AgentServiceCandidateSource(
        agents=FakeRegistry([_info("hub-agent", hub_id="hub-1")]),
        exclusion_reader=FakeExclusion(),
    )
    candidates = await source.list_candidates(
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        candidate_agent_ids=["hub-agent"],
    )
    assert candidates[0].transport_kind == "direct"


async def test_skills_produce_per_skill_and_whole_agent_candidates():
    info = _info(
        "agent-1",
        raw_card={
            "skills": [
                {"id": "skill-1", "name": "Summarize", "description": "summary"},
                {"id": "skill-2", "name": "Translate", "description": "translate"},
            ]
        },
    )
    source = AgentServiceCandidateSource(
        agents=FakeRegistry([info]),
        exclusion_reader=FakeExclusion(),
    )
    candidates = await source.list_candidates(
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        candidate_agent_ids=["agent-1"],
    )
    skill_ids = {candidate.skill_id for candidate in candidates}
    assert skill_ids == {"skill-1", "skill-2", None}
