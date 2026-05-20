from collections.abc import AsyncIterator
from typing import Any

from common.dto import (
    AgentInfo,
    AgentTaskResult,
    GatewayDiscoveryAgentResult,
    GatewayDiscoveryResponse,
    GatewayResponse,
    InternalAgentMessage,
)
from common.errors import GatewayPlatformError
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
        base = self._config.gateway_base_url.rstrip() or self._config.api_prefix
        base = base.rstrip("/") or "/api/v1"
        return f"{base}/gateway/agents/{agent_id}/message/send"

    def mask_agent_card_dict(self, agent_card_dict: dict, agent_id: str) -> dict:
        masked = _mutable_copy(agent_card_dict)
        if "url" in masked:
            masked["url"] = self._gateway_url_for_agent(agent_id)
        for field in (
            "supportedInterfaces",
            "additionalInterfaces",
            "additional_interfaces",
        ):
            interfaces = masked.get(field)
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
        if self._deps.discovery_provider is not None:
            return await self._discover_with_provider(query, limit)
        if self._deps.agent_matcher is None:
            raise RuntimeError("PlatformGateway requires an agent matcher")

        match_query = query
        if self._deps.discovery_query_expander is not None:
            match_query = await self._deps.discovery_query_expander.expand_query_for_discovery(
                query
            )

        matches = await self._deps.agent_matcher.match_agents(
            match_query,
            limit=limit or self._config.discovery_default_limit,
            respect_visibility=True,
            requesting_user_id=user_id,
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
            card = await self._card_for_agent(agent)
            results.append(
                GatewayDiscoveryAgentResult(
                    agent_id=agent.agent_id,
                    agent_card=self.mask_agent_card_dict(card, agent.agent_id),
                    match_score=match.score,
                )
            )
        return GatewayDiscoveryResponse(query=query, agents=results, count=len(results))

    async def _discover_with_provider(
        self, query: str, limit: int | None
    ) -> GatewayDiscoveryResponse:
        discovery = await self._deps.discovery_provider.discover_agents(
            query=query,
            limit=limit,
        )
        results: list[GatewayDiscoveryAgentResult] = []
        for result in getattr(discovery, "agents", []):
            card = self._read_value(result, "agent_card") or {}
            agent_id = await self._agent_id_for_discovered_card(card)
            if not agent_id:
                continue
            results.append(
                GatewayDiscoveryAgentResult(
                    agent_id=agent_id,
                    agent_card=self.mask_agent_card_dict(card, agent_id),
                    match_score=float(self._read_value(result, "match_score") or 0.0),
                )
            )
        return GatewayDiscoveryResponse(
            query=getattr(discovery, "query", query),
            agents=results,
            count=len(results),
        )

    async def _agent_id_for_discovered_card(self, card: dict) -> str | None:
        if self._deps.agent_registry is None:
            return None
        url = card.get("url")
        if not url:
            return None
        agent = await self._deps.agent_registry.get_agent_by_url(str(url))
        return agent.agent_id if agent else None

    @staticmethod
    def _read_value(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    async def get_agent_card(self, agent_id: str, user_id: str) -> GatewayResponse:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        card = await self._card_for_agent(agent)
        return GatewayResponse(
            status_code=200,
            payload=self.mask_agent_card_dict(card, agent_id),
        )

    async def send_message(
        self, agent_id: str, message: InternalAgentMessage, user_id: str
    ) -> GatewayResponse:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        await self._ensure_directly_callable(agent)
        await self._check_agent_rate_limit(agent, user_id)
        transport = self._require_transport()
        internal_message = self._message_to_internal(agent_id, message)
        card = await self._card_for_agent(agent)
        accepted_output_modes = self._accepted_output_modes(card)

        success = False
        try:
            result = await transport.send_message(
                agent.url or "",
                internal_message,
                user_id=user_id,
                accepted_output_modes=accepted_output_modes,
            )
            if getattr(result, "status", None) == "error":
                raise GatewayPlatformError(
                    502,
                    {
                        "error": "agent_error",
                        "message": (
                            "Agent communication failed: "
                            f"{getattr(result, 'error', None) or 'unknown error'}"
                        ),
                    },
                )
            response_payload = self._task_result_to_a2a_response(result)
            success = "error" not in response_payload
            await self._record_agent_request(agent, user_id)
            return GatewayResponse(
                status_code=200,
                payload=response_payload,
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
        finally:
            await self._record_agent_call(agent.agent_id, success=success)

    async def stream_message(
        self, agent_id: str, message: InternalAgentMessage, user_id: str
    ) -> AsyncIterator[GatewayResponse]:
        event_stream = await self.prepare_stream(agent_id, message, user_id)
        async for event in event_stream:
            yield event

    async def prepare_stream(
        self, agent_id: str, message: InternalAgentMessage, user_id: str
    ) -> AsyncIterator[GatewayResponse]:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        await self._ensure_directly_callable(agent)
        await self._check_agent_rate_limit(agent, user_id)
        transport = self._require_transport()
        internal_message = self._message_to_internal(agent_id, message)
        card = await self._card_for_agent(agent)
        accepted_output_modes = self._accepted_output_modes(card)

        if not self._supports_streaming(agent, card):
            async def _sync_event() -> AsyncIterator[GatewayResponse]:
                success = False
                try:
                    result = await transport.send_message(
                        agent.url or "",
                        internal_message,
                        user_id=user_id,
                        accepted_output_modes=accepted_output_modes,
                    )
                    if getattr(result, "status", None) == "error":
                        raise GatewayPlatformError(
                            502,
                            {
                                "error": "agent_error",
                                "message": (
                                    "Agent communication failed: "
                                    f"{getattr(result, 'error', None) or 'unknown error'}"
                                ),
                            },
                    )
                    response_payload = self._task_result_to_a2a_response(result)
                    success = "error" not in response_payload
                    await self._record_agent_request(agent, user_id)
                    yield GatewayResponse(
                        status_code=200,
                        payload=response_payload,
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
                finally:
                    await self._record_agent_call(agent.agent_id, success=success)

            return _sync_event()

        async def _events() -> AsyncIterator[GatewayResponse]:
            success = False
            saw_error = False
            try:
                async for event in transport.stream_message(
                    agent.url or "",
                    internal_message,
                    user_id=user_id,
                    accepted_output_modes=accepted_output_modes,
                ):
                    payload = self._stream_event_to_a2a_response(event)
                    if "error" in payload:
                        saw_error = True
                    else:
                        success = True
                    yield GatewayResponse(
                        status_code=200,
                        payload=payload,
                    )
                await self._record_agent_request(agent, user_id)
                if saw_error:
                    success = False
                elif not success:
                    success = True
            except GatewayPlatformError:
                raise
            except Exception as exc:
                success = False
                raise GatewayPlatformError(
                    502,
                    {
                        "error": "agent_error",
                        "message": f"Agent communication failed: {exc}",
                    },
                ) from exc
            finally:
                await self._record_agent_call(agent.agent_id, success=success)

        return _events()

    @staticmethod
    def _task_result_to_a2a_response(result: AgentTaskResult) -> dict:
        payload = result.result if isinstance(result.result, dict) else {}
        raw = payload.get("raw")
        response_id = result.task_id or ""
        if isinstance(raw, dict):
            jsonrpc_response = _jsonrpc_response_from_raw(raw)
            if jsonrpc_response is not None:
                return jsonrpc_response
            envelope_id, envelope_result = _jsonrpc_envelope_parts(raw)
            response_result = envelope_result or raw
            response_id = str(
                envelope_id
                or response_result.get("id")
                or response_result.get("taskId")
                or response_result.get("task_id")
                or response_id
            )
        else:
            response_result = payload or {
                "id": result.task_id,
                "status": {"state": result.status},
            }
        return {"jsonrpc": "2.0", "id": response_id, "result": response_result}

    @staticmethod
    def _stream_event_to_a2a_response(event: Any) -> dict:
        if hasattr(event, "model_dump"):
            event = event.model_dump(mode="python")
        if not isinstance(event, dict):
            return {"jsonrpc": "2.0", "id": "", "result": event}
        if "jsonrpc" in event and ("result" in event or "error" in event):
            return event
        payload = event.get("payload")
        if event.get("event_type") == "error" and isinstance(payload, dict):
            error = payload.get("error")
            if error is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": str(event.get("task_id") or event.get("taskId") or ""),
                    "error": _jsonrpc_error(error),
                }
        raw = payload.get("raw") if isinstance(payload, dict) else None
        envelope_id = None
        if isinstance(raw, dict):
            jsonrpc_response = _jsonrpc_response_from_raw(raw)
            if jsonrpc_response is not None:
                return jsonrpc_response
            if event.get("event_type") == "error" and raw.get("error") is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": str(event.get("task_id") or event.get("taskId") or ""),
                    "error": _jsonrpc_error(raw["error"]),
                }
            envelope_id, envelope_result = _jsonrpc_envelope_parts(raw)
            response_result = envelope_result or raw
        else:
            response_result = event
        response_id = str(
            envelope_id
            or response_result.get("id")
            or response_result.get("taskId")
            or event.get("task_id")
            or event.get("taskId")
            or ""
        )
        return {"jsonrpc": "2.0", "id": response_id, "result": response_result}

    @staticmethod
    def _supports_streaming(agent: AgentInfo, card: dict) -> bool:
        if "streaming" in set(agent.capabilities):
            return True
        capabilities = card.get("capabilities")
        if isinstance(capabilities, dict):
            return bool(capabilities.get("streaming") or capabilities.get("stream"))
        if isinstance(capabilities, list):
            return any(
                str(capability) in {"streaming", "stream"}
                for capability in capabilities
            )
        return False

    async def _card_for_agent(self, agent: AgentInfo) -> dict:
        if self._deps.agent_registry is not None:
            snapshot = await self._deps.agent_registry.get_agent_card(agent.agent_id)
            if snapshot is not None and snapshot.raw_card:
                return dict(snapshot.raw_card)
        if agent.raw_card:
            return dict(agent.raw_card)
        return {"name": agent.name, "url": agent.url}

    @staticmethod
    def _accepted_output_modes(card: dict) -> list[str] | None:
        modes = card.get("defaultOutputModes") or card.get("default_output_modes")
        if not isinstance(modes, list):
            return None
        return [str(mode) for mode in modes if mode]

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

    async def _record_agent_request(self, agent: AgentInfo, user_id: str) -> None:
        if (
            agent.rate_limit_per_user_per_hour is None
            and agent.rate_limit_system_per_hour is None
        ):
            return
        await self._agent_limiter.record_agent_request(agent.agent_id, user_id)

    async def _record_agent_call(self, agent_id: str, *, success: bool) -> None:
        counter = self._deps.agent_call_counter
        if counter is None:
            return
        try:
            await counter.increment_agent_call_count(agent_id, success=success)
        except Exception as exc:
            if self._deps.logger is not None:
                self._deps.logger.error(
                    "Failed to record agent call count for %s: %s",
                    agent_id,
                    exc,
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
        if hasattr(message, "model_dump"):
            payload = message.model_dump(mode="json", exclude_none=True)
            parts = payload.get("parts")
            if parts is None and "text" in payload:
                parts = [{"kind": "text", "text": payload["text"]}]
            return InternalAgentMessage(
                agent_id=agent_id,
                role=str(payload.get("role", "user")),
                parts=parts or [],
                metadata=payload.get("metadata", {}),
            )
        return InternalAgentMessage(
            agent_id=agent_id,
            role=getattr(message, "role", "user"),
            parts=[{"value": message}],
        )


def _jsonrpc_envelope_parts(value: dict[str, Any]) -> tuple[Any | None, dict[str, Any] | None]:
    if "jsonrpc" not in value:
        return None, None
    result = value.get("result")
    if not isinstance(result, dict):
        return value.get("id"), None
    return value.get("id"), result


def _jsonrpc_response_from_raw(value: dict[str, Any]) -> dict[str, Any] | None:
    if "jsonrpc" not in value:
        return None
    response = {
        "jsonrpc": value.get("jsonrpc") or "2.0",
        "id": value.get("id", ""),
    }
    if "error" in value:
        response["error"] = value["error"]
        return response
    if "result" in value:
        response["result"] = value["result"]
        return response
    return None


def _jsonrpc_error(error: Any) -> dict[str, Any]:
    if isinstance(error, dict):
        data = {key: value for key, value in error.items() if value is not None}
        message = data.get("message") or data.get("error") or data.get("detail") or "Agent error"
        code = data.get("code") if isinstance(data.get("code"), int) else -32000
        return {**data, "code": code, "message": message}
    return {"code": -32000, "message": str(error)}


__all__ = ["PlatformGateway"]
