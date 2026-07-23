from __future__ import annotations

from dataclasses import dataclass

from agent.matching import (
    accepts_input_modes as _accepts_input_modes,
)
from agent.matching import (
    select_top_matches,
    supports_files,
)
from agent.service import _agent_info_to_legacy_agent
from common.protocols import AgentMessageMatcher
from models.agent import Agent


def _agent_to_dict(agent: Agent) -> dict:
    card = agent.agent_card.model_dump(mode="json")
    return {"agent_id": agent.agent_id, "agent_card": card}


def _agent_supports_files(agent: Agent) -> bool:
    return supports_files(_agent_to_dict(agent))


def accepts_input_modes(
    agent: Agent,
    required_input_modes: list[str] | None = None,
) -> bool:
    return _accepts_input_modes(_agent_to_dict(agent), required_input_modes)


@dataclass
class MatchedAgent:
    agent: Agent
    lexical_score: float
    final_score: float


@dataclass
class MatchResult:
    agents: list[MatchedAgent]
    total_candidates: int
    filtered_count: int


def select_top_agents(
    ranked: list[MatchedAgent],
    is_debate_mode: bool,
) -> list[MatchedAgent]:
    converted = [
        {
            "index": index,
            "final_score": match.final_score,
        }
        for index, match in enumerate(ranked)
    ]
    selected = select_top_matches(converted, is_debate_mode=is_debate_mode)
    return [ranked[item["index"]] for item in selected]


class AgentMatcher:
    def __init__(self, facade=None) -> None:
        self._facade = facade
        self._bound = facade is not None

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "AgentMatcher.bind_facade() not called - startup incomplete"
            )
        return self._facade

    async def match(
        self,
        message_text: str,
        user_id: str | None = None,
        is_debate_mode: bool = False,
        required_input_modes: list[str] | None = None,
    ) -> MatchResult:
        facade = self._require_facade()
        if isinstance(facade, AgentMessageMatcher):
            matches = await facade.match_for_message(
                message_text,
                requesting_user_id=user_id,
                required_input_modes=required_input_modes,
                is_debate_mode=is_debate_mode,
            )
        else:
            matches = await facade.match_agents(
                message_text,
                requesting_user_id=user_id,
            )
        converted: list[MatchedAgent] = []
        for match in matches:
            matched_agent = _to_matched_agent(match)
            if matched_agent is not None:
                converted.append(matched_agent)
        return MatchResult(
            agents=converted,
            total_candidates=len(converted),
            filtered_count=len(converted),
        )


def _to_matched_agent(match) -> MatchedAgent | None:
    if isinstance(match, dict):
        agent_info = match.get("agent")
        if agent_info is None:
            return None
        agent = _agent_info_to_legacy_agent(agent_info)
        if agent is None:
            return None
        return MatchedAgent(
            agent=agent,
            lexical_score=match.get("lexical_score", match.get("score", 0.0)),
            final_score=match.get(
                "final_score",
                match.get("lexical_score", match.get("score", 0.0)),
            ),
        )

    if getattr(match, "agent", None) is None:
        return None
    agent = _agent_info_to_legacy_agent(match.agent)
    if agent is None:
        return None
    return MatchedAgent(
        agent=agent,
        lexical_score=getattr(match, "score", 0.0),
        final_score=getattr(match, "score", 0.0),
    )
