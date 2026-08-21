"""Durable prepared-invocation reconstruction from generic Run snapshots."""

from __future__ import annotations

from ..models import ToolInvocation
from ..ports import OrchestratorRunStore
from .models import PreparedInvocationSnapshot
from .ports import AgentToolBindingStore
from .resources import freeze_call_manifest


class RunPreparedInvocationSnapshotReader:
    def __init__(
        self,
        *,
        run_store: OrchestratorRunStore,
        binding_store: AgentToolBindingStore,
    ) -> None:
        self.run_store = run_store
        self.binding_store = binding_store

    async def read_prepared(
        self, invocation: ToolInvocation
    ) -> PreparedInvocationSnapshot | None:
        run = await self.run_store.load(invocation.run_id)
        if run is None:
            return None
        binding = await self.binding_store.load(invocation.tool.binding.binding_id)
        if (
            binding is None
            or binding.run_id != run.run_id
            or binding.binding_digest != invocation.tool.binding.binding_digest
            or binding.tool_name != invocation.tool.definition.name
            or binding.room_id != run.room_id
            or binding.room_epoch != run.request.room_epoch
        ):
            return None
        manifest = freeze_call_manifest(
            arguments=invocation.arguments,
            run_manifest=run.resource_manifest,
            binding=binding,
            source_room_id=run.room_id,
            source_room_epoch=run.request.room_epoch,
        )
        return PreparedInvocationSnapshot(
            run_id=run.run_id,
            invocation_id=invocation.invocation_id,
            room_id=run.room_id,
            room_epoch=run.request.room_epoch,
            requesting_subject_id=run.request.requesting_subject_id,
            binding=binding,
            resource_manifest=manifest,
        )
