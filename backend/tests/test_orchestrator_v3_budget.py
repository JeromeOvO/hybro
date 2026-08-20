from __future__ import annotations

from datetime import timedelta

import pytest

from execution.orchestrator.budget import (
    AttemptUsageLedger,
    BudgetExceeded,
    BudgetPolicy,
)
from execution.orchestrator.models import BudgetState, UsageRecord
from tests._orchestrator_v3_helpers import NOW, profile


def budget(**updates):
    values = {"deadline_at": NOW + timedelta(seconds=60)}
    values.update(updates)
    return BudgetState(**values)


def test_attempt_usage_ledger_replaces_replayed_snapshot_instead_of_double_counting():
    ledger = AttemptUsageLedger()
    ledger.record(1, 1, UsageRecord(input_tokens=10, output_tokens=2))
    ledger.record(1, 1, UsageRecord(input_tokens=10, output_tokens=3))
    ledger.record(1, 2, UsageRecord(input_tokens=4, output_tokens=1))
    assert ledger.totals() == UsageRecord(input_tokens=14, output_tokens=4)


def test_turn_retry_usage_and_tool_budgets_are_cumulative():
    policy = BudgetPolicy()
    configured = profile()
    updated = policy.record_model_turn(
        budget(),
        configured,
        provider_attempts=3,
        usage=UsageRecord(input_tokens=5, output_tokens=2),
    )
    assert (updated.model_turns_used, updated.provider_retries_used) == (1, 2)
    assert policy.remaining_provider_retries(updated, configured) == 2
    updated = policy.record_tool_calls(updated, 3)
    assert updated.agent_calls_used == 3


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"deadline_at": NOW}, "deadline"),
        ({"provider_retries_used": 5}, "provider_retries"),
        ({"input_tokens": 10000}, "input_tokens"),
        ({"output_tokens": 5000}, "output_tokens"),
        ({"model_turns_used": 5, "grace_turns_used": 1}, "model_turns"),
    ],
)
def test_model_turn_limits_fail_typed(updates, reason):
    with pytest.raises(BudgetExceeded) as caught:
        BudgetPolicy().before_model_turn(budget(**updates), profile(), now=NOW)
    assert caught.value.reason == reason


def test_wrap_up_is_idempotent_and_disables_new_tools_during_grace():
    policy = BudgetPolicy()
    wrapped = policy.request_wrap_up(budget())
    assert policy.request_wrap_up(wrapped) is wrapped
    with pytest.raises(BudgetExceeded, match="grace_tools_disabled"):
        policy.before_tool_call(wrapped, profile(), now=NOW)


def test_compaction_uses_its_own_budget_without_incrementing_normal_turns():
    policy = BudgetPolicy()
    compacted = policy.record_compaction(budget())
    assert compacted.compactions_used == 1
    assert compacted.model_turns_used == 0
    with pytest.raises(BudgetExceeded, match="compactions"):
        policy.before_model_turn(compacted, profile(), now=NOW, purpose="compaction")
