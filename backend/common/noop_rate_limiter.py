from typing import Any
from execution.ports import RateLimitPort, AgentRateLimitResultPort

class NoOpRateLimitResult(AgentRateLimitResultPort):
    allowed: bool = True
    reason: str | None = None
    user_requests_used: int = 0
    user_requests_limit: int | None = None
    system_requests_used: int = 0
    system_requests_limit: int | None = None
    retry_after_seconds: int | None = None

class NoOpAgentRateLimiter(RateLimitPort):
    async def check_rate_limit(
        self,
        agent_id: str,
        user_id: str,
        rate_limit_per_user: int | None,
        rate_limit_system: int | None,
    ) -> AgentRateLimitResultPort:
        return NoOpRateLimitResult()

    async def record_request(self, agent_id: str, user_id: str) -> None:
        pass
