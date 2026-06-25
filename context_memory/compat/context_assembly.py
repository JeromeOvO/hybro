"""Compatibility adapter for context-memory context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from context_memory import legacy_assembly
from context_memory.config import TokenBudgetConfig
from context_memory.translators import primitive


class TruncationReason(StrEnum):
    """Reason for context truncation."""

    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    TURN_COUNT_EXCEEDED = "turn_count_exceeded"
    CHAR_LIMIT_EXCEEDED = "char_limit_exceeded"


@dataclass
class ContextAssemblyResult:
    """Legacy app-shell result shape for context assembly."""

    context: str
    total_tokens: int
    occupancy_pct: float
    was_truncated: bool
    truncation_reason: TruncationReason | None
    turns_included: int
    turns_truncated: int
    stable_prefix_tokens: int
    dynamic_suffix_tokens: int


@dataclass
class ContextMetrics:
    """Metrics for context assembly monitoring."""

    room_id: str
    total_tokens: int
    budget_tokens: int
    occupancy_pct: float
    was_truncated: bool
    truncation_reason: str | None
    turns_included: int
    turns_truncated: int
    full_turns: int
    compact_turns: int


class ContextAssemblyService:
    """Thin compatibility shim over ``ContextMemoryFacade`` assembly methods."""

    def __init__(self):
        self._budget = TokenBudgetConfig()
        self._facade = None
        self._bound = False
        self._truncation_count = 0

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "ContextAssemblyService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    @property
    def budget(self):
        return self._budget

    @property
    def truncation_count(self) -> int:
        return self._truncation_count

    def build_supervisor_context(
        self,
        room_memory,
        current_task: str,
        agent_registry: list[dict] | None = None,
        max_turns: int = 5,
        memory_search_results: list | None = None,
    ) -> ContextAssemblyResult:
        facade = self._require_facade()
        assembled = facade.assemble_supervisor_context_from_memory(
            primitive(room_memory),
            current_task,
            agent_registry=agent_registry,
            max_turns=max_turns,
            memory_search_results=memory_search_results,
        )
        result = _legacy_context_result(assembled)
        was_truncated = legacy_assembly.record_context_metrics(
            room_id=getattr(room_memory, "room_id", "") or "",
            result=result,
            context_type="supervisor",
            budget_summary=self.get_budget_summary(),
            metadata=assembled.metadata,
        )
        if was_truncated:
            self._truncation_count += 1
        return result

    def build_agent_execution_context(
        self,
        room_memory,
        current_task: str,
        agent_name: str | None = None,
        room_awareness: str | None = None,
        quoted_text: str | None = None,
        agent_task: str | None = None,
        include_system_instruction: bool = True,
    ) -> ContextAssemblyResult:
        facade = self._require_facade()
        assembled = facade.assemble_agent_execution_context_from_memory(
            primitive(room_memory),
            current_task,
            agent_name=agent_name,
            room_awareness=room_awareness,
            quoted_text=quoted_text,
            agent_task=agent_task,
            include_system_instruction=include_system_instruction,
        )
        result = _legacy_context_result(assembled)
        was_truncated = legacy_assembly.record_context_metrics(
            room_id=getattr(room_memory, "room_id", "") or "",
            result=result,
            context_type="agent",
            budget_summary=self.get_budget_summary(),
            metadata=assembled.metadata,
        )
        if was_truncated:
            self._truncation_count += 1
        return result

    def get_budget_summary(self) -> dict[str, Any]:
        facade = self._require_facade()
        if hasattr(facade, "get_budget_summary"):
            return facade.get_budget_summary()
        return self._budget.get_budget_summary()


context_assembly_adapter = ContextAssemblyService()


def _legacy_context_result(assembled) -> ContextAssemblyResult:
    metadata = assembled.metadata
    reason = metadata.get("truncation_reason")
    return ContextAssemblyResult(
        context=metadata.get("context", ""),
        total_tokens=assembled.total_tokens,
        occupancy_pct=metadata.get("occupancy_pct", 0.0),
        was_truncated=metadata.get("was_truncated", False),
        truncation_reason=TruncationReason(reason) if reason else None,
        turns_included=metadata.get("turns_included", 0),
        turns_truncated=metadata.get("turns_truncated", 0),
        stable_prefix_tokens=metadata.get("stable_prefix_tokens", 0),
        dynamic_suffix_tokens=metadata.get("dynamic_suffix_tokens", 0),
    )
