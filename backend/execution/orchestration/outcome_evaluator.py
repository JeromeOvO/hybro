from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from models.orchestration import (
    AgentOutputRecord,
    DelegationOutcomeRecord,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
)

VOLATILE_KEYS = {
    "artifact_key",
    "source_agent_message_id",
    "source_agent_id",
    "message_id",
    "task_id",
    "context_id",
    "created_at",
    "updated_at",
}


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_value(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def canonical_content_fingerprint(value: Any) -> str:
    payload = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def semantic_fact_map(facts: list[dict[str, Any]]) -> dict[str, object]:
    return {
        str(fact["semantic_key"]): _stable_value(fact.get("value"))
        for fact in facts
        if isinstance(fact, dict)
        and fact.get("kind") != "agent_text"
        and fact.get("semantic_key")
    }


@dataclass(frozen=True)
class GoalFingerprints:
    goal_family_fingerprint: str
    evidence_fingerprint: str
    goal_revision_fingerprint: str
    attempt_fingerprint: str


def effective_output_key(output: DispatchExpectedOutput) -> str:
    if output.output_key is None:
        raise ValueError("expected output must have a normalized output_key")
    return output.output_key


def goal_fingerprints(
    *,
    agent_id: str,
    expected_outputs: list[DispatchExpectedOutput],
    selected_content_fingerprints: list[str],
    dependency_family_fingerprints: list[str],
    upstream_output_fingerprints: list[str],
) -> GoalFingerprints:
    contracts = sorted(
        (
            {
                "output_key": effective_output_key(output),
                "kind": output.kind,
                "artifact_name": output.artifact_name,
                "required_fields": sorted(output.required_fields),
                "description": (
                    output.description
                    if not output.artifact_name and not output.required_fields
                    else None
                ),
            }
            for output in expected_outputs
        ),
        key=canonical_content_fingerprint,
    )
    family = canonical_content_fingerprint(
        {
            "contracts": contracts,
            "dependency_families": sorted(dependency_family_fingerprints),
        }
    )
    evidence = canonical_content_fingerprint(
        {
            "selected_content": sorted(set(selected_content_fingerprints)),
            "upstream_outputs": sorted(set(upstream_output_fingerprints)),
        }
    )
    revision = canonical_content_fingerprint({"family": family, "evidence": evidence})
    attempt = canonical_content_fingerprint({"revision": revision, "agent": agent_id})
    return GoalFingerprints(
        goal_family_fingerprint=family,
        evidence_fingerprint=evidence,
        goal_revision_fingerprint=revision,
        attempt_fingerprint=attempt,
    )


def required_obligations(outputs: list[DispatchExpectedOutput]) -> set[str]:
    obligations: set[str] = set()
    for output in outputs:
        if not output.required:
            continue
        key = effective_output_key(output)
        obligations.add(f"{key}:$present")
        obligations.update(f"{key}:{path}" for path in output.required_fields)
    return obligations


def invalidate_required_evidence(
    state: OrchestrationRunState,
    *,
    goal_family_fingerprint: str,
    evidence_key: str,
    obligation_keys: list[str],
    reason: str,
    source_event_id: str,
) -> tuple[OrchestrationRunState, dict[str, object]]:
    payload = {
        "code": "required_evidence_invalidated",
        "goal_family_fingerprint": goal_family_fingerprint,
        "evidence_key": evidence_key,
        "obligation_keys": sorted(set(obligation_keys)),
        "reason": reason,
        "source_event_id": source_event_id,
    }
    updated = state.model_copy(deep=True)
    updated.decision_log.append(payload)
    return updated, payload


def _value_at_path(value: object, path: str) -> object | None:
    current = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _artifact_data(artifact: object) -> list[object]:
    if not isinstance(artifact, dict):
        return []
    return [
        part["data"]
        for part in artifact.get("parts", [])
        if isinstance(part, dict) and "data" in part
    ]


def _output_artifacts(
    state: OrchestrationRunState,
    expected_output: DispatchExpectedOutput,
    agent_output: AgentOutputRecord,
) -> list[dict[str, Any]]:
    if expected_output.kind != "artifact":
        return []
    artifact_keys = set(agent_output.artifact_keys)
    return [
        artifact
        for artifact in state.artifacts
        if isinstance(artifact, dict)
        and artifact.get("artifact_key") in artifact_keys
        and (
            not expected_output.artifact_name
            or artifact.get("name") == expected_output.artifact_name
        )
    ]


def _output_fact_map(
    state: OrchestrationRunState, agent_output: AgentOutputRecord
) -> dict[str, object]:
    return semantic_fact_map(
        [
            fact
            for fact in state.facts
            if isinstance(fact, dict)
            and fact.get("source_agent_message_id") == agent_output.agent_message_id
        ]
    )


def _satisfied_obligations(
    state: OrchestrationRunState,
    outputs: list[DispatchExpectedOutput],
    agent_output: AgentOutputRecord,
) -> set[str]:
    satisfied: set[str] = set()
    facts = _output_fact_map(state, agent_output)
    for output in outputs:
        if not output.required:
            continue
        key = effective_output_key(output)
        artifacts = _output_artifacts(state, output, agent_output)
        values = [data for artifact in artifacts for data in _artifact_data(artifact)]
        if artifacts or key in facts:
            satisfied.add(f"{key}:$present")
        for path in output.required_fields:
            candidates = [
                _value_at_path(value, path) for value in values + [facts.get(key)]
            ]
            if any(value is not None for value in candidates):
                satisfied.add(f"{key}:{path}")
    return satisfied


def _invalidated_obligations(
    state: OrchestrationRunState, goal_family_fingerprint: str
) -> set[str]:
    return {
        str(obligation)
        for entry in state.decision_log
        if entry.get("code") == "required_evidence_invalidated"
        and entry.get("goal_family_fingerprint") == goal_family_fingerprint
        for obligation in entry.get("obligation_keys", [])
    }


def _has_matching_output_evidence(
    state: OrchestrationRunState,
    outputs: list[DispatchExpectedOutput],
    agent_output: AgentOutputRecord,
) -> bool:
    facts = _output_fact_map(state, agent_output)
    return any(
        _output_artifacts(state, output, agent_output)
        if output.kind == "artifact"
        else effective_output_key(output) in facts
        for output in outputs
    )


def _selected_fingerprints(
    selected_resource_fingerprints: dict[str, object] | list[object] | None,
) -> list[str]:
    if isinstance(selected_resource_fingerprints, dict):
        values = selected_resource_fingerprints.values()
    else:
        values = selected_resource_fingerprints or []
    return sorted(
        str(value) for value in values if isinstance(value, (str, int, float))
    )


class DelegationOutcomeEvaluator:
    def evaluate(
        self,
        before_state: OrchestrationRunState,
        after_state: OrchestrationRunState,
        intent: DispatchIntent,
        output: AgentOutputRecord,
        selected_resource_fingerprints: dict[str, object] | list[object] | None,
    ) -> DelegationOutcomeRecord:
        fingerprints = goal_fingerprints(
            agent_id=intent.agent_id,
            expected_outputs=intent.expected_outputs,
            selected_content_fingerprints=_selected_fingerprints(
                selected_resource_fingerprints
            ),
            dependency_family_fingerprints=[],
            upstream_output_fingerprints=[],
        )
        obligations = required_obligations(intent.expected_outputs)
        prior_satisfied = {
            obligation
            for outcome in before_state.delegation_outcomes
            if outcome.goal_family_fingerprint == fingerprints.goal_family_fingerprint
            for obligation in outcome.newly_satisfied_required_obligations
        }
        prior_satisfied.update(
            f"{output_key}:$present"
            for outcome in before_state.delegation_outcomes
            if outcome.goal_family_fingerprint == fingerprints.goal_family_fingerprint
            for output_key in outcome.satisfied_output_keys
        )
        invalidated = _invalidated_obligations(
            after_state, fingerprints.goal_family_fingerprint
        )
        current_satisfied = _satisfied_obligations(
            after_state, intent.expected_outputs, output
        )
        retained_satisfied = prior_satisfied - invalidated
        satisfied = retained_satisfied | current_satisfied
        remaining = obligations - satisfied
        newly_satisfied = sorted(satisfied - retained_satisfied)

        before_artifact_fingerprints = {
            canonical_content_fingerprint(artifact)
            for artifact in before_state.artifacts
            if isinstance(artifact, dict)
            and artifact.get("artifact_key") in set(output.artifact_keys)
        }
        output_artifacts = [
            artifact
            for artifact in after_state.artifacts
            if isinstance(artifact, dict)
            and artifact.get("artifact_key") in set(output.artifact_keys)
        ]
        changed_artifact_keys = sorted(
            str(artifact["artifact_key"])
            for artifact in output_artifacts
            if artifact.get("artifact_key")
            and canonical_content_fingerprint(artifact)
            not in before_artifact_fingerprints
        )
        before_facts = _output_fact_map(before_state, output)
        after_facts = _output_fact_map(after_state, output)
        changed_fact_keys = sorted(
            key for key, value in after_facts.items() if before_facts.get(key) != value
        )
        open_failure_ids = sorted(
            failure.failure_id
            for failure in after_state.open_failures
            if failure.status == "open"
            and (
                failure.dispatch_intent_id == intent.dispatch_intent_id
                or failure.agent_message_id == output.agent_message_id
            )
        )
        output_keys = {
            effective_output_key(expected) for expected in intent.expected_outputs
        }
        blockers = [
            blocker
            for blocker in after_state.blockers
            if blocker.status == "open"
            and blocker.claimed_user_only
            and blocker.validation_status == "validated"
            and (
                not blocker.blocked_output_keys
                or bool(set(blocker.blocked_output_keys) & output_keys)
            )
        ]
        legacy_result_fingerprint = canonical_content_fingerprint(
            {"text": output.text or "", "status": output.status}
        )
        prior_legacy_result = any(
            not intent.expected_outputs
            and outcome.goal_family_fingerprint == fingerprints.goal_family_fingerprint
            and outcome.result_fingerprint == legacy_result_fingerprint
            for outcome in before_state.delegation_outcomes
        )
        has_matching_output_evidence = _has_matching_output_evidence(
            after_state, intent.expected_outputs, output
        )

        if output.status == "failed" or open_failure_ids:
            status = "failed"
        elif blockers:
            status = "blocked"
        elif obligations and not remaining:
            status = "fulfilled"
        elif (
            intent.expected_outputs
            and not obligations
            and has_matching_output_evidence
        ):
            status = "fulfilled"
        elif not intent.expected_outputs and not prior_legacy_result:
            status = "fulfilled"
        elif newly_satisfied:
            status = "partial"
        else:
            status = "no_progress"

        result_fingerprint = (
            legacy_result_fingerprint
            if not intent.expected_outputs
            else canonical_content_fingerprint(
                {
                    "artifacts": [_stable_value(artifact) for artifact in output_artifacts],
                    "facts": after_facts,
                }
            )
        )
        return DelegationOutcomeRecord(
            outcome_id="outcome:"
            + canonical_content_fingerprint(
                {
                    "intent": intent.dispatch_intent_id,
                    "agent_message": output.agent_message_id,
                    "attempt": fingerprints.attempt_fingerprint,
                }
            )[:20],
            dispatch_intent_id=intent.dispatch_intent_id,
            agent_id=intent.agent_id,
            goal_family_fingerprint=fingerprints.goal_family_fingerprint,
            goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
            attempt_fingerprint=fingerprints.attempt_fingerprint,
            result_fingerprint=result_fingerprint,
            status=status,
            satisfied_output_keys=sorted(
                {
                    obligation.removesuffix(":$present")
                    for obligation in satisfied
                    if obligation.endswith(":$present")
                }
            ),
            missing_output_keys=sorted(
                {
                    obligation.removesuffix(":$present")
                    for obligation in remaining
                    if obligation.endswith(":$present")
                }
            ),
            remaining_required_obligations=sorted(remaining),
            newly_satisfied_required_obligations=newly_satisfied,
            changed_artifact_keys=changed_artifact_keys,
            changed_fact_keys=changed_fact_keys,
            open_failure_ids=open_failure_ids,
            blockers=blockers,
        )
