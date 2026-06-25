"""Compatibility imports for the legacy app-shell context assembly service."""

from context_memory.compat.context_assembly import (
    ContextAssemblyResult,
    ContextAssemblyService,
    ContextMetrics,
    TruncationReason,
    context_assembly_service,
)

__all__ = [
    "ContextAssemblyResult",
    "ContextAssemblyService",
    "ContextMetrics",
    "TruncationReason",
    "context_assembly_service",
]
