"""Synchronous provider-neutral catalog reconstructed from a frozen snapshot."""

from __future__ import annotations

from ..models import (
    FrozenToolCatalogEntry,
    FrozenToolCatalogSnapshot,
    OrchestratorRunState,
    ResolvedTool,
    ToolDefinition,
)
from .catalog_assembler import agent_tool_input_schema
from .resources import resource_is_compatible


class FrozenToolCatalog:
    def __init__(self, snapshot: FrozenToolCatalogSnapshot) -> None:
        self.snapshot = snapshot
        self._entries = {entry.definition.name: entry for entry in snapshot.entries}

    def list_tools(self, run: OrchestratorRunState) -> list[ToolDefinition]:
        self._verify_run(run)
        return [self._definition_for_run(run, entry) for entry in self.snapshot.entries]

    def resolve(self, run: OrchestratorRunState, tool_name: str) -> ResolvedTool:
        self._verify_run(run)
        entry = self._entries.get(tool_name)
        if entry is None:
            raise KeyError(tool_name)
        return ResolvedTool(
            definition=self._definition_for_run(run, entry), binding=entry.binding
        )

    @staticmethod
    def _definition_for_run(
        run: OrchestratorRunState, entry: FrozenToolCatalogEntry
    ) -> ToolDefinition:
        manifest = run.resource_manifest
        if manifest is None:
            return entry.definition
        context_refs: list[str] = []
        artifact_refs: list[str] = []
        attachment_refs: list[str] = []
        for ref in manifest.refs:
            if not resource_is_compatible(
                kind=ref.kind,
                mime_type=ref.mime_type,
                input_modes=entry.input_modes,
            ):
                continue
            if ref.kind == "context":
                context_refs.append(ref.ref_id)
            elif ref.kind == "artifact":
                artifact_refs.append(ref.ref_id)
            elif ref.kind == "attachment":
                attachment_refs.append(ref.ref_id)
        schema = agent_tool_input_schema(context_refs, artifact_refs, attachment_refs)
        if schema == entry.definition.input_schema:
            return entry.definition
        return entry.definition.model_copy(update={"input_schema": schema})

    def _verify_run(self, run: OrchestratorRunState) -> None:
        if (
            run.tool_catalog is None
            or run.tool_catalog.catalog_id != self.snapshot.catalog_id
        ):
            raise ValueError("Run is not bound to this frozen tool catalog")
