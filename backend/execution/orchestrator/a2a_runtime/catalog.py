"""Synchronous provider-neutral catalog reconstructed from a frozen snapshot."""

from __future__ import annotations

from ..models import (
    FrozenToolCatalogSnapshot,
    OrchestratorRunState,
    ResolvedTool,
    ToolDefinition,
)


class FrozenToolCatalog:
    def __init__(self, snapshot: FrozenToolCatalogSnapshot) -> None:
        self.snapshot = snapshot
        self._entries = {entry.definition.name: entry for entry in snapshot.entries}

    def list_tools(self, run: OrchestratorRunState) -> list[ToolDefinition]:
        self._verify_run(run)
        return [entry.definition for entry in self.snapshot.entries]

    def resolve(self, run: OrchestratorRunState, tool_name: str) -> ResolvedTool:
        self._verify_run(run)
        entry = self._entries.get(tool_name)
        if entry is None:
            raise KeyError(tool_name)
        return ResolvedTool(definition=entry.definition, binding=entry.binding)

    def _verify_run(self, run: OrchestratorRunState) -> None:
        if (
            run.tool_catalog is None
            or run.tool_catalog.catalog_id != self.snapshot.catalog_id
        ):
            raise ValueError("Run is not bound to this frozen tool catalog")
