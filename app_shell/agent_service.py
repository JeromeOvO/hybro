from __future__ import annotations

from typing import Any

from a2a_adapter.translators import a2a_card_to_snapshot
from agent.url_utils import (  # noqa: F401 - legacy service compatibility re-export
    is_local_agent_url,
    normalize_agent_url,
)

# Deprecated compatibility re-export for legacy service imports.
# New code should import URL helpers from agent.url_utils directly.
from common.dto.agent import AgentCardSnapshot, AgentInfo
from common.types import AgentCard
from common.utils.logger import get_logger
from models.agent import Agent
from models.error import (
    AgentCardRequiredError,
    AgentIdRequiredError,
    AgentNotFoundError,
    IllgalParameterError,
    QueryTextRequiredError,
)
from models.request import AgentCenterRequest
from models.response import AgentCenterResponse

logger = get_logger(__name__)


class AgentService:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "AgentService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    async def get_agent_card_from_url(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        if not request.agent_url:
            raise IllgalParameterError()
        facade = self._require_facade()
        card = await facade.resolve_agent_card_from_url(request.agent_url)
        return AgentCenterResponse(
            agent_card=_card_snapshot_to_legacy_card(card) if card else None,
            success=card is not None,
            error=None if card else "Agent card could not resolve",
            status_code=200 if card else 404,
        )

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        facade = self._require_facade()
        agent_url = request.agent_url or getattr(request.agent_card, "url", None)
        if not agent_url and request.agent_card is None:
            raise AgentCardRequiredError()
        if not agent_url:
            raise IllgalParameterError()

        try:
            kwargs = {
                "preferred_subdomain": getattr(request, "preferred_subdomain", None),
            }
            if request.agent_card is not None:
                kwargs["resolved_card"] = a2a_card_to_snapshot(
                    request.agent_card,
                    agent_url,
                )
            info = await facade.register_agent(
                agent_url,
                request.provider_id,
                **kwargs,
            )
        except ValueError as exc:
            status = 400 if "already registered" in str(exc).lower() else 500
            return AgentCenterResponse(success=False, error=str(exc), status_code=status)
        except Exception as exc:
            logger.error("AgentCenter: Failed to register agent: %s", exc)
            return AgentCenterResponse(success=False, error=str(exc), status_code=500)

        return AgentCenterResponse(
            agent_id=info.agent_id,
            provider_id=info.provider_id,
            agent=_agent_info_to_legacy_agent(info),
            success=True,
            error=None,
            status_code=200,
            public_url=info.public_url,
        )

    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        if request.agent_id is None:
            raise AgentIdRequiredError()
        facade = self._require_facade()
        updates = _updates_from_request(request)
        try:
            info = await facade.update_agent(request.agent_id, updates)
        except ValueError as exc:
            return AgentCenterResponse(
                agent_id=request.agent_id,
                success=False,
                error=str(exc),
                status_code=400,
            )
        if info is None:
            return AgentCenterResponse(
                agent_id=request.agent_id,
                success=False,
                error="Agent not found",
                status_code=404,
            )
        return AgentCenterResponse(
            agent_id=info.agent_id,
            agent=_agent_info_to_legacy_agent(info),
            success=True,
            error=None,
            status_code=200,
        )

    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        if request.agent_id is None:
            raise AgentIdRequiredError()
        facade = self._require_facade()
        provider_id = request.provider_id
        if provider_id is None:
            current = await facade.get_agent(request.agent_id)
            provider_id = current.provider_id if current else None
        if provider_id is None:
            return AgentCenterResponse(
                agent_id=request.agent_id,
                success=False,
                error="Agent not found",
                status_code=404,
            )
        deleted = await facade.delete_agent(request.agent_id, provider_id)
        return AgentCenterResponse(
            agent_id=request.agent_id,
            success=deleted,
            error=None if deleted else "Agent not found",
            status_code=200 if deleted else 404,
        )

    async def query_agent_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        if request.agent_id is None:
            raise AgentIdRequiredError()
        info = await self._require_facade().get_agent(request.agent_id)
        if info is None:
            raise AgentNotFoundError()
        if not info.is_public and (
            request.user_id is None or info.provider_id != request.user_id
        ):
            return AgentCenterResponse(
                agent_id=request.agent_id,
                success=False,
                error="Agent not found",
                status_code=404,
            )
        return AgentCenterResponse(
            agent_id=info.agent_id,
            agent=_agent_info_to_legacy_agent(info),
            success=True,
            error=None,
            status_code=200,
        )

    async def get_agents_by_provider_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        if not request.provider_id:
            return AgentCenterResponse(
                success=False,
                error="provider_id is required",
                status_code=400,
            )
        infos = await self._require_facade().list_agents(request.provider_id)
        return AgentCenterResponse(
            agents=[_agent_info_to_legacy_agent(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        try:
            infos = await self._require_facade().list_visible_agents(
                user_id=request.user_id,
                active_only=False,
            )
        except Exception as exc:
            logger.error("AgentCenter: Failed to get all agents: %s", exc)
            return AgentCenterResponse(success=False, error=str(exc), status_code=500)
        return AgentCenterResponse(
            agents=[_agent_info_to_legacy_agent(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def get_all_active_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        try:
            infos = await self._require_facade().list_visible_agents(
                user_id=request.user_id,
                active_only=True,
            )
        except Exception as exc:
            logger.error("AgentCenter: Failed to get all active agents: %s", exc)
            return AgentCenterResponse(success=False, error=str(exc), status_code=500)
        return AgentCenterResponse(
            agents=[_agent_info_to_legacy_agent(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        infos = await self._require_facade().list_visible_agents(
            user_id=request.user_id,
            active_only=False,
            query=request.query,
            limit=request.limit,
        )
        return AgentCenterResponse(
            agents=[_agent_info_to_legacy_agent(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def query_similar_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        if request.query_text is None:
            raise QueryTextRequiredError()
        if request.agent_count is not None and request.agent_count <= 0:
            raise IllgalParameterError()
        count = request.agent_count if request.agent_count and request.agent_count > 0 else 5
        matches = await self._require_facade().match_agents(
            request.query_text,
            limit=count,
            respect_visibility=True,
            requesting_user_id=request.user_id,
        )
        return AgentCenterResponse(
            agents=[
                _agent_info_to_legacy_agent(match.agent)
                for match in matches
                if match.agent is not None
            ],
            success=True,
            error=None,
            status_code=200,
        )

    async def validate_agent_card(self, card_data: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        required_fields = frozenset(
            [
                "name",
                "description",
                "url",
                "version",
                "capabilities",
                "defaultInputModes",
                "defaultOutputModes",
                "skills",
            ]
        )
        for field in required_fields:
            if field not in card_data:
                errors.append(f"Required field is missing: '{field}'.")
        if "url" in card_data and not (
            card_data["url"].startswith("http://")
            or card_data["url"].startswith("https://")
        ):
            errors.append(
                "Field 'url' must be an absolute URL starting with http:// or https://."
            )
        if "capabilities" in card_data and not isinstance(
            card_data["capabilities"], dict
        ):
            errors.append("Field 'capabilities' must be an object.")
        for field in ["defaultInputModes", "defaultOutputModes"]:
            if field in card_data:
                if not isinstance(card_data[field], list):
                    errors.append(f"Field '{field}' must be an array of strings.")
                elif not all(isinstance(item, str) for item in card_data[field]):
                    errors.append(f"All items in '{field}' must be strings.")
        if "skills" in card_data:
            if not isinstance(card_data["skills"], list):
                errors.append("Field 'skills' must be an array of AgentSkill objects.")
            elif not card_data["skills"]:
                errors.append(
                    "Field 'skills' array is empty. Agent must have at least one skill if it performs actions."
                )
        return errors

    async def get_agent_url_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        if request.agent_id is None:
            raise AgentIdRequiredError()
        card = await self._require_facade().get_agent_card(request.agent_id)
        if card is None:
            return AgentCenterResponse(
                success=False,
                error="Agent not found",
                status_code=404,
            )
        return AgentCenterResponse(agent_url=card.url, success=True, error=None)

    def get_agent_root_url(self, agent_url: str) -> str:
        if "/.well-known/agent.json" in agent_url:
            return agent_url.split("/.well-known/agent.json")[0]
        if agent_url.endswith("/"):
            return agent_url[:-1]
        return agent_url

    async def get_agent_by_url(self, agent_url: str) -> Agent | None:
        if agent_url is None:
            raise IllgalParameterError("agent_url is required")
        info = await self._require_facade().get_agent_by_url(agent_url)
        return _agent_info_to_legacy_agent(info) if info else None

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        info = await self._require_facade().get_agent(agent_id)
        return _agent_info_to_legacy_agent(info) if info else None

    def _mask_sensitive_information(
        self, response: AgentCenterResponse, fields: list[str]
    ) -> AgentCenterResponse:
        data = response.model_dump()

        def remove_nested_field(obj, path_parts):
            if not path_parts:
                return
            if isinstance(obj, dict):
                if len(path_parts) == 1:
                    obj[path_parts[0]] = ""
                elif path_parts[0] in obj:
                    remove_nested_field(obj[path_parts[0]], path_parts[1:])
            elif isinstance(obj, list):
                for item in obj:
                    remove_nested_field(item, path_parts)

        for field_path in fields:
            parts = field_path.split(".")
            if len(parts) == 1:
                data.pop(parts[0], None)
            else:
                if "agents" in data:
                    remove_nested_field(data["agents"], parts)
                if "agent" in data:
                    remove_nested_field(data["agent"], parts)

        return AgentCenterResponse(**data)


def _updates_from_request(request: AgentCenterRequest) -> dict:
    if request.agent is not None:
        return {
            "agent_status": _status_to_string(request.agent.agent_status),
            "is_public": request.agent.is_public,
            "rate_limit_per_user_per_hour": request.agent.rate_limit_per_user_per_hour,
            "rate_limit_system_per_hour": request.agent.rate_limit_system_per_hour,
            "agent_card": request.agent.agent_card.model_dump(mode="json"),
        }
    if request.agent_card is not None:
        return {"agent_card": request.agent_card.model_dump(mode="json")}
    return {}


def _agent_info_to_legacy_agent(info: AgentInfo | None) -> Agent | None:
    if info is None:
        return None
    return Agent(
        agent_id=info.agent_id,
        provider_id=info.provider_id,
        agent_card=_agent_info_to_card(info),
        public_url=info.public_url,
        agent_status=info.status,
        call_count=info.call_count,
        rate_limit_per_user_per_hour=info.rate_limit_per_user_per_hour,
        rate_limit_system_per_hour=info.rate_limit_system_per_hour,
        is_public=info.is_public,
        source=info.source,
        hub_id=info.hub_id,
        is_hub_online=bool(info.is_hub_online),
    )


def _agent_info_to_card(info: AgentInfo) -> AgentCard:
    raw_card = getattr(info, "raw_card", None)
    if raw_card:
        raw = _plain_data(raw_card)
        raw.setdefault("name", info.name or "")
        raw.setdefault("description", info.description or "")
        raw.setdefault("url", info.url or "")
        raw.setdefault("version", "1.0.0")
        raw.setdefault("capabilities", {})
        raw.setdefault("defaultInputModes", ["text"])
        raw.setdefault("defaultOutputModes", ["text"])
        raw.setdefault("skills", [])
        return _legacy_card_from_raw(raw)
    return _legacy_card_from_raw(
        {
            "name": info.name or "",
            "description": info.description or "",
            "url": info.url or "",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [],
        }
    )


def _card_snapshot_to_legacy_card(card: AgentCardSnapshot | None) -> AgentCard | None:
    if card is None:
        return None
    raw = dict(card.raw_card or {})
    raw.setdefault("name", card.name or "")
    raw.setdefault("description", card.description or "")
    raw.setdefault("url", card.url)
    raw.setdefault("version", "1.0.0")
    raw.setdefault("capabilities", {})
    raw.setdefault("defaultInputModes", ["text"])
    raw.setdefault("defaultOutputModes", ["text"])
    raw.setdefault("skills", [])
    return _legacy_card_from_raw(raw)


def _legacy_card_from_raw(raw: dict) -> AgentCard:
    raw = _plain_data(raw)
    raw.setdefault("capabilities", {})
    if raw["capabilities"] is None:
        raw["capabilities"] = {}
    return AgentCard(**raw)


def _status_to_string(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value) if value is not None else "active"


def _plain_data(value):
    if isinstance(value, dict):
        return {key: _plain_data(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_data(item) for item in value]
    return value


agent_service = AgentService()
