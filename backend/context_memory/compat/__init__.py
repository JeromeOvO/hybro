"""Compatibility adapters owned by ContextMemory."""

from context_memory.compat.context_assembly import (
    ContextAssemblyResult,
    ContextAssemblyService,
    ContextMetrics,
    TruncationReason,
    context_assembly_adapter,
)
from context_memory.compat.runtime import ContextMemoryRoomMemoryAdapter

__all__ = [
    "ContextAssemblyResult",
    "ContextAssemblyService",
    "ContextMemoryRoomMemoryAdapter",
    "ContextMetrics",
    "TruncationReason",
    "context_assembly_adapter",
]
