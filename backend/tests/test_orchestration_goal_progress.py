from execution.orchestration.goal_progress import rebuild_goal_progress
from execution.orchestration.outcome_evaluator import invalidate_required_evidence
from models.orchestration import (
    DelegationOutcomeRecord,
    GoalFamilyDispositionRecord,
    OrchestrationRunState,
)


def test_goal_progress_aggregates_by_goal_family_revision():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Broker to insurer workflow",
        candidate_agent_ids=["broker-agent", "insurer-agent"],
    )
    first_outcome = DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id="broker-agent",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        attempt_fingerprint="attempt-1",
        status="partial",
        newly_satisfied_required_obligations=["broker_submission:$present"],
        remaining_required_obligations=["broker_submission:requested_limit"],
    )
    second_outcome = first_outcome.model_copy(
        update={
            "outcome_id": "outcome-2",
            "dispatch_intent_id": "intent-2",
            "attempt_fingerprint": "attempt-2",
            "status": "blocked",
            "newly_satisfied_required_obligations": [],
            "remaining_required_obligations": ["broker_submission:requested_limit"],
        }
    )

    state.delegation_outcomes = [first_outcome, second_outcome]

    updated = rebuild_goal_progress(state)

    assert len(updated.goal_progress) == 1
    progress = updated.goal_progress[0]
    assert progress.goal_family_fingerprint == "family-1"
    assert progress.through_goal_revision_fingerprint == "revision-1"
    assert progress.status == "blocked"
    assert progress.latest_outcome_id == "outcome-2"
    assert progress.source_outcome_ids == ["outcome-1", "outcome-2"]
    assert progress.satisfied_required_obligations == ["broker_submission:$present"]
    assert progress.remaining_required_obligations == [
        "broker_submission:requested_limit"
    ]


def test_goal_progress_reopens_invalidated_required_evidence():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Broker to insurer workflow",
        candidate_agent_ids=["broker-agent"],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="broker-agent",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
                newly_satisfied_required_obligations=["quote:$present"],
                remaining_required_obligations=[],
            )
        ],
    )

    invalidated, _ = invalidate_required_evidence(
        state,
        evidence_key="quote-evidence",
        obligation_keys=["quote:$present"],
        reason="The quote evidence is no longer valid.",
        source_event_id="event-1",
    )

    updated = rebuild_goal_progress(invalidated)

    assert updated.goal_progress[0].satisfied_required_obligations == []
    assert updated.goal_progress[0].remaining_required_obligations == ["quote:$present"]


def test_goal_progress_allows_later_satisfaction_to_supersede_invalidation():
    first_outcome = DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id="broker-agent",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        attempt_fingerprint="attempt-1",
        status="partial",
        newly_satisfied_required_obligations=["quote:$present"],
    )
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Broker to insurer workflow",
        candidate_agent_ids=["broker-agent"],
        delegation_outcomes=[first_outcome],
    )
    invalidated, _ = invalidate_required_evidence(
        state,
        evidence_key="quote-evidence",
        obligation_keys=["quote:$present"],
        reason="The original quote evidence is no longer valid.",
        source_event_id="event-1",
    )
    invalidated.delegation_outcomes.append(
        first_outcome.model_copy(
            update={
                "outcome_id": "outcome-2",
                "dispatch_intent_id": "intent-2",
                "attempt_fingerprint": "attempt-2",
                "status": "fulfilled",
                "newly_satisfied_required_obligations": ["quote:$present"],
                "remaining_required_obligations": [],
            }
        )
    )

    updated = rebuild_goal_progress(invalidated)

    assert updated.goal_progress[0].satisfied_required_obligations == [
        "quote:$present"
    ]
    assert updated.goal_progress[0].remaining_required_obligations == []


def test_goal_progress_excludes_disposed_latest_revision():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Broker to insurer workflow",
        candidate_agent_ids=["broker-agent"],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="broker-agent",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="blocked",
            )
        ],
        goal_family_dispositions=[
            GoalFamilyDispositionRecord(
                event_id="dispose-1",
                goal_family_fingerprint="family-1",
                through_goal_revision_fingerprint="revision-1",
                status="abandoned",
                reason="No longer needed.",
            )
        ],
    )

    updated = rebuild_goal_progress(state)

    assert updated.goal_progress == []
