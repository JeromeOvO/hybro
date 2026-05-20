from collections.abc import AsyncIterator
from typing import Any

from common.dto import AgentInfo, AgentTaskResult, InternalAgentMessage
from models.gateway import GatewayDiscoveryAgentResult, GatewayDiscoveryResponse
from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps
from platform_module.rate_limit import PlatformAgentRateLimiter


def _mutable_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mutable_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mutable_copy(item) for item in value]
    if isinstance(value, tuple):
        return [_mutable_copy(item) for item in value]
    return value


class GatewayPlatformError(Exception):
    def __init__(self, status_code: int, detail: dict) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail.get("message") or detail.get("error") or str(detail))


class PlatformGateway:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps
        self._agent_limiter = PlatformAgentRateLimiter(
            deps.agent_rate_limit_collection,
            clock=deps.clock,
            window_seconds=config.per_agent_rate_limit_window_seconds,
        )

    def _gateway_url_for_agent(self, agent_id: str) -> str:
        base = self._config.gateway_base_url.rstrip("/") or "/api/v1"
        return f"{base}/gateway/agents/{agent_id}/message/send"

    def mask_agent_card_dict(self, agent_card_dict: dict, agent_id: str) -> dict:
        masked = _mutable_copy(agent_card_dict)
        if "url" in masked:
            masked["url"] = self._gateway_url_for_agent(agent_id)
        interfaces = masked.get("supportedInterfaces")
        if isinstance(interfaces, list):
            for iface in interfaces:
                if isinstance(iface, dict) and "url" in iface:
                    iface["url"] = self._gateway_url_for_agent(agent_id)
        return masked

    async def get_agent_for_gateway(self, agent_id: str, user_id: str) -> AgentInfo:
        if self._deps.agent_registry is None:
            raise RuntimeError("PlatformGateway requires an agent registry")

        agent = await self._deps.agent_registry.get_agent(agent_id)
        if agent is None or agent.status != "active":
            raise GatewayPlatformError(
                404,
                {
                    "error": "agent_not_found",
                    "message": "Agent not found or inactive",
                },
            )
        if not agent.is_public and agent.provider_id != user_id:
            raise GatewayPlatformError(
                403,
                {
                    "error": "access_denied",
                    "message": "You do not have access to this agent",
                },
            )
        return agent

    async def discover_agents(
        self, query: str, limit: int | None, user_id: str
    ) -> GatewayDiscoveryResponse:
        if self._deps.agent_matcher is None:
            raise RuntimeError("PlatformGateway requires an agent matcher")

        matches = await self._deps.agent_matcher.match_agents(
            query,
            limit=limit or 5,
            respect_visibility=True,
            requesting_user_id=user_id,
        )
        results: list[GatewayDiscoveryAgentResult] = []
        for match in matches:
            agent = match.agent
            if agent is None and self._deps.agent_registry is not None:
                agent = await self._deps.agent_registry.get_agent(match.agent_id)
            if agent is None:
                continue
            card = await self._card_for_agent(agent)
            results.append(
                GatewayDiscoveryAgentResult(
                    agent_id=agent.agent_id,
                    agent_card=self.mask_agent_card_dict(card, agent.agent_id),
                    match_score=match.score,
                )
            )
        return GatewayDiscoveryResponse(query=query, agents=results, count=len(results))

    async def get_agent_card(self, agent_id: str, user_id: str) -> dict:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        card = await self._card_for_agent(agent)
        return self.mask_agent_card_dict(card, agent_id)

    async def send_message(
        self, agent_id: str, message: Any, user_id: str
    ) -> AgentTaskResult:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        await self._ensure_directly_callable(agent)
        await self._check_agent_rate_limit(agent, user_id)
        transport = self._require_transport()

        try:
            return await transport.send_message(
                agent.url or "",
                self._message_to_internal(agent_id, message),
                user_id=user_id,
            )
        except GatewayPlatformError:
            raise
        except Exception as exc:
            raise GatewayPlatformError(
                502,
                {
                    "error": "agent_error",
                    "message": f"Agent communication failed: {exc}",
                },
            ) from exc

    async def stream_message(
        self, agent_id: str, message: Any, user_id: str
    ) -> AsyncIterator[dict]:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        await self._ensure_directly_callable(agent)
        await self._check_agent_rate_limit(agent, user_id)
        transport = self._require_transport()

        try:
            async for event in transport.stream_message(
                agent.url or "",
                self._message_to_internal(agent_id, message),
                user_id=user_id,
            ):
                yield (
                    event.model_dump(mode="python")
                    if hasattr(event, "model_dump")
                    else event
                )
        except GatewayPlatformError:
            raise
        except Exception as exc:
            raise GatewayPlatformError(
                502,
                {
                    "error": "agent_error",
                    "message": f"Agent communication failed: {exc}",
                },
            ) from exc

    async def _card_for_agent(self, agent: AgentInfo) -> dict:
        if self._deps.agent_registry is not None:
            snapshot = await self._deps.agent_registry.get_agent_card(agent.agent_id)
            if snapshot is not None and snapshot.raw_card:
                return dict(snapshot.raw_card)
        if agent.raw_card:
            return dict(agent.raw_card)
        return {"name": agent.name, "url": agent.url}

    async def _ensure_directly_callable(self, agent: AgentInfo) -> None:
        directly_callable = agent.source != "hub"
        if self._deps.agent_registry is not None:
            directly_callable = await self._deps.agent_registry.is_directly_callable(
                agent.agent_id
            )
        if not directly_callable:
            raise GatewayPlatformError(
                502,
                {
                    "error": "hub_agent_not_directly_callable",
                    "message": (
                        "Hub-sourced agents cannot be called directly via the gateway. "
                        "Use the platform UI or relay API instead."
                    ),
                },
            )

    async def _check_agent_rate_limit(self, agent: AgentInfo, user_id: str) -> None:
        if (
            agent.rate_limit_per_user_per_hour is None
            and agent.rate_limit_system_per_hour is None
        ):
            return
        result = await self._agent_limiter.check_agent_limit(
            agent.agent_id,
            user_id,
            agent.rate_limit_per_user_per_hour,
            agent.rate_limit_system_per_hour,
        )
        if not result.allowed:
            raise GatewayPlatformError(
                429,
                {
                    "error": "rate_limit_exceeded",
                    "message": result.reason or "Rate limit exceeded",
                    "retry_after": result.retry_after_seconds or 60,
                },
            )

    def _require_transport(self):
        if self._deps.agent_transport is None:
            raise RuntimeError("PlatformGateway requires an agent transport")
        return self._deps.agent_transport

    @staticmethod
    def _message_to_internal(agent_id: str, message: Any) -> InternalAgentMessage:
        if isinstance(message, InternalAgentMessage):
            return message
        if isinstance(message, dict):
            parts = message.get("parts")
            if parts is None and "text" in message:
                parts = [{"text": message["text"]}]
            return InternalAgentMessage(
                agent_id=agent_id,
                role=message.get("role", "user"),
                parts=parts or [],
                metadata=message.get("metadata", {}),
            )
        return InternalAgentMessage(
            agent_id=agent_id,
            role=getattr(message, "role", "user"),
            parts=[{"value": message}],
        )


__all__ = ["GatewayPlatformError", "PlatformGateway"]
