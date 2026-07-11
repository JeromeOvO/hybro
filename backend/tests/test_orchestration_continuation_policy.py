from execution.orchestration.continuation_policy import (
    claim_continuation,
    continuation_match,
    reconcile_continuation,
)
from models.orchestration import PendingAgentContinuation, PlannedDelegateTarget


def _continuation(status="open"):
    return PendingAgentContinuation(
        continuation_id="cont-1",
        source_intent_id="intent-1",
        source_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
        attempted_resource_fingerprints=["resource-old"],
        status=status,
    )


def _target(**overrides):
    values = {
        "agent_id": "agent-1",
        "task": "continue with the new resource",
        "repair_of_intent_id": "intent-1",
    }
    values.update(overrides)
    return PlannedDelegateTarget(**values)


def test_continuation_match_requires_explicit_lineage_and_new_resource():
    match = continuation_match(
        _continuation(),
        target=_target(),
        goal_family_fingerprint="family-1",
        selected_resource_fingerprints={"resource-old", "resource-new"},
    )

    assert match.allowed is True
    assert match.new_resource_fingerprints == ("resource-new",)


def test_continuation_rejects_same_agent_different_family():
    match = continuation_match(
        _continuation(),
        target=_target(task="different goal"),
        goal_family_fingerprint="family-2",
        selected_resource_fingerprints={"resource-new"},
    )

    assert match.code == "continuation_goal_family_mismatch"


def test_continuation_rejects_without_new_resource():
    match = continuation_match(
        _continuation(),
        target=_target(task="retry unchanged"),
        goal_family_fingerprint="family-1",
        selected_resource_fingerprints={"resource-old"},
    )

    assert match.code == "continuation_resource_already_attempted"


def test_continuation_rejects_missing_explicit_lineage():
    match = continuation_match(
        _continuation(),
        target=_target(repair_of_intent_id=None),
        goal_family_fingerprint="family-1",
        selected_resource_fingerprints={"resource-new"},
    )

    assert match.code == "continuation_lineage_missing"


def test_continuation_rejects_wrong_agent():
    match = continuation_match(
        _continuation(),
        target=_target(agent_id="agent-2"),
        goal_family_fingerprint="family-1",
        selected_resource_fingerprints={"resource-new"},
    )

    assert match.code == "continuation_agent_mismatch"


def test_continuation_rejects_non_open_status():
    match = continuation_match(
        _continuation(status="resuming"),
        target=_target(),
        goal_family_fingerprint="family-1",
        selected_resource_fingerprints={"resource-new"},
    )

    assert match.code == "continuation_not_open"


def test_continuation_rejects_missing_task_or_context_ids():
    for field in ("a2a_task_id", "a2a_context_id"):
        continuation = _continuation()
        setattr(continuation, field, "")
        match = continuation_match(
            continuation,
            target=_target(),
            goal_family_fingerprint="family-1",
            selected_resource_fingerprints={"resource-new"},
        )
        assert match.code == "continuation_task_context_missing"


def test_claim_continuation_only_transitions_open_to_resuming():
    claimed = claim_continuation(_continuation(), ("resource-new",))

    assert claimed.status == "resuming"
    assert claimed.attempted_resource_fingerprints == ["resource-old", "resource-new"]
    assert claim_continuation(claimed, ("resource-other",)) is None


def test_reconcile_continuation_returns_claim_to_open_or_terminal_state():
    claimed = claim_continuation(_continuation(), ("resource-new",))

    assert reconcile_continuation(claimed, status="open").status == "open"
    assert reconcile_continuation(claimed, status="resolved").status == "resolved"
    assert reconcile_continuation(claimed, status="abandoned").status == "abandoned"
