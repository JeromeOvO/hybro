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
    ("id", "id"),
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
        existing_artifact_keys = {
            artifact.get("artifact_key")
            for artifact in updated.artifacts
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
            updated.artifacts.append(artifact_record)
            existing_artifact_keys.add(artifact_key)

        existing_output = next(
            (
                output
                for output in updated.agent_outputs
                if output.agent_message_id == result.agent_message_id
            ),
            None,
        )
        if existing_output is None:
            updated.agent_outputs.append(
                AgentOutputRecord(
                    agent_message_id=result.agent_message_id,
                    agent_id=result.agent_id,
                    status=result.status,
                    text=result.text,
                    artifact_keys=result_artifact_keys,
                    error=result.error,
                )
            )
        else:
            existing_output.agent_id = result.agent_id
            existing_output.status = result.status
            existing_output.text = result.text
            existing_output.error = result.error
            for artifact_key in result_artifact_keys:
                if artifact_key not in existing_output.artifact_keys:
                    existing_output.artifact_keys.append(artifact_key)

        updated.state_version += 1
        updated.updated_at = utcnow()
        return updated
