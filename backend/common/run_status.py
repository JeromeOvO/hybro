"""Orchestrator Run status vocabulary shared across modules."""

from __future__ import annotations

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "canceled", "budget_exhausted"}
)

__all__ = ["TERMINAL_RUN_STATUSES"]
