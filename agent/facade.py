from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
from typing import Any

from common.dto import VectorRecord
from common.dto.agent import (
    AgentCardSnapshot,
    AgentInfo,
    AgentMatchResult,
    HubAgentDescriptor,
    SyncedHubAgent,
)
from common.observability import NoopTracingProvider
from common.protocols import (
    AgentCardResolver,
    AgentRepository,
    HubLivenessReader,
    LLMProvider,
    VectorDAL,
)
from agent.public_url import PublicUrlGenerator
from agent.matching import rank_agent_docs, select_top_matches
from agent.translators import (
    agent_card_from_doc,
    agent_info_from_doc,
    docs_by_vector_order,
    hub_descriptor_to_doc,
    registration_doc_from_card,
)
from agent.url_utils import is_local_agent_url, normalize_agent_url

_ALLOWED_UPDATE_KEYS = frozenset({
    "agent_status",
    "is_public",
    "rate_limit_per_user_per_hour",
    "rate_limit_system_per_hour",
    "agent_card",
})

_AGENT_CARD_NO_OVERWRITE = frozenset({"iconUrl"})


class AgentFacade:
    def __init__(
        self,
        *,
        repository: AgentRepository,
        vector: VectorDAL,
        llm_provider: LLMProvider,
        card_resolver: AgentCardResolver,
        hub_liveness: HubLivenessReader | None = None,
        agent_index: str = "a2a-agents",
        gateway_base_url: str | None = None,
        public_url_base_domain: str = "hybro.ai",
        public_url_protocol: str = "https",
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        tracer: Any | None = None,
    ) -> None:
        self._repository = repository
        self._vector = vector
        self._llm_provider = llm_provider
        self._card_resolver = card_resolver
        self._hub_liveness = hub_liveness
        self._agent_index = agent_index
        self._gateway_base_url = gateway_base_url
        self._public_url_base_domain = public_url_base_domain
        self._public_url_protocol = public_url_protocol
        self._id_factory = id_factory
        self._now = now
        self._tracer = tracer or NoopTracingProvider()

    async def get_agent(self, agent_id: str) -> AgentInfo | None:
        doc = await self._repository.get_by_id(agent_id)
        if doc is None:
            return None
        return agent_info_from_doc(self._with_hub_liveness(doc))

    async def get_agent_card(self, agent_id: str) -> AgentCardSnapshot | None:
        doc = await self._repository.get_by_id(agent_id)
        if doc is None:
            return None
        return agent_card_from_doc(doc)

    async def get_agents_by_ids(self, agent_ids: list[str]) -> list[AgentInfo]:
        docs = await self._repository.get_by_ids(agent_ids)
        docs_by_id = {doc.get("agent_id"): doc for doc in docs}
        return [
            agent_info_from_doc(self._with_hub_liveness(docs_by_id[agent_id]))
            for agent_id in agent_ids
            if agent_id in docs_by_id
        ]

    async def is_agent_healthy(self, agent_id: str) -> bool:
        doc = await self._repository.get_by_id(agent_id)
        return doc is not None and _status_value(doc.get("agent_status")) == "active"

    async def is_directly_callable(self, agent_id: str) -> bool:
        doc = await self._repository.get_by_id(agent_id)
        if doc is None or _status_value(doc.get("agent_status")) != "active":
            return False
        if doc.get("source") != "hub" and not doc.get("hub_id"):
            return True
        if self._hub_liveness is None or not doc.get("hub_id"):
            return False
        return self._hub_liveness.is_hub_online(doc["hub_id"])

    async def match_agents(
        self,
        query: str,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
    ) -> list[AgentMatchResult]:
        selected = await self._match_agent_records(
            query,
            limit=limit,
            filter_ids=filter_ids,
            respect_visibility=respect_visibility,
            requesting_user_id=requesting_user_id,
        )
        return [
            AgentMatchResult(
                agent_id=match["agent_id"],
                score=match["final_score"],
                reason=(
                    f"Match score: {match['final_score']:.2f} "
                    f"(vector: {match['vector_score']:.2f}, "
                    f"capability: {match['capability_score']:.2f})"
                ),
                agent=match["agent"],
            )
            for match in selected
        ]

    async def match_for_message(
        self,
        query: str,
        *,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        requesting_user_id: str | None = None,
        required_input_modes: list[str] | None = None,
        is_debate_mode: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._match_agent_records(
            query,
            limit=limit,
            filter_ids=filter_ids,
            respect_visibility=True,
            requesting_user_id=requesting_user_id,
            required_input_modes=required_input_modes,
            is_debate_mode=is_debate_mode,
        )

    async def register_agent(
        self,
        url: str,
        provider_id: str,
        **kwargs: Any,
    ) -> AgentInfo:
        if not url:
            raise ValueError("url is required")
        if not provider_id:
            raise ValueError("provider_id is required")

        requested_normalized = normalize_agent_url(url)
        card = await self._card_resolver.resolve_card(url)
        if card is None:
            raise ValueError("agent card could not resolve")

        normalized_url = normalize_agent_url(card.url or url) or requested_normalized
        existing = await self._repository.find_by_normalized_url(
            normalized_url,
            provider_id=None,
        )
        if existing is not None:
            raise ValueError("Agent with this URL is already registered")

        agent_id = self._id_factory()
        public_url = await self._public_url_generator().generate_public_url(
            agent_name=card.name,
            agent_id=agent_id,
            preferred_subdomain=kwargs.get("preferred_subdomain"),
        )
        doc = registration_doc_from_card(
            agent_id=agent_id,
            provider_id=provider_id,
            card=card,
            normalized_url=normalized_url,
            public_url=public_url,
            now=self._now(),
            is_public=kwargs.get("is_public", True),
            rate_limit_per_user_per_hour=kwargs.get("rate_limit_per_user_per_hour"),
            rate_limit_system_per_hour=kwargs.get("rate_limit_system_per_hour"),
        )

        self._validate_rate_limits(doc)
        await self._repository.upsert(agent_id, doc)
        try:
            await self._index_agent_description(agent_id, card.description)
        except Exception:
            await self._repository.delete(agent_id)
            raise
        return agent_info_from_doc(doc)

    async def delete_agent(self, agent_id: str, provider_id: str) -> bool:
        doc = await self._repository.get_by_id(agent_id)
        if doc is None or doc.get("provider_id") != provider_id:
            return False
        deleted = await self._repository.delete(agent_id)
        if not deleted:
            return False
        try:
            await self._vector.delete(self._agent_index, [agent_id])
        except Exception:
            return False
        return True

    async def update_agent(self, agent_id: str, updates: dict) -> AgentInfo | None:
        unknown = set(updates) - _ALLOWED_UPDATE_KEYS
        if unknown:
            raise ValueError(f"Unknown agent update keys: {sorted(unknown)}")

        current = await self._repository.get_by_id(agent_id)
        if current is None:
            return None

        update_doc = self._build_update_doc(current, updates)
        self._validate_rate_limits(update_doc)
        updated = await self._repository.update(agent_id, update_doc)
        if updated is None:
            return None

        if _description_changed(current, updated):
            await self._index_agent_description(
                agent_id,
                (updated.get("agent_card") or {}).get("description"),
            )
        return agent_info_from_doc(updated)

    async def list_agents(self, provider_id: str) -> list[AgentInfo]:
        docs = await self._repository.get_by_provider(provider_id)
        return [agent_info_from_doc(self._with_hub_liveness(doc)) for doc in docs]

    async def list_public_agents(self, limit: int = 50) -> list[AgentInfo]:
        docs = await self._repository.get_public(limit=limit)
        return [agent_info_from_doc(self._with_hub_liveness(doc)) for doc in docs]

    async def sync_hub_agents(
        self,
        hub_id: str,
        owner_user_id: str,
        agents: list[HubAgentDescriptor],
        prune_missing: bool = True,
    ) -> list[SyncedHubAgent]:
        synced: list[SyncedHubAgent] = []
        for descriptor in agents:
            card = _descriptor_card(descriptor)
            card_url = descriptor.url or card.get("url")
            card_name = descriptor.name or card.get("name")
            if not descriptor.agent_id or not card_url or not card_name:
                continue

            normalized_url = normalize_agent_url(card_url)
            if is_local_agent_url(card_url):
                normalized_url = None

            public_url = None
            existing = None
            if normalized_url is not None:
                existing = await self._repository.find_by_normalized_url(
                    normalized_url,
                    provider_id=owner_user_id,
                )

            agent_id = existing["agent_id"] if existing is not None else self._id_factory()
            if self._gateway_base_url:
                public_url = (
                    f"{self._gateway_base_url.rstrip('/')}"
                    f"/gateway/agents/{agent_id}/message/send"
                )

            doc = hub_descriptor_to_doc(
                hub_id=hub_id,
                owner_user_id=owner_user_id,
                descriptor=descriptor,
                agent_id=agent_id,
                normalized_url=normalized_url,
                public_url=public_url,
            )
            doc["agent_card"].setdefault("description", card.get("description"))

            if existing is not None:
                await self._repository.update(agent_id, doc)
            else:
                agent_id = await self._repository.upsert_hub_agent(
                    hub_id,
                    descriptor.agent_id,
                    doc,
                )

            await self._index_description_if_changed(
                agent_id,
                doc["agent_card"].get("description"),
            )
            synced.append(
                SyncedHubAgent(
                    hub_id=hub_id,
                    agent_id=agent_id,
                    status="active",
                    is_online=True,
                    descriptor=descriptor,
                )
            )

        if prune_missing and synced:
            await self._repository.prune_missing_hub_agents(
                hub_id,
                [item.agent_id for item in synced],
            )

        is_online = self._hub_liveness.is_hub_online(hub_id) if self._hub_liveness else False
        if is_online and synced:
            await self._repository.activate_agents([item.agent_id for item in synced])

        return [
            SyncedHubAgent(
                hub_id=item.hub_id,
                agent_id=item.agent_id,
                status=item.status,
                is_online=is_online,
                descriptor=item.descriptor,
            )
            for item in synced
        ]

    async def mark_hub_agents_offline(self, hub_id: str) -> None:
        await self._repository.mark_hub_agents_offline(hub_id)

    async def resolve_agent_card_from_url(
        self, url: str
    ) -> AgentCardSnapshot | None:
        return await self._card_resolver.resolve_card(url)

    async def list_visible_agents(
        self,
        *,
        user_id: str | None = None,
        active_only: bool = False,
        limit: int = 0,
    ) -> list[AgentInfo]:
        docs = await self._repository.list_visible(
            user_id=user_id,
            active_only=active_only,
            limit=limit,
        )
        return [agent_info_from_doc(self._with_hub_liveness(doc)) for doc in docs]

    async def get_agent_by_url(self, url: str) -> AgentInfo | None:
        doc = await self._repository.find_by_normalized_url(
            normalize_agent_url(url),
            provider_id=None,
        )
        return agent_info_from_doc(self._with_hub_liveness(doc)) if doc else None

    async def update_health(self, agent_id: str, healthy: bool) -> None:
        await self._repository.update_health(agent_id, healthy)

    def _with_hub_liveness(self, doc: dict) -> dict:
        if not doc.get("hub_id") or self._hub_liveness is None:
            return doc
        enriched = dict(doc)
        enriched["is_hub_online"] = self._hub_liveness.is_hub_online(doc["hub_id"])
        return enriched

    def _public_url_generator(self) -> PublicUrlGenerator:
        return PublicUrlGenerator(
            exists=self._repository.public_url_exists,
            base_domain=self._public_url_base_domain,
            protocol=self._public_url_protocol,
            id_factory=self._id_factory,
        )

    async def _index_agent_description(
        self,
        agent_id: str,
        description: str | None,
    ) -> None:
        embedding = await self._llm_provider.embed(description or "")
        await self._vector.upsert(
            self._agent_index,
            [
                VectorRecord(
                    id=agent_id,
                    vector=embedding,
                    metadata={"type": "a2a_agent", "agent_id": agent_id},
                )
            ],
        )

    async def _index_description_if_changed(
        self,
        agent_id: str,
        description: str | None,
    ) -> None:
        desc_hash = _description_hash(description)
        current_hash = await self._repository.get_indexed_description_hash(agent_id)
        if current_hash == desc_hash:
            return
        await self._index_agent_description(agent_id, description)
        await self._repository.set_indexed_description_hash(agent_id, desc_hash)

    async def _match_agent_records(
        self,
        query: str,
        *,
        limit: int,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
        required_input_modes: list[str] | None = None,
        is_debate_mode: bool = False,
    ) -> list[dict[str, Any]]:
        if not query or filter_ids == []:
            return []

        user_id = requesting_user_id if respect_visibility else None
        candidates = await self._repository.list_visible(
            user_id=user_id,
            active_only=True,
            agent_ids=filter_ids,
            limit=0,
        )
        if not candidates:
            return []

        candidate_ids = [doc["agent_id"] for doc in candidates]
        candidate_id_set = set(candidate_ids)
        embedding = await self._llm_provider.embed(query)
        results = await self._vector.search(
            self._agent_index,
            embedding,
            top_k=max(limit * 3, 15),
            filter={"agent_id": {"$in": candidate_ids}},
        )
        result_ids = [
            _vector_result_id(result)
            for result in results
            if _vector_result_id(result) in candidate_id_set
        ]
        if not result_ids:
            return []

        docs = await self._repository.get_by_ids(result_ids)
        ordered_docs = [
            doc
            for doc in docs_by_vector_order(docs, result_ids)
            if doc.get("agent_id") in candidate_id_set
            and doc.get("agent_status") == "active"
            and (
                doc.get("is_public", True)
                or (user_id is not None and doc.get("provider_id") == user_id)
            )
        ]
        vector_scores = {
            _vector_result_id(result): _vector_result_score(result)
            for result in results
        }
        ranked = rank_agent_docs(
            ordered_docs,
            vector_scores,
            required_input_modes=required_input_modes,
        )
        selected = select_top_matches(ranked, is_debate_mode=is_debate_mode)[:limit]
        for match in selected:
            match["agent"] = agent_info_from_doc(match["agent"])
        return selected

    def _build_update_doc(self, current: dict, updates: dict) -> dict:
        update_doc: dict[str, Any] = {}
        for key in (
            "agent_status",
            "is_public",
            "rate_limit_per_user_per_hour",
            "rate_limit_system_per_hour",
        ):
            if key in updates:
                update_doc[key] = updates[key]

        if "agent_card" in updates:
            current_card = dict(current.get("agent_card") or {})
            for key, value in dict(updates["agent_card"]).items():
                if key in _AGENT_CARD_NO_OVERWRITE:
                    continue
                current_card[key] = value
            update_doc["agent_card"] = current_card
        return update_doc

    @staticmethod
    def _validate_rate_limits(doc: dict) -> None:
        for key in (
            "rate_limit_per_user_per_hour",
            "rate_limit_system_per_hour",
        ):
            value = doc.get(key)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{key} must be a positive integer or None")


def _status_value(status: Any) -> str | None:
    value = getattr(status, "value", status)
    return str(value) if value is not None else None


def _description_changed(before: dict, after: dict) -> bool:
    return (before.get("agent_card") or {}).get("description") != (
        after.get("agent_card") or {}
    ).get("description")


def _vector_result_id(result: Any) -> str:
    if isinstance(result, dict):
        return result.get("id") or result.get("agent_id")
    return getattr(result, "id")


def _vector_result_score(result: Any) -> float:
    if isinstance(result, dict):
        return float(result.get("score", 0.0))
    return float(getattr(result, "score", 0.0))


def _descriptor_card(descriptor: HubAgentDescriptor) -> dict[str, Any]:
    card = dict(descriptor.raw_card or {})
    if descriptor.name is not None:
        card.setdefault("name", descriptor.name)
    if descriptor.url is not None:
        card.setdefault("url", descriptor.url)
    return card


def _description_hash(description: str | None) -> str:
    return hashlib.sha256((description or "").encode()).hexdigest()
