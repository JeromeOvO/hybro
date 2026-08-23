"""Async authorization and Agent Card projection before a Run starts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from ..models import (
    CandidateScopeSnapshot,
    FrozenToolCatalogEntry,
    FrozenToolCatalogSnapshot,
    RunResourceManifestSnapshot,
    ToolBindingRef,
    ToolDefinition,
)
from .models import AgentToolBindingRecord
from .ports import AgentToolBindingStore, AgentToolCandidateSource, RoomEpochStore
from .resources import resource_is_compatible


@dataclass(frozen=True, slots=True)
class PreparedAgentCatalog:
    snapshot: FrozenToolCatalogSnapshot
    bindings: tuple[AgentToolBindingRecord, ...]


class AgentToolCatalogAssembler:
    def __init__(
        self,
        *,
        candidate_source: AgentToolCandidateSource,
        binding_store: AgentToolBindingStore,
        room_epoch_store: RoomEpochStore,
    ) -> None:
        self.candidate_source = candidate_source
        self.binding_store = binding_store
        self.room_epoch_store = room_epoch_store

    async def prepare(
        self,
        *,
        run_id: str,
        room_id: str,
        room_epoch: int,
        requesting_subject_id: str,
        candidate_scope: CandidateScopeSnapshot,
        resource_manifest: RunResourceManifestSnapshot,
        authorization_basis_digest: str,
        created_at: datetime,
    ) -> PreparedAgentCatalog:
        if not await self.room_epoch_store.verify_active(room_id, room_epoch):
            raise PermissionError("Room epoch is not active")
        candidates = await self.candidate_source.list_candidates(
            run_id=run_id,
            room_id=room_id,
            room_epoch=room_epoch,
            requesting_subject_id=requesting_subject_id,
            candidate_agent_ids=candidate_scope.agent_ids,
        )
        usable = [
            candidate
            for candidate in candidates
            if candidate.active and candidate.authorized and not candidate.excluded
        ]
        subject_digest = _digest(requesting_subject_id)
        authorization_kind = (
            candidate_scope.authorization_basis.kind
            if candidate_scope.authorization_basis is not None
            else None
        )
        bindings: list[AgentToolBindingRecord] = []
        entries: list[FrozenToolCatalogEntry] = []
        used_names: set[str] = set()
        for candidate in sorted(
            usable, key=lambda item: (item.agent_id, item.skill_id or "")
        ):
            name = deterministic_tool_name(candidate.agent_id, candidate.skill_id)
            if name in used_names:
                raise ValueError("deterministic Agent tool name collision")
            used_names.add(name)
            description_parts = [
                (candidate.description or candidate.display_name).strip()
            ]
            io_parts: list[str] = []
            if candidate.input_modes:
                io_parts.append("Input: " + ", ".join(candidate.input_modes))
            if candidate.output_modes:
                io_parts.append("Output: " + ", ".join(candidate.output_modes))
            if io_parts:
                description_parts.append("; ".join(io_parts))
            description = " ".join(part for part in description_parts if part)[:500]
            compatible_refs = [
                ref
                for ref in resource_manifest.refs
                if resource_is_compatible(
                    kind=ref.kind,
                    mime_type=ref.mime_type,
                    input_modes=candidate.input_modes,
                )
            ]
            context_refs = [
                ref.ref_id for ref in compatible_refs if ref.kind == "context"
            ]
            artifact_refs = [
                ref.ref_id for ref in compatible_refs if ref.kind == "artifact"
            ]
            attachment_refs = [
                ref.ref_id for ref in compatible_refs if ref.kind == "attachment"
            ]
            definition = ToolDefinition(
                name=name,
                label=candidate.display_name.strip()[:120] or "Agent",
                description=description or "Delegate this task to the bound Agent.",
                input_schema=agent_tool_input_schema(
                    context_refs, artifact_refs, attachment_refs
                ),
                execution_mode=candidate.execution_mode,
                side_effect_level="external",
            )
            digest_payload = {
                "definition": definition.model_dump(mode="json"),
                "agent_id": candidate.agent_id,
                "skill_id": candidate.skill_id,
                "card_digest": candidate.card_digest,
                "endpoint_scope_digest": candidate.endpoint_scope_digest,
                "candidate_scope_id": candidate_scope.snapshot_id,
                "candidate_scope_revision": candidate_scope.revision,
                "authorization_basis_digest": authorization_basis_digest,
                "authorization_kind": authorization_kind,
                "input_modes": candidate.input_modes,
                "output_modes": candidate.output_modes,
                "direct_capabilities": candidate.direct_capabilities,
                "compatible_resource_refs": [ref.ref_id for ref in compatible_refs],
            }
            binding_digest = _digest_json(digest_payload)
            binding_id = f"binding-{_digest_json({'run_id': run_id, 'tool': name, 'digest': binding_digest})}"
            binding = AgentToolBindingRecord(
                binding_id=binding_id,
                binding_digest=binding_digest,
                run_id=run_id,
                room_id=room_id,
                room_epoch=room_epoch,
                tool_name=name,
                definition=definition,
                agent_id=candidate.agent_id,
                skill_id=candidate.skill_id,
                card_digest=candidate.card_digest,
                endpoint_scope=candidate.endpoint_scope,
                endpoint_scope_digest=candidate.endpoint_scope_digest,
                transport_kind=candidate.transport_kind,
                direct_capabilities=candidate.direct_capabilities,
                candidate_scope_id=candidate_scope.snapshot_id,
                candidate_scope_revision=candidate_scope.revision,
                authorization_basis_digest=authorization_basis_digest,
                authorization_kind=authorization_kind,
                requesting_subject_digest=subject_digest,
                input_modes=candidate.input_modes,
                output_modes=candidate.output_modes,
                compatible_resource_refs=[ref.ref_id for ref in compatible_refs],
                created_at=created_at,
            )
            outcome = await self.binding_store.insert(binding)
            if outcome not in {"accepted", "replayed"}:
                raise RuntimeError(f"binding persistence failed: {outcome}")
            bindings.append(binding)
            entries.append(
                FrozenToolCatalogEntry(
                    definition=definition,
                    binding=ToolBindingRef(
                        binding_id=binding_id, binding_digest=binding_digest
                    ),
                    input_modes=list(candidate.input_modes),
                )
            )
        catalog_id = f"catalog-{_digest_json([entry.model_dump(mode='json') for entry in entries])}"
        return PreparedAgentCatalog(
            snapshot=FrozenToolCatalogSnapshot(
                catalog_id=catalog_id, entries=entries, created_at=created_at
            ),
            bindings=tuple(bindings),
        )


def deterministic_tool_name(agent_id: str, skill_id: str | None = None) -> str:
    agent_hash = sha256(agent_id.encode()).hexdigest()[:20]
    if skill_id:
        skill_hash = sha256(skill_id.encode()).hexdigest()[:20]
        return f"agent_{agent_hash}_{skill_hash}"
    return f"agent_{agent_hash}"


def agent_tool_input_schema(
    context_refs: list[str],
    artifact_refs: list[str] | None = None,
    attachment_refs: list[str] | None = None,
) -> dict[str, object]:
    # Each ref family is exposed only when that family has real resources.
    # A free-form ref field invites the model to invent reference ids (which
    # are then rejected by authorization); with no refs for a family the model
    # inlines facts into ``task`` instead.
    properties: dict[str, object] = {
        "task": {"type": "string", "minLength": 1, "maxLength": 20_000},
    }

    def ref_field(items: list[str], max_items: int) -> dict[str, object]:
        return {
            "type": "array",
            "items": {"type": "string", "enum": sorted(set(items))},
            "uniqueItems": True,
            "maxItems": max_items,
        }

    if context_refs:
        properties["context_refs"] = ref_field(context_refs, 100)
    if artifact_refs:
        properties["artifact_refs"] = ref_field(artifact_refs, 100)
    if attachment_refs:
        properties["attachment_refs"] = ref_field(attachment_refs, 20)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["task"],
        "properties": properties,
    }


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()
