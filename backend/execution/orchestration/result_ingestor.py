from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from common.utils.time import utcnow
from models.orchestration import AgentOutputRecord, OrchestrationRunState


class AgentResultRead(BaseModel):
    agent_message_id: str
    agent_id: str
    status: str
    text: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


_STABLE_ID_FIELDS = (
    ("artifact_id", "artifact_id"),
    ("artifactId", "artifact_id"),
    ("id", "artifact_id"),
    ("part_id", "part_id"),
    ("partId", "part_id"),
)

_PROJECTION_OWNED_ARTIFACT_FIELDS = {
    "artifact_key",
    "source_agent_message_id",
    "source_agent_id",
    "summary",
}


def canonical_artifact_key(
    agent_message_id: str,
    index: int,
    artifact: dict[str, Any],
) -> str:
    for source_field, canonical_field in _STABLE_ID_FIELDS:
        value = artifact.get(source_field)
        if value is not None and str(value):
            return f"{agent_message_id}:{canonical_field}:{value}"

    artifact_identity_payload = {
        key: value
        for key, value in artifact.items()
        if key not in _PROJECTION_OWNED_ARTIFACT_FIELDS
    }
    stable_json = json.dumps(
        artifact_identity_payload,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(stable_json.encode("utf-8")).hexdigest()[:16]
    return f"{agent_message_id}:{index}:{digest}"


class AgentResultIngestor:
    def ingest(
        self,
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> OrchestrationRunState:
        if not isinstance(result, AgentResultRead):
            result = AgentResultRead.model_validate(result)

        updated = state.model_copy(deep=True)
        result_artifact_keys, artifacts_changed = self._merge_artifacts(
            updated,
            result,
        )
        output_changed = self._merge_output(
            updated,
            result,
            result_artifact_keys,
        )
        fact_changed = self._merge_fact(updated, result)

        if not artifacts_changed and not output_changed and not fact_changed:
            return state
        updated.state_version += 1
        updated.updated_at = utcnow()
        return updated

    @staticmethod
    def _merge_artifacts(
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> tuple[list[str], bool]:
        changed = False
        existing_artifacts_by_key = {
            artifact.get("artifact_key"): artifact
            for artifact in state.artifacts
            if isinstance(artifact, dict)
        }
        existing_output = next(
            (
                output
                for output in state.agent_outputs
                if output.agent_message_id == result.agent_message_id
            ),
            None,
        )
        previous_artifact_keys = (
            set(existing_output.artifact_keys)
            if existing_output is not None
            else set()
        )
        artifact_keys_referenced_by_other_outputs = {
            artifact_key
            for output in state.agent_outputs
            if output.agent_message_id != result.agent_message_id
            for artifact_key in output.artifact_keys
        }
        preserve_sparse_replay = _is_sparse_terminal_replay(existing_output, result)
        result_artifact_keys: list[str] = []
        for index, artifact in enumerate(result.artifacts):
            artifact_payload = copy.deepcopy(artifact)
            artifact_key = canonical_artifact_key(
                result.agent_message_id,
                index,
                artifact_payload,
            )
            if artifact_key in result_artifact_keys:
                continue
            result_artifact_keys.append(artifact_key)
            existing_artifact = existing_artifacts_by_key.get(artifact_key)
            if existing_artifact is not None:
                previous_artifact = copy.deepcopy(existing_artifact)
                _replace_artifact_payload(existing_artifact, artifact_payload)
                _apply_artifact_projection(
                    existing_artifact,
                    result,
                    artifact_payload,
                    artifact_key,
                )
                if existing_artifact != previous_artifact:
                    changed = True
                continue

            artifact_record = copy.deepcopy(artifact_payload)
            artifact_record["artifact_key"] = artifact_key
            _apply_artifact_projection(
                artifact_record,
                result,
                artifact_payload,
                artifact_key,
            )
            state.artifacts.append(artifact_record)
            existing_artifacts_by_key[artifact_key] = artifact_record
            changed = True

        if not preserve_sparse_replay:
            current_artifact_keys = set(result_artifact_keys)
            retained_artifacts = [
                artifact
                for artifact in state.artifacts
                if not (
                    isinstance(artifact, dict)
                    and artifact.get("artifact_key")
                    not in artifact_keys_referenced_by_other_outputs
                    and (
                        (
                            artifact.get("source_agent_message_id")
                            == result.agent_message_id
                            and artifact.get("artifact_key")
                            not in current_artifact_keys
                        )
                        or (
                            artifact.get("artifact_key") in previous_artifact_keys
                            and artifact.get("artifact_key")
                            not in current_artifact_keys
                        )
                    )
                )
            ]
            if retained_artifacts != state.artifacts:
                state.artifacts = retained_artifacts
                changed = True

        return result_artifact_keys, changed

    @staticmethod
    def _merge_output(
        state: OrchestrationRunState,
        result: AgentResultRead,
        result_artifact_keys: list[str],
    ) -> bool:
        changed = False
        existing_output = next(
            (
                output
                for output in state.agent_outputs
                if output.agent_message_id == result.agent_message_id
            ),
            None,
        )
        if existing_output is None:
            state.agent_outputs.append(
                AgentOutputRecord(
                    agent_message_id=result.agent_message_id,
                    agent_id=result.agent_id,
                    status=result.status,
                    text=result.text,
                    artifact_keys=result_artifact_keys,
                    error=result.error,
                )
            )
            changed = True
        else:
            preserve_sparse_replay = _is_sparse_terminal_replay(
                existing_output,
                result,
            )
            if existing_output.agent_id != result.agent_id:
                existing_output.agent_id = result.agent_id
                changed = True
            if existing_output.status != result.status:
                existing_output.status = result.status
                changed = True
            if result.text is not None and existing_output.text != result.text:
                existing_output.text = result.text
                changed = True
            if result.error is not None and existing_output.error != result.error:
                existing_output.error = result.error
                changed = True
            if (
                not preserve_sparse_replay
                and existing_output.artifact_keys != result_artifact_keys
            ):
                existing_output.artifact_keys = result_artifact_keys
                changed = True

        return changed

    @staticmethod
    def _merge_fact(
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> bool:
        text = result.text.strip() if isinstance(result.text, str) else ""
        fact_id = f"{result.agent_message_id}:text"
        existing_facts_by_id = {
            fact.get("fact_id"): fact
            for fact in state.facts
            if isinstance(fact, dict)
        }
        existing_fact = existing_facts_by_id.get(fact_id)
        existing_output = next(
            (
                output
                for output in state.agent_outputs
                if output.agent_message_id == result.agent_message_id
            ),
            None,
        )
        if _is_sparse_terminal_replay(existing_output, result):
            return False
        if text and _is_fact_projectable(result):
            fact_record = {
                "fact_id": fact_id,
                "source_agent_message_id": result.agent_message_id,
                "source_agent_id": result.agent_id,
                "kind": "agent_text",
                "text": text,
            }
            if existing_fact is None:
                state.facts.append(fact_record)
                return True
            if any(existing_fact.get(key) != value for key, value in fact_record.items()):
                existing_fact.update(fact_record)
                return True
        elif existing_fact is not None:
            state.facts = [
                fact
                for fact in state.facts
                if not (
                    isinstance(fact, dict)
                    and fact.get("fact_id") == fact_id
                )
            ]
            return True
        return False


def _artifact_summary(artifact: dict[str, Any]) -> str:
    for field in ("summary", "name", "title", "description"):
        value = artifact.get(field)
        if isinstance(value, str):
            if value.strip():
                return value.strip()[:240]
        elif value is not None:
            return str(value)[:240]
    return ""


def _is_fact_projectable(result: AgentResultRead) -> bool:
    return result.status == "completed"


def _is_sparse_terminal_replay(
    existing_output: AgentOutputRecord | None,
    result: AgentResultRead,
) -> bool:
    return bool(
        existing_output is not None
        and existing_output.status == result.status
        and result.text is None
        and result.error is None
        and not result.artifacts
        and (
            existing_output.text is not None
            or existing_output.error is not None
            or bool(existing_output.artifact_keys)
        )
    )


def _apply_artifact_projection(
    artifact_record: dict[str, Any],
    result: AgentResultRead,
    artifact_payload: dict[str, Any],
    artifact_key: str,
) -> None:
    artifact_record["artifact_key"] = artifact_key
    artifact_record["source_agent_message_id"] = result.agent_message_id
    artifact_record["source_agent_id"] = result.agent_id
    artifact_record["summary"] = _artifact_summary(artifact_payload)


def _replace_artifact_payload(
    artifact_record: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> None:
    preserved_projection_fields = {
        key: artifact_record[key]
        for key in _PROJECTION_OWNED_ARTIFACT_FIELDS
        if key in artifact_record
    }
    artifact_record.clear()
    artifact_record.update(preserved_projection_fields)
    for key, value in artifact_payload.items():
        if key in _PROJECTION_OWNED_ARTIFACT_FIELDS:
            continue
        artifact_record[key] = copy.deepcopy(value)
