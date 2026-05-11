from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agent import AgentFacade, AgentMongoRepository
from common.protocols import (
    AgentCardResolver,
    AgentExclusionReader,
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
    HubLivenessReader,
    LLMProvider,
    MongoDAL,
    VectorDAL,
)
from common.utils.time import utcnow


@dataclass(frozen=True)
class AgentDeps:
    agent_registry: AgentRegistry
    agent_matcher: AgentMatcher
    agent_management: AgentManagement
    agent_registry_writer: AgentRegistryWriter


def create_agent_deps(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    card_resolver: AgentCardResolver,
    hub_liveness: HubLivenessReader | None = None,
    exclusion_reader: AgentExclusionReader | None = None,
    gateway_base_url: str | None = None,
) -> AgentDeps:
    repository = AgentMongoRepository(mongo=mongo)
    facade = AgentFacade(
        repository=repository,
        vector=vector,
        llm_provider=llm_provider,
        card_resolver=card_resolver,
        hub_liveness=hub_liveness,
        exclusion_reader=exclusion_reader,
        gateway_base_url=gateway_base_url,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return AgentDeps(
        agent_registry=facade,
        agent_matcher=facade,
        agent_management=facade,
        agent_registry_writer=facade,
    )
