"""
Agent Selection Service for Auto Room Mode

Thin facade over AgentMatcher. Provides backward-compatible API
for agent selection with legacy RoutingStrategy and AgentSelectionResult types.
"""

from dataclasses import dataclass
from enum import StrEnum

from agent.matcher import AgentMatcher
from agent.protocols import AgentSuggestion, AgentSuggestionResult
from common.utils.logger import get_logger

logger = get_logger(__name__)


class RoutingStrategy(StrEnum):
    """Strategy for routing messages to agents"""
    SINGLE = "single"       # Route to 1 best agent (simple questions)
    PARALLEL = "parallel"   # Route to 2-3 agents simultaneously (multi-perspective)
    SEQUENTIAL = "sequential"  # Route to agents with dependency chain (complex tasks)


@dataclass
class AgentSelection:
    """Represents a selected agent with reason for selection"""
    agent_id: str
    agent_name: str
    reason: str
    score: float = 1.0


@dataclass
class AgentSelectionResult:
    """Result of agent selection process"""
    strategy: RoutingStrategy
    agents: list[AgentSelection]
    reasoning: str
    needs_debate: bool = False


class AgentSelectionService:
    """
    Facade over AgentMatcher for backward compatibility.

    Delegates to AgentMatcher.match() and converts results to legacy
    AgentSelectionResult format.
    """

    def __init__(self, matcher=None):
        self._matcher = matcher or AgentMatcher()

    def bind_facade(self, facade) -> None:
        self._matcher.bind_facade(facade)

    async def select_agents_for_message(
        self,
        message_text: str,
        top_k: int = 10,
        user_id: str | None = None,
        required_input_modes: list[str] | None = None,
        is_debate_mode: bool = False,
    ) -> AgentSelectionResult:
        """
        Select agents for a message using deterministic matching.

        Args:
            message_text: The user's message to route
            top_k: Maximum number of agents to return (caps matcher output)
            user_id: Optional sender ID for private agent visibility
            required_input_modes: If present (non-None), message has attachments
            is_debate_mode: If True, returns 3-5 agents for debate diversity

        Returns:
            AgentSelectionResult with strategy, selected agents, and reasoning
        """
        logger.info(
            "AgentSelectionService: Selecting agents for message (length: %d chars, debate=%s)",
            len(message_text), is_debate_mode
        )

        # Delegate to AgentMatcher — let exceptions propagate so callers
        # (e.g. _resolve_explicit_target_scope) can surface a proper 500.
        match_result = await self._matcher.match(
            message_text=message_text,
            user_id=user_id,
            is_debate_mode=is_debate_mode,
            required_input_modes=required_input_modes,
        )

        # Convert MatchResult to AgentSelectionResult for backward compatibility
        if not match_result.agents:
            logger.warning("AgentSelectionService: No agents matched")
            return AgentSelectionResult(
                strategy=RoutingStrategy.SINGLE,
                agents=[],
                reasoning="No matching agents found in the network",
                needs_debate=False
            )

        # Map MatchedAgent to AgentSelection, respecting top_k cap
        agent_selections = [
            AgentSelection(
                agent_id=matched.agent.agent_id,
                agent_name=matched.agent.agent_card.name,
                reason=f"Match score: {matched.final_score:.2f} (vector: {matched.vector_score:.2f}, capability: {matched.capability_score:.2f})",
                score=matched.final_score,
            )
            for matched in match_result.agents[:top_k]
        ]

        # Backward-compat strategy: SINGLE if 1 agent, PARALLEL if >1
        strategy = RoutingStrategy.SINGLE if len(agent_selections) == 1 else RoutingStrategy.PARALLEL

        reasoning = f"Matched {len(agent_selections)} agent(s) from {match_result.total_candidates} candidates"

        logger.info(
            "AgentSelectionService: Selected %d agents with strategy=%s",
            len(agent_selections),
            strategy.value,
        )

        return AgentSelectionResult(
            strategy=strategy,
            agents=agent_selections,
            reasoning=reasoning,
            needs_debate=False,  # Backward-compat: always False
        )

    async def suggest_agents(
        self, message_text: str, top_k: int = 3
    ) -> AgentSuggestionResult:
        """
        Public API method to suggest agents for a message.
        Returns a route-facing protocol DTO.

        Args:
            message_text: The user's message
            top_k: Maximum number of agents to suggest

        Returns:
            AgentSuggestionResult with routing metadata and suggested agents
        """
        result = await self.select_agents_for_message(message_text, top_k)

        return AgentSuggestionResult(
            analysis=result.reasoning,
            metadata={
                "routing_strategy": result.strategy.value,
                "needs_debate": result.needs_debate,
                "reasoning": result.reasoning,
            },
            suggested_agents=[
                AgentSuggestion(
                    agent_id=agent.agent_id,
                    name=agent.agent_name,
                    reason=agent.reason,
                    score=agent.score,
                )
                for agent in result.agents
            ],
        )
