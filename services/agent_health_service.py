import asyncio
import os

import httpx
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)
from loguru import logger

from config.settings import settings
from database.mongodb import mongodb
from models.agent import Agent, AgentStatus


class AgentHealthService:
    """
    Periodic health check service for registered agents.

    Runs background HTTP ping checks to verify agent connectivity and updates
    their status (active/inactive) based on reachability.
    """

    def __init__(
        self,
        check_interval_seconds: int = int(
            os.getenv("AGENT_HEALTH_CHECK_INTERVAL", "3600")
        ),
        timeout_seconds: float = 10.0,
        max_consecutive_failures: int = 3,
    ):
        """
        Initialize the health check service.

        Args:
            check_interval_seconds: Interval between health check cycles
            timeout_seconds: HTTP request timeout per agent
            max_consecutive_failures: Number of failures before marking inactive
        """
        self.check_interval = check_interval_seconds
        self.timeout = timeout_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self._running = False
        self._task: asyncio.Task | None = None
        # Track consecutive failures per agent
        self._failure_counts: dict[str, int] = {}

    async def check_agent_health(self, agent: Agent) -> bool:
        """
        Check if an agent is reachable by making an HTTP request to its URL.

        A2A protocol agents typically only accept POST on root URL for message
        sending, so we fall back to checking the agent card endpoint which
        accepts GET requests.

        Args:
            agent: The agent to check

        Returns:
            bool: True if agent is reachable, False otherwise
        """
        agent_url = agent.agent_card.url

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Try current A2A well-known path first
                agent_card_url = agent_url.rstrip("/") + AGENT_CARD_WELL_KNOWN_PATH
                response = await client.get(agent_card_url)

                # Fall back to previous well-known path if not found
                if response.status_code == 404:
                    agent_card_url = (
                        agent_url.rstrip("/") + PREV_AGENT_CARD_WELL_KNOWN_PATH
                    )
                    response = await client.get(agent_card_url)

                # Consider 2xx and 3xx as healthy
                is_healthy = response.status_code < 400

                if is_healthy:
                    logger.debug(
                        f"Agent {agent.agent_id} ({agent.agent_card.name}) is healthy"
                    )
                else:
                    logger.warning(
                        f"Agent {agent.agent_id} ({agent.agent_card.name}) "
                        f"returned status {response.status_code}"
                    )

                return is_healthy

        except httpx.TimeoutException:
            logger.warning(
                f"Agent {agent.agent_id} ({agent.agent_card.name}) timed out"
            )
            return False
        except httpx.RequestError as e:
            logger.warning(
                f"Agent {agent.agent_id} ({agent.agent_card.name}) unreachable: {e}"
            )
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking agent {agent.agent_id}: {e}")
            return False

    async def update_agent_status(self, agent_id: str, new_status: AgentStatus) -> bool:
        """
        Update agent status in the database.

        Args:
            agent_id: The agent ID to update
            new_status: The new status to set

        Returns:
            bool: True if update was successful
        """
        try:
            agent = await mongodb.get_agent_by_agent_id(agent_id)
            if agent and agent.agent_status != new_status:
                agent.agent_status = new_status
                success = await mongodb.update_agent_by_agent_id(agent_id, agent)
                if success:
                    logger.info(
                        f"Agent {agent_id} status updated to {new_status.value}"
                    )
                return success
            return True  # No update needed
        except Exception as e:
            logger.error(f"Failed to update agent {agent_id} status: {e}")
            return False

    async def run_health_check_cycle(self):
        """Run a single health check cycle for all active agents."""
        try:
            # Get all agents that are not deleted
            agents = await mongodb.get_agents_with_conditions(
                query={"agent_status": {"$ne": AgentStatus.deleted.value}}
            )

            logger.info(f"Running health check for {len(agents)} agents")

            for agent in agents:
                is_healthy = await self.check_agent_health(agent)
                agent_id = agent.agent_id

                if is_healthy:
                    # Reset failure count and mark as active if was inactive
                    self._failure_counts[agent_id] = 0
                    if agent.agent_status == AgentStatus.inactive:
                        await self.update_agent_status(agent_id, AgentStatus.active)
                        logger.info(
                            f"Agent {agent_id} ({agent.agent_card.name}) "
                            f"recovered - marked active"
                        )
                else:
                    # Increment failure count
                    self._failure_counts[agent_id] = (
                        self._failure_counts.get(agent_id, 0) + 1
                    )
                    failures = self._failure_counts[agent_id]

                    # Only mark inactive after consecutive failures
                    if failures >= self.max_consecutive_failures:
                        if agent.agent_status == AgentStatus.active:
                            await self.update_agent_status(
                                agent_id, AgentStatus.inactive
                            )
                            logger.warning(
                                f"Agent {agent_id} ({agent.agent_card.name}) "
                                f"marked inactive after {failures} consecutive failures"
                            )
                    else:
                        logger.debug(
                            f"Agent {agent_id} failed check "
                            f"({failures}/{self.max_consecutive_failures})"
                        )

        except Exception as e:
            logger.error(f"Health check cycle failed: {e}", exc_info=True)

    async def _health_check_loop(self):
        """Background loop that periodically runs health checks."""
        logger.info(
            f"Agent health check service started "
            f"(interval: {self.check_interval}s, timeout: {self.timeout}s, "
            f"max_failures: {self.max_consecutive_failures})"
        )

        while self._running:
            await self.run_health_check_cycle()
            await asyncio.sleep(self.check_interval)

    async def start(self):
        """Start the health check background task."""
        if not settings.agent_health_check_enabled:
            logger.info("Agent health check service is disabled")
            return

        if self._running:
            logger.warning("Health check service is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._health_check_loop())
        logger.info("Agent health check service started")

    async def stop(self):
        """Stop the health check background task."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent health check service stopped")


# Singleton instance
agent_health_service = AgentHealthService()
