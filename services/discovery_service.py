"""
Discovery Service for Agent Discovery API

Provides semantic search for agents using OpenAI embeddings and Pinecone vector search.
Returns A2A-compliant AgentCards for agents that meet the confidence threshold.
"""

from common.utils.logger import get_logger
from config.settings import settings
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from models.response import DiscoveryAgentResult, DiscoveryResponse
from services.openai_service import openai_service

logger = get_logger(__name__)


class DiscoveryService:
    """
    Service for discovering agents via semantic search.
    
    Uses OpenAI embeddings to vectorize queries and Pinecone for similarity search.
    Returns agents that meet the confidence threshold with their A2A AgentCards.
    """

    def __init__(self):
        self.openai_service = openai_service
        self.pinecone = pinecone_db
        self.mongo = mongodb
    
    @property
    def threshold(self):
        """Read threshold dynamically from settings to allow runtime changes."""
        return settings.discovery_confidence_threshold
    
    @property
    def default_limit(self):
        """Read default limit dynamically from settings."""
        return settings.discovery_default_limit

    async def discover_agents(
        self,
        query: str,
        limit: int | None = None,
    ) -> DiscoveryResponse:
        """
        Discover agents based on a text query.
        
        Args:
            query: The user's search query
            limit: Maximum number of agents to return (default from settings)
            
        Returns:
            DiscoveryResponse with matching agents and their scores
            
        Raises:
            ValueError: If no agents meet the confidence threshold
        """
        # Use default limit if not specified
        if limit is None:
            limit = self.default_limit
        
        # Expand query for better semantic matching (LLM-based expansion)
        expanded_query = await self.openai_service.expand_query_for_discovery(query)
        
        logger.info(
            f"DiscoveryService: Searching with query '{query}' "
            f"(expanded: '{expanded_query[:100]}...'), limit: {limit}"
        )

        # Step 1: Generate embedding for the expanded query
        embedding = await self.openai_service.get_embedding(expanded_query)
        
        # Step 2: Query Pinecone for similar agents
        results = self.pinecone.query(vector=embedding, top_k=limit)
        
        # Extract matches from Pinecone results
        matches = getattr(results, "matches", []) if results else []
        
        if not matches:
            logger.info("DiscoveryService: No matches found in Pinecone")
            raise ValueError("No agent found matching your query with sufficient confidence")
        
        # Step 3: Filter by confidence threshold and extract IDs with scores
        agent_id_to_score = {}
        all_scores = []
        for match in matches:
            # Handle both dict-like and object-like access
            agent_id = match["id"] if isinstance(match, dict) else getattr(match, "id", None)
            score = match.get("score", 0.0) if isinstance(match, dict) else getattr(match, "score", 0.0)
            score_float = float(score) if score is not None else 0.0
            all_scores.append(score_float)
            
            if agent_id and score_float >= self.threshold:
                agent_id_to_score[agent_id] = score_float
        
        if not agent_id_to_score:
            best_score = max(all_scores) if all_scores else 0.0
            logger.warning(f"(No match: threshold: {self.threshold}, best score: {best_score})")    
            raise ValueError(f"No agent found matching your query with sufficient confidence ")
                      
        logger.info(f"DiscoveryService: Found {len(agent_id_to_score)} agents meeting threshold")
            
        # Step 4: Fetch full agent information from MongoDB
        agent_ids = list(agent_id_to_score.keys())
        db_query = {"agent_id": {"$in": agent_ids}}
        agents = await self.mongo.get_agents_with_conditions(db_query)
        
        # Create ID to Agent mapping
        id_to_agent = {agent.agent_id: agent for agent in agents}
        
        # Step 5: Build response with agents sorted by score (descending)
        results_list = []
        for agent_id in sorted(agent_id_to_score.keys(), key=lambda x: agent_id_to_score[x], reverse=True):
            if agent_id in id_to_agent:
                agent = id_to_agent[agent_id]
                score = agent_id_to_score[agent_id]
                
                # Serialize AgentCard to dict (A2A Protocol compliant)
                agent_card_dict = agent.agent_card.model_dump(mode="json")
                
                results_list.append(DiscoveryAgentResult(
                    agent_card=agent_card_dict,
                    match_score=score,
                ))
        
        logger.info(
            f"DiscoveryService: Returning {len(results_list)} agents for query"
        )
        
        return DiscoveryResponse(
            query=query,
            agents=results_list,
            count=len(results_list),
        )


# Singleton instance
discovery_service = DiscoveryService()

