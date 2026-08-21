"""Async authorization and Agent Card projection before a V3 Run starts."""

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
            description = (candidate.description or candidate.display_name).strip()[
                :500
            ]
            compatible_refs = [
                ref.ref_id
                for ref in resource_manifest.refs
                if _resource_is_compatible(
                    kind=ref.kind,
                    mime_type=ref.mime_type,
                    input_modes=candidate.input_modes,
                )
            ]
            definition = ToolDefinition(
                name=name,
                label=candidate.display_name.strip()[:120] or "Agent",
                description=description or "Delegate this task to the bound Agent.",
                input_schema=agent_tool_input_schema(compatible_refs),
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
                "input_modes": candidate.input_modes,
                "output_modes": candidate.output_modes,
                "direct_capabilities": candidate.direct_capabilities,
                "compatible_resource_refs": compatible_refs,
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
                requesting_subject_digest=subject_digest,
                input_modes=candidate.input_modes,
                output_modes=candidate.output_modes,
                compatible_resource_refs=compatible_refs,
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


def agent_tool_input_schema(resource_refs: list[str]) -> dict[str, object]:
    ref_items: dict[str, object] = {"type": "string"}
    if resource_refs:
        ref_items["enum"] = sorted(set(resource_refs))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["task"],
        "properties": {
            "task": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "context_refs": {
                "type": "array",
                "items": {**ref_items},
                "uniqueItems": True,
                "maxItems": 100,
            },
            "artifact_refs": {
                "type": "array",
                "items": {**ref_items},
                "uniqueItems": True,
                "maxItems": 100,
            },
            "attachment_refs": {
                "type": "array",
                "items": {**ref_items},
                "uniqueItems": True,
                "maxItems": 20,
            },
        },
    }


def _resource_is_compatible(
    *, kind: str, mime_type: str | None, input_modes: list[str]
) -> bool:
    modes = {mode.lower() for mode in input_modes}
    if kind == "context":
        return "text" in modes or "text/plain" in modes
    if "file" in modes or "*/*" in modes:
        return True
    if mime_type is None:
        return False
    normalized = mime_type.lower()
    major = normalized.split("/", 1)[0] + "/*" if "/" in normalized else normalized
    return normalized in modes or major in modes


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()
