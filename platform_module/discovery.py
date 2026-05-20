from common.dto import GatewayDiscoveryAgentResult, GatewayDiscoveryResponse
from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps


class PlatformDiscovery:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps

    async def discover_agents(
        self, query: str, limit: int | None = None
    ) -> GatewayDiscoveryResponse:
        if self._deps.discovery_provider is not None:
            result = await self._deps.discovery_provider.discover_agents(
                query=query,
                limit=limit,
            )
            return GatewayDiscoveryResponse.model_validate(result)
        if self._deps.agent_matcher is None:
            raise RuntimeError("PlatformDiscovery requires a discovery provider or matcher")

        match_query = query
        if self._deps.discovery_query_expander is not None:
            match_query = await self._deps.discovery_query_expander.expand_query_for_discovery(
                query
            )

        matches = await self._deps.agent_matcher.match_agents(
            match_query,
            limit=limit or self._config.discovery_default_limit,
            respect_visibility=False,
            requesting_user_id=None,
        )
        results: list[GatewayDiscoveryAgentResult] = []
        for match in matches:
            if match.score < self._config.discovery_confidence_threshold:
                continue
            agent = match.agent
            if agent is None and self._deps.agent_registry is not None:
                agent = await self._deps.agent_registry.get_agent(match.agent_id)
            if agent is None:
                continue
            card = agent.raw_card or {"name": agent.name, "url": agent.url}
            results.append(
                GatewayDiscoveryAgentResult(
                    agent_id=agent.agent_id,
                    agent_card=dict(card),
                    match_score=match.score,
                )
            )
        return GatewayDiscoveryResponse(query=query, agents=results, count=len(results))


__all__ = ["PlatformDiscovery"]
