from __future__ import annotations

from collections.abc import Mapping

from execution.orchestration.outcome_evaluator import (
    GoalFingerprints,
    goal_fingerprints,
)
from models.orchestration import PlannedDelegateTarget


def target_goal_fingerprints(
    target: PlannedDelegateTarget,
    resource_fingerprints: Mapping[str, str],
) -> GoalFingerprints:
    selected_content_fingerprints = [
        resource_fingerprints[ref.ref_id]
        for ref in (
            *target.context_refs,
            *target.artifact_refs,
            *target.attachment_refs,
        )
        if ref.ref_id in resource_fingerprints
    ]
    return goal_fingerprints(
        agent_id=target.agent_id,
        expected_outputs=list(target.expected_outputs),
        selected_content_fingerprints=selected_content_fingerprints,
        dependency_family_fingerprints=[],
        upstream_output_fingerprints=[],
    )
