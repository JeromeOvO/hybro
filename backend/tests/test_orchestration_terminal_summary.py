from execution.orchestration.terminal_summary import build_terminal_summary
from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    OrchestrationRunState,
)


def test_terminal_summary_names_blockers_and_last_progress():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Broker to insurer workflow",
        candidate_agent_ids=["broker-agent"],
        blockers=[
            BlockerRecord(
                key="blocker-1",
                description="Need requested limit.",
                blocked_output_keys=["broker_submission"],
                source="agent",
                claimed_user_only=True,
                validated_user_only=True,
                validation_status="validated",
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="broker-agent",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="blocked",
                remaining_required_obligations=["broker_submission:requested_limit"],
            )
        ],
    )

    summary = build_terminal_summary(
        state,
        reason="delegate action violates outcome policy: delegate_no_progress_repeat",
    )

    assert summary["code"] == "orchestration_failed"
    assert summary["reason"] == "delegate action violates outcome policy: delegate_no_progress_repeat"
    assert summary["last_outcome_status"] == "blocked"
    assert summary["validated_blocker_keys"] == ["blocker-1"]
    assert summary["recommended_next_action"] == "ask_user"
