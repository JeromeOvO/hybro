"""Compatibility adapters owned by ContextMemory."""

from context_memory.compat.context_assembly import (
    ContextAssemblyResult,
    ContextAssemblyService,
    ContextMetrics,
    TruncationReason,
    context_assembly_service,
)
from context_memory.compat.runtime import (
    ContextMemoryChatAdapter,
    ContextMemoryRoomMemoryAdapter,
    ContextMemoryRouteCenter,
)

__all__ = [
    "ContextAssemblyResult",
    "ContextAssemblyService",
    "ContextMemoryChatAdapter",
    "ContextMemoryRoomMemoryAdapter",
    "ContextMemoryRouteCenter",
    "ContextMetrics",
    "TruncationReason",
    "context_assembly_service",
]
