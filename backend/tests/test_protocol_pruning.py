"""C7: a Protocol exists only if it crosses a module boundary or has two
production implementations."""

from __future__ import annotations

import importlib

import pytest

ZERO_CONSUMER_PROTOCOLS = {
    "common.protocols.a2a_protocols": ("AgentTransport",),
    "common.protocols.context_memory_protocols": ("MemorySearchPort",),
    "common.protocols.dal_protocols": ("DistributedLock",),
    "common.protocols.llm_protocols": (
        "EmbeddingServiceProtocol",
        "RequiredEmbeddingServiceProtocol",
        "ModelSelectableEmbeddingServiceProtocol",
        "LLMProvider",
        "LLMStreamingProvider",
        "ModelRegistry",
    ),
    "common.protocols.platform_protocols": (
        "AttachmentCleanupPort",
        "AttachmentContentReader",
    ),
    "common.protocols.repository_protocols": (
        "HITLRepository",
        "QuoteRepository",
        "RunRepository",
        "RunEventRepository",
    ),
}


@pytest.mark.parametrize("module_name", sorted(ZERO_CONSUMER_PROTOCOLS))
def test_zero_consumer_protocols_are_gone(module_name):
    import common.protocols as package

    module = importlib.import_module(module_name)
    for name in ZERO_CONSUMER_PROTOCOLS[module_name]:
        assert not hasattr(module, name), name
        assert not hasattr(package, name), name
        assert name not in package.__all__, name


def test_live_types_named_beside_dead_protocols_stay():
    import common.protocols as package

    for name in (
        # Return types of live Protocols.
        "RoomRouteRecord",
        "SSEUserMessageRecord",
        "SSEConnectionLike",
        # Live through its EventPublisher alias.
        "DeliveryEventPublisher",
        "EventPublisher",
        # The /health route contract, consumed by main.py.
        "HealthCheck",
        # Bases of the live LLMGateway.
        "LLMEmbeddingGateway",
        "LLMStreamGateway",
    ):
        assert hasattr(package, name), name


MOVED_TO_CONSUMER = {
    # Protocol name: (old module, consumer module that now defines it)
    "IDFactory": (
        "execution.orchestrator.ports",
        "execution.orchestrator.kernel",
    ),
    "InvocationOutcomeCheckpointReader": (
        "execution.orchestrator.ports",
        "execution.orchestrator.a2a_runtime.ingress",
    ),
    "AgentToolCandidateSource": (
        "execution.orchestrator.a2a_runtime.ports",
        "execution.orchestrator.a2a_runtime.catalog_assembler",
    ),
    "AuthReferenceVerificationPort": (
        "execution.orchestrator.a2a_runtime.ports",
        "execution.orchestrator.a2a_runtime.hitl",
    ),
    "AgentMessageMatcher": ("common.protocols.agent_protocols", "agent.matcher"),
    "ProjectionPort": (
        "common.protocols.context_memory_protocols",
        "context_memory.events",
    ),
    "CompactionPort": (
        "common.protocols.context_memory_protocols",
        "jobs.compaction_sweep",
    ),
}


@pytest.mark.parametrize("name", sorted(MOVED_TO_CONSUMER))
def test_single_consumer_protocols_live_with_their_consumer(name):
    import common.protocols as package
    import execution.orchestrator as orchestrator

    old_module, consumer_module = MOVED_TO_CONSUMER[name]
    consumer = importlib.import_module(consumer_module)

    assert not hasattr(importlib.import_module(old_module), name)
    assert not hasattr(package, name)
    assert name not in orchestrator.__all__
    protocol = getattr(consumer, name)
    assert getattr(protocol, "_is_protocol", False)
    assert protocol.__module__ == consumer_module


def test_composition_root_only_protocols_are_inlined():
    import inspect

    import common.protocols as package
    import container

    for name in ("AgentManagement", "AgentRegistryWriter", "RedisStreams"):
        assert not hasattr(package, name), name
    hints = inspect.get_annotations(container.AgentDeps)
    assert "AgentManagement" not in str(hints["agent_management"])
    assert "AgentRegistryWriter" not in str(hints["agent_registry_writer"])
    redis_hints = inspect.get_annotations(container.RedisRuntimeDeps)
    assert "RedisStreamsImpl" in str(redis_hints["streams_client"])
