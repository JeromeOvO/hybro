import asyncio
from typing import List

from common.utils.logger import get_logger
from models.agent import Agent
from services.database_service import db_service

logger = get_logger(__name__)


async def reupsert_agents() -> None:
    """
    Re-upsert all agents into Pinecone with agent_id in metadata.
    Safe to run multiple times.

    How to run:
      From repo root: python scripts/reupsert_agents_pinecone.py
      Ensure env vars for Mongo/Pinecone/OpenAI are set (same as app runtime).
    """
    agents: List[Agent] = await db_service.get_all_agents()
    logger.info("Re-upserting %d agents into Pinecone", len(agents))

    for agent in agents:
        try:
            embedding = await db_service.ai_service.get_embedding(
                agent.agent_card.description
            )
            vector = {
                "id": str(agent.agent_id),
                "values": embedding,
                "metadata": {"type": "a2a_agent", "agent_id": str(agent.agent_id)},
            }
            db_service.pinecone.upsert([vector])
            logger.info("Upserted agent %s into Pinecone", agent.agent_id)
        except Exception as e:  # pragma: no cover - operational script
            logger.error("Failed to upsert agent %s: %s", agent.agent_id, e)


if __name__ == "__main__":
    asyncio.run(reupsert_agents())

