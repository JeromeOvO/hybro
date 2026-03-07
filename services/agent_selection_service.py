"""
Agent Selection Service for Auto Room Mode

This service provides smart agent routing with a two-stage process:
1. Vector search for candidate agents using Pinecone
2. LLM analysis to decide routing strategy (single/parallel/sequential)
"""

from dataclasses import dataclass
from enum import Enum

from common.utils.logger import get_logger
from models.agent import Agent, AgentStatus
from services.database_service import db_service
from services.openai_service import openai_service

logger = get_logger(__name__)


class RoutingStrategy(str, Enum):
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
    Service for intelligent agent selection in Auto mode rooms.
    
    Uses vector search to find candidate agents, then LLM to analyze
    the message and decide the optimal routing strategy.
    """

    def __init__(self):
        self.database_service = db_service
        self.openai_service = openai_service

    async def select_agents_for_message(
        self,
        message_text: str,
        top_k: int = 3,
        user_id: str | None = None,
    ) -> AgentSelectionResult:
        """
        Select agents for a message using vector search + LLM routing.
        
        Only active agents will be considered for selection.
        
        Args:
            message_text: The user's message to route
            top_k: Maximum number of candidate agents to consider
            user_id: Optional sender ID — when provided, includes the sender's
                     private agents in the candidate pool (shared eligibility predicate)
            
        Returns:
            AgentSelectionResult with strategy, selected agents, and reasoning
        """
        logger.info(
            "AgentSelectionService: Selecting agents for message (length: %d chars)",
            len(message_text)
        )

        # Step 1: Vector search for candidate agents (active only)
        candidates = await self.database_service.query_similar_agents(
            message_text, count=top_k, active_only=True, user_id=user_id,
        )

        if not candidates:
            logger.warning("AgentSelectionService: No active candidate agents found")
            return AgentSelectionResult(
                strategy=RoutingStrategy.SINGLE,
                agents=[],
                reasoning="No active matching agents found in the network",
                needs_debate=False
            )

        logger.info(
            "AgentSelectionService: Found %d active candidate agents via vector search",
            len(candidates)
        )

        # Step 2: LLM analyzes message and decides routing strategy
        routing_result = await self._analyze_routing_needs(message_text, candidates)

        return routing_result

    async def _analyze_routing_needs(
        self,
        message_text: str,
        candidates: list[Agent]
    ) -> AgentSelectionResult:
        """
        Use LLM to analyze message and decide routing strategy.
        
        Only active agents will be selected.
        
        Args:
            message_text: The user's message
            candidates: List of candidate agents from vector search
            
        Returns:
            AgentSelectionResult with strategy and selected agents
        """
        # Filter candidates to ensure only active agents are considered (safety check)
        active_candidates = [
            agent for agent in candidates 
            if agent.agent_status == AgentStatus.active
        ]
        
        if not active_candidates:
            logger.warning(
                "AgentSelectionService: No active agents in candidates after filtering"
            )
            return AgentSelectionResult(
                strategy=RoutingStrategy.SINGLE,
                agents=[],
                reasoning="No active agents available",
                needs_debate=False
            )
        
        try:
            routing_decision = await self.openai_service.analyze_message_routing(
                message_text, active_candidates
            )

            # Parse LLM response
            strategy_str = routing_decision.get("strategy", "single").lower()
            strategy = RoutingStrategy(strategy_str) if strategy_str in [s.value for s in RoutingStrategy] else RoutingStrategy.SINGLE

            selected_agent_ids = routing_decision.get("agent_ids", [])
            reasoning = routing_decision.get("reasoning", "")
            needs_debate = routing_decision.get("needs_debate", False)

            # Build agent selections from the LLM's chosen agents (only active ones)
            agent_selections = []
            agent_reasons = routing_decision.get("agent_reasons", {})
            
            for agent in active_candidates:
                if agent.agent_id in selected_agent_ids:
                    # Double-check agent is active before adding
                    if agent.agent_status != AgentStatus.active:
                        logger.warning(
                            "AgentSelectionService: Skipping inactive agent %s in selection",
                            agent.agent_id
                        )
                        continue
                    reason = agent_reasons.get(
                        agent.agent_id, 
                        f"Best match for: {agent.agent_card.description[:50]}..."
                    )
                    agent_selections.append(AgentSelection(
                        agent_id=agent.agent_id,
                        agent_name=agent.agent_card.name,
                        reason=reason
                    ))

            # If LLM didn't select any agents, fall back to first active candidate
            if not agent_selections and active_candidates:
                first_agent = active_candidates[0]
                agent_selections.append(AgentSelection(
                    agent_id=first_agent.agent_id,
                    agent_name=first_agent.agent_card.name,
                    reason="Best overall match based on capabilities"
                ))
                strategy = RoutingStrategy.SINGLE

            logger.info(
                "AgentSelectionService: Routing decision - strategy=%s, agents=%d, debate=%s",
                strategy.value,
                len(agent_selections),
                needs_debate
            )

            return AgentSelectionResult(
                strategy=strategy,
                agents=agent_selections,
                reasoning=reasoning,
                needs_debate=needs_debate
            )

        except Exception as e:
            logger.error(
                "AgentSelectionService: Failed to analyze routing, falling back to single agent: %s",
                str(e)
            )
            # Fallback: use first active candidate with single strategy
            if active_candidates:
                first_agent = active_candidates[0]
                return AgentSelectionResult(
                    strategy=RoutingStrategy.SINGLE,
                    agents=[AgentSelection(
                        agent_id=first_agent.agent_id,
                        agent_name=first_agent.agent_card.name,
                        reason="Fallback selection due to routing analysis error"
                    )],
                    reasoning=f"Fallback to best match due to error: {str(e)}",
                    needs_debate=False
                )
            return AgentSelectionResult(
                strategy=RoutingStrategy.SINGLE,
                agents=[],
                reasoning="No active agents available",
                needs_debate=False
            )

    async def suggest_agents(self, message_text: str, top_k: int = 3) -> dict:
        """
        Public API method to suggest agents for a message.
        Returns a dict suitable for API response.
        
        Args:
            message_text: The user's message
            top_k: Maximum number of agents to suggest
            
        Returns:
            Dict with routing_strategy and suggested_agents
        """
        result = await self.select_agents_for_message(message_text, top_k)
        
        return {
            "routing_strategy": result.strategy.value,
            "reasoning": result.reasoning,
            "needs_debate": result.needs_debate,
            "suggested_agents": [
                {
                    "agent_id": agent.agent_id,
                    "name": agent.agent_name,
                    "reason": agent.reason
                }
                for agent in result.agents
            ]
        }


# Singleton instance
agent_selection_service = AgentSelectionService()

