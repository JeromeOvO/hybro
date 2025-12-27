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

    Uses exponential backoff for faster detection of offline agents:
    - Normal interval: default 1 hour between full cycles
    - On failure: retry at 30s, 60s, 120s (total ~3.5 min to mark inactive)
    """

    def __init__(
        self,
        check_interval_seconds: int = int(
            os.getenv("AGENT_HEALTH_CHECK_INTERVAL", "3600")
        ),
        timeout_seconds: float = 10.0,
        max_consecutive_failures: int = 3,
        initial_retry_delay: float = 30.0,  # First retry after 30s
        max_retry_delay: float = 120.0,  # Cap retry delay at 2 min
        backoff_multiplier: float = 2.0,  # Double delay each retry
    ):
        """
        Initialize the health check service.

        Args:
            check_interval_seconds: Interval between health check cycles
            timeout_seconds: HTTP request timeout per agent
            max_consecutive_failures: Number of failures before marking inactive
            initial_retry_delay: Initial retry delay in seconds
            max_retry_delay: Maximum retry delay in seconds
            backoff_multiplier: Backoff multiplier for the retry delay
        """
        self.check_interval = check_interval_seconds
        self.timeout = timeout_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.initial_retry_delay = initial_retry_delay
        self.max_retry_delay = max_retry_delay
        self.backoff_multiplier = backoff_multiplier
        self._running = False
        self._task: asyncio.Task | None = None
        # Track consecutive failures per agent
        self._failure_counts: dict[str, int] = {}
        # Track active retry tasks per agent to avoid duplicates
        self._retry_tasks: dict[str, asyncio.Task] = {}

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

    def _get_retry_delay(self, failure_count: int) -> float:
        """Calculate retry delay with exponential backoff."""
        delay = self.initial_retry_delay * (
            self.backoff_multiplier ** (failure_count - 1)
        )
        return min(delay, self.max_retry_delay)

    async def _retry_agent_check(self, agent_id: str):
        """
        Retry checking a specific agent with exponential backoff.
        Runs until agent recovers or is marked inactive.
        """
        try:
            while self._running:
                failure_count = self._failure_counts.get(agent_id, 0)

                # Agent recovered or already marked inactive elsewhere
                if failure_count == 0 or failure_count >= self.max_consecutive_failures:
                    break

                delay = self._get_retry_delay(failure_count)
                logger.debug(
                    f"Scheduling retry for agent {agent_id} in {delay}s "
                    f"(failure {failure_count}/{self.max_consecutive_failures})"
                )
                await asyncio.sleep(delay)

                # Re-fetch agent to get current state
                agent = await mongodb.get_agent_by_agent_id(agent_id)
                if not agent or agent.agent_status == AgentStatus.deleted:
                    break

                is_healthy = await self.check_agent_health(agent)

                if is_healthy:
                    self._failure_counts[agent_id] = 0
                    if agent.agent_status == AgentStatus.inactive:
                        await self.update_agent_status(agent_id, AgentStatus.active)
                        logger.info(
                            f"Agent {agent_id} ({agent.agent_card.name}) "
                            f"recovered - marked active"
                        )
                    break
                else:
                    self._failure_counts[agent_id] = failure_count + 1
                    failures = self._failure_counts[agent_id]

                    if failures >= self.max_consecutive_failures:
                        if agent.agent_status == AgentStatus.active:
                            await self.update_agent_status(
                                agent_id, AgentStatus.inactive
                            )
                            logger.warning(
                                f"Agent {agent_id} ({agent.agent_card.name}) "
                                f"marked inactive after {failures} consecutive failures"
                            )
                        break

        except asyncio.CancelledError:
            logger.debug(f"Retry task for agent {agent_id} cancelled")
        except Exception as e:
            logger.error(f"Retry task for agent {agent_id} failed: {e}", exc_info=True)
        finally:
            # Clean up retry task reference
            self._retry_tasks.pop(agent_id, None)

    def _schedule_retry(self, agent_id: str):
        """Schedule a retry task for an agent if not already scheduled."""
        if agent_id not in self._retry_tasks or self._retry_tasks[agent_id].done():
            self._retry_tasks[agent_id] = asyncio.create_task(
                self._retry_agent_check(agent_id)
            )

    async def run_health_check_cycle(self):
        """Run a single health check cycle for all agents."""
        try:
            agents = await mongodb.get_agents_with_conditions(
                query={"agent_status": {"$ne": AgentStatus.deleted.value}}
            )

            logger.info(f"Running health check for {len(agents)} agents")

            for agent in agents:
                agent_id = agent.agent_id

                # Skip agents with active retry tasks to avoid race conditions
                if (
                    agent_id in self._retry_tasks
                    and not self._retry_tasks[agent_id].done()
                ):
                    logger.debug(f"Skipping agent {agent_id} - retry task in progress")
                    continue

                is_healthy = await self.check_agent_health(agent)

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
                        # Schedule exponential backoff retry
                        self._schedule_retry(agent_id)

        except Exception as e:
            logger.error(f"Health check cycle failed: {e}", exc_info=True)

    async def _health_check_loop(self):
        """Background loop that periodically runs health checks."""
        logger.info(
            f"Agent health check service started "
            f"(interval: {self.check_interval}s, timeout: {self.timeout}s, "
            f"max_failures: {self.max_consecutive_failures}, "
            f"initial_retry: {self.initial_retry_delay}s)"
        )

        while self._running:
            try:
                await self.run_health_check_cycle()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Health check loop failed: {e}", exc_info=True)
                raise

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

        # Cancel all retry tasks
        for task in self._retry_tasks.values():
            task.cancel()
        self._retry_tasks.clear()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent health check service stopped")


# Singleton instance
agent_health_service = AgentHealthService()
