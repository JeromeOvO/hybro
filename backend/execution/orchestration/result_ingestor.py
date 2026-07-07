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


def canonical_artifact_key(
    agent_message_id: str,
    index: int,
    artifact: dict[str, Any],
) -> str:
    for source_field, canonical_field in _STABLE_ID_FIELDS:
        value = artifact.get(source_field)
        if value is not None and str(value):
            return f"{agent_message_id}:{canonical_field}:{value}"

    stable_json = json.dumps(artifact, sort_keys=True, default=str)
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
        existing_artifact_keys = {
            artifact.get("artifact_key")
            for artifact in state.artifacts
            if isinstance(artifact, dict)
        }
        result_artifact_keys: list[str] = []
        for index, artifact in enumerate(result.artifacts):
            artifact_payload = copy.deepcopy(artifact)
            artifact_key = canonical_artifact_key(
                result.agent_message_id,
                index,
                artifact_payload,
            )
            if artifact_key not in result_artifact_keys:
                result_artifact_keys.append(artifact_key)
            if artifact_key in existing_artifact_keys:
                continue

            artifact_record = copy.deepcopy(artifact_payload)
            artifact_record["artifact_key"] = artifact_key
            artifact_record.setdefault("source_agent_message_id", result.agent_message_id)
            artifact_record.setdefault("source_agent_id", result.agent_id)
            if "summary" not in artifact_record:
                artifact_record["summary"] = _artifact_summary(artifact_record)
            state.artifacts.append(artifact_record)
            existing_artifact_keys.add(artifact_key)
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
            for artifact_key in result_artifact_keys:
                if artifact_key not in existing_output.artifact_keys:
                    existing_output.artifact_keys.append(artifact_key)
                    changed = True

        return changed

    @staticmethod
    def _merge_fact(
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> bool:
        text = result.text.strip() if isinstance(result.text, str) else ""
        fact_id = f"{result.agent_message_id}:text"
        existing_fact_ids = {
            fact.get("fact_id")
            for fact in state.facts
            if isinstance(fact, dict)
        }
        if text and fact_id not in existing_fact_ids:
            state.facts.append(
                {
                    "fact_id": fact_id,
                    "source_agent_message_id": result.agent_message_id,
                    "source_agent_id": result.agent_id,
                    "kind": "agent_text",
                    "text": text,
                }
            )
            return True
        return False


def _artifact_summary(artifact: dict[str, Any]) -> str:
    value = artifact.get("summary") or artifact.get("name") or artifact.get("title")
    return str(value)[:240] if value is not None else ""
