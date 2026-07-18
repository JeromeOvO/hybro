from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.orchestration.agent_observation import extract_agent_observation
from execution.orchestration.failure_classifier import classify_agent_failure
from models.orchestration import (
    AgentOutputRecord,
    BlockerRecord,
    DispatchIntent,
    OpenFailureRecord,
    OrchestrationRunState,
)

logger = get_logger(__name__)


class AgentResultRead(BaseModel):
    agent_message_id: str
    agent_id: str
    status: str
    text: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    status_message: str | None = None
    interactive_state: str | None = None
    requires_auth: bool = False
    requires_policy: bool = False


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


def _input_required_failure(result: AgentResultRead) -> OpenFailureRecord:
    message = result.status_message or "Agent requested additional input."
    return OpenFailureRecord(
        failure_id=uuid4().hex,
        fingerprint=f"{result.agent_id}:{result.agent_message_id}:agent_input_required",
        source="a2a_adapter",
        agent_id=result.agent_id,
        agent_message_id=result.agent_message_id,
        error_code="agent_input_required",
        error_message=message,
        recoverable=True,
        retry_count=0,
        max_retries=2,
        status="open",
        recovery_hints=[
            "retry_with_available_resource_refs",
            "retry_after_resource_projection",
            "ask_user_if_missing",
        ],
        updated_at=utcnow(),
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
        observation_changed = self._merge_observation(
            updated,
            result,
            result_artifact_keys,
        )
        output_changed = self._merge_output(
            updated,
            result,
            result_artifact_keys,
        )
        fact_changed = self._merge_fact(updated, result)
        failure_changed = self._merge_failures(updated, result)

        if not any(
            (
                artifacts_changed,
                observation_changed,
                output_changed,
                fact_changed,
                failure_changed,
            )
        ):
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

        if _upsert_structured_blockers(state, result_artifact_keys):
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
    def _merge_observation(
        state: OrchestrationRunState,
        result: AgentResultRead,
        result_artifact_keys: list[str],
    ) -> bool:
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

        artifact_key_set = set(result_artifact_keys)
        observation_artifacts = [
            artifact
            for artifact in state.artifacts
            if isinstance(artifact, dict)
            and artifact.get("artifact_key") in artifact_key_set
        ]
        observation = extract_agent_observation(
            agent_message_id=result.agent_message_id,
            agent_id=result.agent_id,
            status=result.status,
            text=result.text,
            status_message=result.status_message,
            artifact_records=observation_artifacts,
        )
        observation_fact_ids = {fact["fact_id"] for fact in observation.facts}
        retained_facts = [
            fact
            for fact in state.facts
            if not (
                isinstance(fact, dict)
                and fact.get("source_agent_message_id") == result.agent_message_id
                and fact.get("kind")
                in {"agent_observation", "agent_text_evidence"}
                and fact.get("fact_id") not in observation_fact_ids
            )
        ]
        stale_facts_removed = retained_facts != state.facts
        if stale_facts_removed:
            state.facts = retained_facts
        facts_changed = _upsert_observation_facts(state, observation.facts)
        unknowns_changed = _upsert_unknowns(state, observation.unknowns)
        blockers_changed = _upsert_blockers(state, observation.blocker_candidates)
        return (
            stale_facts_removed
            or facts_changed
            or unknowns_changed
            or blockers_changed
        )

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
                    a2a_task_id=result.a2a_task_id,
                    a2a_context_id=result.a2a_context_id,
                    status_message=result.status_message,
                    interactive_state=result.interactive_state,
                    requires_auth=result.requires_auth,
                    requires_policy=result.requires_policy,
                )
            )
            changed = True
        else:
            preserve_sparse_replay = _is_sparse_terminal_replay(
                existing_output,
                result,
            )
            for field, value in (
                ("agent_id", result.agent_id),
                ("status", result.status),
                ("a2a_task_id", result.a2a_task_id),
                ("a2a_context_id", result.a2a_context_id),
                ("status_message", result.status_message),
                ("interactive_state", result.interactive_state),
                ("requires_auth", result.requires_auth),
                ("requires_policy", result.requires_policy),
            ):
                if getattr(existing_output, field) != value:
                    setattr(existing_output, field, value)
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
    def _merge_failures(
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> bool:
        matched_intent = next(
            (
                intent
                for intent in state.dispatch_intents
                if intent.planned_agent_message_id == result.agent_message_id
            ),
            None,
        )
        if result.status in {"failed", "error", "canceled", "rejected"}:
            failure = classify_agent_failure(
                agent_id=result.agent_id,
                agent_message_id=result.agent_message_id,
                error=result.error,
                status_message=result.status_message,
                dispatch_intent_id=(
                    matched_intent.dispatch_intent_id if matched_intent else None
                ),
            )
            if failure is not None:
                retried_failure: OpenFailureRecord | None = None
                existing_failure = next(
                    (
                        item
                        for item in state.open_failures
                        if item.fingerprint == failure.fingerprint
                        and item.status == "open"
                    ),
                    None,
                )
                if existing_failure is not None:
                    existing_failure.updated_at = utcnow()
                    return True
                else:
                    retried_failure = (
                        _matching_open_failure_for_retry_attempt(
                            state.open_failures,
                            failure=failure,
                            retry_intent=matched_intent,
                            dispatch_intents=state.dispatch_intents,
                        )
                        if matched_intent is not None
                        else None
                    )
                    if retried_failure is not None:
                        retried_failure.retry_count = min(
                            retried_failure.retry_count + 1,
                            retried_failure.max_retries,
                        )
                        retried_failure.updated_at = utcnow()
                        logger.info(
                            "orchestration_recovery_retried",
                            extra={
                                "run_id": state.run_id,
                                "failure_id": retried_failure.failure_id,
                                "dispatch_intent_id": retried_failure.dispatch_intent_id,
                                "retry_dispatch_intent_id": (
                                    matched_intent.dispatch_intent_id
                                    if matched_intent is not None
                                    else None
                                ),
                                "retry_count": retried_failure.retry_count,
                                "max_retries": retried_failure.max_retries,
                                "error_code": retried_failure.error_code,
                            },
                        )
                        if retried_failure.retry_count >= retried_failure.max_retries:
                            retried_failure.status = "abandoned"
                            retried_failure.updated_at = utcnow()
                            logger.info(
                                "orchestration_recovery_abandoned",
                                extra={
                                    "run_id": state.run_id,
                                    "failure_id": retried_failure.failure_id,
                                    "dispatch_intent_id": retried_failure.dispatch_intent_id,
                                    "retry_count": retried_failure.retry_count,
                                    "max_retries": retried_failure.max_retries,
                                    "error_code": retried_failure.error_code,
                                },
                            )
                        return True
                    else:
                        state.open_failures.append(failure)
                        return True
        elif result.status == "awaiting_input":
            failure = _input_required_failure(result)
            existing_failure = next(
                (
                    item
                    for item in state.open_failures
                    if item.fingerprint == failure.fingerprint
                    and item.status == "open"
                ),
                None,
            )
            if existing_failure is None:
                state.open_failures.append(failure)
            else:
                existing_failure.error_message = failure.error_message
                existing_failure.updated_at = utcnow()
            return True
        elif result.status == "completed":
            changed = False
            for failure in _matching_open_failures_for_completed_result(
                state.open_failures,
                result,
                matched_intent,
                state.dispatch_intents,
            ):
                failure.status = "resolved"
                failure.resolved_by_agent_message_id = result.agent_message_id
                failure.updated_at = utcnow()
                changed = True
                logger.info(
                    "orchestration_recovery_resolved",
                    extra={
                        "run_id": state.run_id,
                        "failure_id": failure.failure_id,
                        "dispatch_intent_id": failure.dispatch_intent_id,
                        "resolved_by_agent_message_id": result.agent_message_id,
                        "error_code": failure.error_code,
                    },
                )
            return changed
        return False

    @staticmethod
    def _merge_fact(
        state: OrchestrationRunState,
        result: AgentResultRead,
    ) -> bool:
        text = result.text.strip() if isinstance(result.text, str) else ""
        fact_id = f"{result.agent_message_id}:text"
        existing_fact_index = next(
            (
                index
                for index, fact in enumerate(state.facts)
                if isinstance(fact, dict) and fact.get("fact_id") == fact_id
            ),
            None,
        )
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
            if existing_fact_index is None:
                state.facts.append(fact_record)
                return True
            if state.facts[existing_fact_index] != fact_record:
                state.facts[existing_fact_index] = fact_record
                return True
        elif existing_fact_index is not None:
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


def _upsert_structured_blockers(
    state: OrchestrationRunState,
    artifact_keys: list[str],
) -> bool:
    changed = False
    artifact_key_set = set(artifact_keys)
    for artifact in state.artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_key = artifact.get("artifact_key")
        if artifact_key not in artifact_key_set:
            continue
        raw_blockers = artifact.get("blockers")
        if not isinstance(raw_blockers, list):
            continue
        for raw_blocker in raw_blockers:
            if not isinstance(raw_blocker, dict):
                continue
            blocker_payload = copy.deepcopy(raw_blocker)
            evidence_refs = list(blocker_payload.get("evidence_refs") or [])
            if artifact_key and artifact_key not in evidence_refs:
                evidence_refs.append(str(artifact_key))
            blocker_payload["evidence_refs"] = evidence_refs
            blocker_payload["claimed_user_only"] = False
            blocker_payload["validated_user_only"] = False
            blocker_payload["validation_status"] = "candidate"
            blocker_payload["status"] = blocker_payload.get("status") or "open"
            blocker = BlockerRecord.model_validate(blocker_payload)
            if _upsert_blockers(state, [blocker]):
                changed = True
    return changed


def _upsert_observation_facts(
    state: OrchestrationRunState,
    facts: list[dict[str, Any]],
) -> bool:
    changed = False
    existing_by_id = {
        fact.get("fact_id"): index
        for index, fact in enumerate(state.facts)
        if isinstance(fact, dict) and fact.get("fact_id")
    }
    for fact in facts:
        fact_id = fact.get("fact_id")
        if not isinstance(fact_id, str) or not fact_id:
            continue
        existing_index = existing_by_id.get(fact_id)
        if existing_index is None:
            state.facts.append(copy.deepcopy(fact))
            existing_by_id[fact_id] = len(state.facts) - 1
            changed = True
        elif state.facts[existing_index] != fact:
            state.facts[existing_index] = copy.deepcopy(fact)
            changed = True
    return changed


def _upsert_unknowns(
    state: OrchestrationRunState,
    unknowns: list,
) -> bool:
    changed = False
    existing_by_key = {item.key: index for index, item in enumerate(state.unknowns)}
    for unknown in unknowns:
        existing_index = existing_by_key.get(unknown.key)
        if existing_index is None:
            state.unknowns.append(unknown)
            existing_by_key[unknown.key] = len(state.unknowns) - 1
            changed = True
        elif state.unknowns[existing_index] != unknown:
            state.unknowns[existing_index] = unknown
            changed = True
    return changed


def _upsert_blockers(
    state: OrchestrationRunState,
    blockers: list[BlockerRecord],
) -> bool:
    changed = False
    existing_by_key = {item.key: index for index, item in enumerate(state.blockers)}
    for blocker in blockers:
        existing_index = existing_by_key.get(blocker.key)
        if existing_index is None:
            state.blockers.append(blocker)
            existing_by_key[blocker.key] = len(state.blockers) - 1
            changed = True
        else:
            existing = state.blockers[existing_index]
            merged_evidence_refs = sorted(
                set(existing.evidence_refs) | set(blocker.evidence_refs)
            )
            if existing.validation_status == "validated" or existing.validated_user_only:
                replacement = existing.model_copy(
                    update={
                        "evidence_refs": merged_evidence_refs,
                        "blocked_output_keys": sorted(
                            set(existing.blocked_output_keys)
                            | set(blocker.blocked_output_keys)
                        ),
                    }
                )
                if replacement != existing:
                    state.blockers[existing_index] = replacement
                    changed = True
                continue
            replacement = blocker.model_copy(
                update={"evidence_refs": merged_evidence_refs}
            )
            if replacement != existing:
                state.blockers[existing_index] = replacement
                changed = True
    return changed


def _artifact_summary(artifact: dict[str, Any]) -> str:
    for field in ("summary", "name", "title", "description"):
        value = artifact.get(field)
        if isinstance(value, str):
            if value.strip():
                return value.strip()[:240]
        elif value is not None:
            return str(value)[:240]
    return ""


def _matching_open_failures_for_completed_result(
    open_failures: list[OpenFailureRecord],
    result: AgentResultRead,
    matched_intent: DispatchIntent | None,
    dispatch_intents: list[DispatchIntent],
) -> list[OpenFailureRecord]:
    unresolved_recoverable_failures = [
        failure
        for failure in open_failures
        if failure.status in {"open", "abandoned"} and failure.recoverable
    ]
    if matched_intent is not None:
        same_intent_failures = [
            failure
            for failure in unresolved_recoverable_failures
            if failure.dispatch_intent_id == matched_intent.dispatch_intent_id
        ]
        if same_intent_failures:
            return same_intent_failures
        retried_failure = _related_open_failure_for_dispatch_intent(
            unresolved_recoverable_failures,
            retry_intent=matched_intent,
            dispatch_intents=dispatch_intents,
        )
        if retried_failure is not None:
            return [retried_failure]
    return [
        failure
        for failure in unresolved_recoverable_failures
        if failure.agent_message_id == result.agent_message_id
    ]


def _matching_open_failure_for_retry_attempt(
    open_failures: list[OpenFailureRecord],
    *,
    failure: OpenFailureRecord,
    retry_intent: DispatchIntent,
    dispatch_intents: list[DispatchIntent],
) -> OpenFailureRecord | None:
    return _related_open_failure_for_dispatch_intent(
        open_failures,
        retry_intent=retry_intent,
        dispatch_intents=dispatch_intents,
        error_code=failure.error_code,
    )


def related_open_failure_for_dispatch_intent(
    open_failures: list[OpenFailureRecord],
    *,
    retry_intent: DispatchIntent,
    dispatch_intents: list[DispatchIntent],
    statuses: set[str] | None = None,
) -> OpenFailureRecord | None:
    return _related_open_failure_for_dispatch_intent(
        open_failures,
        retry_intent=retry_intent,
        dispatch_intents=dispatch_intents,
        statuses=statuses,
    )


def _related_open_failure_for_dispatch_intent(
    open_failures: list[OpenFailureRecord],
    *,
    retry_intent: DispatchIntent,
    dispatch_intents: list[DispatchIntent],
    statuses: set[str] | None = None,
    error_code: str | None = None,
) -> OpenFailureRecord | None:
    allowed_statuses = statuses or {"open"}
    intents_by_id = {intent.dispatch_intent_id: intent for intent in dispatch_intents}
    scored: list[tuple[tuple[int, int, int, int, int], OpenFailureRecord]] = []
    for open_failure in open_failures:
        if open_failure.status not in allowed_statuses:
            continue
        if not open_failure.recoverable:
            continue
        if error_code is not None and open_failure.error_code != error_code:
            continue
        failed_intent = intents_by_id.get(open_failure.dispatch_intent_id)
        score = _retry_lineage_score(
            retry_intent=retry_intent,
            failed_intent=failed_intent,
            failure=open_failure,
        )
        if score is not None:
            scored.append((score, open_failure))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _retry_lineage_score(
    *,
    retry_intent: DispatchIntent,
    failed_intent: DispatchIntent | None,
    failure: OpenFailureRecord,
) -> tuple[int, int, int, int, int, int] | None:
    if failed_intent is None:
        return None
    if failed_intent.dispatch_intent_id == retry_intent.dispatch_intent_id:
        return None

    same_task_hash = bool(
        retry_intent.task_hash
        and failed_intent.task_hash
        and retry_intent.task_hash == failed_intent.task_hash
    )
    same_task = _normalized_task(retry_intent.task) == _normalized_task(failed_intent.task)
    shared_non_attachment_refs = (
        _intent_ref_keys(retry_intent, include_attachments=False)
        & _intent_ref_keys(failed_intent, include_attachments=False)
    )
    shared_attachment_refs = (
        _intent_ref_keys(retry_intent, include_attachments=True)
        - _intent_ref_keys(retry_intent, include_attachments=False)
    ) & (
        _intent_ref_keys(failed_intent, include_attachments=True)
        - _intent_ref_keys(failed_intent, include_attachments=False)
    )
    anchors = {
        value
        for value in (failure.agent_message_id, failed_intent.planned_agent_message_id)
        if isinstance(value, str) and value
    }
    mentions_failed_message = _intent_mentions_any_message(retry_intent, anchors)
    attachment_drop = bool(failed_intent.attachment_refs) and not bool(
        retry_intent.attachment_refs
    )
    has_shared_ref_lineage = bool(
        (shared_non_attachment_refs and (same_task or same_task_hash or attachment_drop))
        or (shared_attachment_refs and (same_task or same_task_hash))
    )
    if not (mentions_failed_message or has_shared_ref_lineage):
        return None
    return (
        1 if mentions_failed_message else 0,
        len(shared_non_attachment_refs),
        len(shared_attachment_refs),
        1 if same_task_hash else 0,
        1 if same_task else 0,
        1 if attachment_drop else 0,
    )
def _normalized_task(task: str) -> str:
    return " ".join(task.lower().split())


def _intent_ref_keys(
    intent: DispatchIntent,
    *,
    include_attachments: bool,
) -> set[tuple[str, str]]:
    refs = list(intent.context_refs) + list(intent.artifact_refs)
    if include_attachments:
        refs.extend(intent.attachment_refs)
    return {
        (ref.kind.value, ref.ref_id)
        for ref in refs
    }


def _intent_mentions_any_message(
    intent: DispatchIntent,
    message_ids: set[str],
) -> bool:
    if not message_ids:
        return False
    for ref in (
        list(intent.context_refs)
        + list(intent.artifact_refs)
        + list(intent.attachment_refs)
    ):
        ref_id = getattr(ref, "ref_id", "")
        source_agent_message_id = getattr(ref, "source_agent_message_id", None)
        if isinstance(source_agent_message_id, str) and source_agent_message_id in message_ids:
            return True
        if isinstance(ref_id, str) and any(ref_id.startswith(f"{message_id}:") for message_id in message_ids):
            return True
    return False


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
