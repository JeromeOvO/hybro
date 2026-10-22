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
