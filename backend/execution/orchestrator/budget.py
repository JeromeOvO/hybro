"""Pure bounded-loop budget policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import BudgetState, OrchestratorProfile, UsageRecord


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class AttemptUsageLedger:
    _usage: dict[tuple[int, int], UsageRecord] = field(default_factory=dict)

    def record(self, turn: int, attempt: int, usage: UsageRecord) -> None:
        self._usage[(turn, attempt)] = usage

    def totals(self) -> UsageRecord:
        return UsageRecord(
            input_tokens=sum(item.input_tokens for item in self._usage.values()),
            output_tokens=sum(item.output_tokens for item in self._usage.values()),
            cache_read_tokens=sum(
                item.cache_read_tokens for item in self._usage.values()
            ),
            cache_write_tokens=sum(
                item.cache_write_tokens for item in self._usage.values()
            ),
        )


class BudgetPolicy:
    def before_model_turn(
        self,
        budget: BudgetState,
        profile: OrchestratorProfile,
        *,
        now: datetime,
        purpose: str = "agent_turn",
    ) -> None:
        if now >= budget.deadline_at:
            raise BudgetExceeded("deadline")
        if budget.provider_retries_used > profile.max_provider_retries_total:
            raise BudgetExceeded("provider_retries")
        if (
            profile.max_input_tokens_total is not None
            and budget.input_tokens >= profile.max_input_tokens_total
        ):
            raise BudgetExceeded("input_tokens")
        if (
            profile.max_output_tokens_total is not None
            and budget.output_tokens >= profile.max_output_tokens_total
        ):
            raise BudgetExceeded("output_tokens")
        if purpose == "compaction":
            if budget.compactions_used >= profile.max_compactions:
                raise BudgetExceeded("compactions")
            return
        turn_limit = profile.max_model_turns + profile.grace_model_turns
        if budget.model_turns_used + budget.grace_turns_used >= turn_limit:
            raise BudgetExceeded("model_turns")

    def remaining_provider_retries(
        self, budget: BudgetState, profile: OrchestratorProfile
    ) -> int:
        return max(0, profile.max_provider_retries_total - budget.provider_retries_used)

    def record_provider_attempt(
        self,
        budget: BudgetState,
        profile: OrchestratorProfile,
        *,
        attempt_key: str,
        retry: bool,
    ) -> BudgetState:
        if attempt_key in budget.provider_attempt_keys:
            return budget
        retries = budget.provider_retries_used + (1 if retry else 0)
        if retries > profile.max_provider_retries_total:
            raise BudgetExceeded("provider_retries")
        return budget.model_copy(
            update={
                "provider_attempt_keys": [
                    *budget.provider_attempt_keys,
                    attempt_key,
                ],
                "provider_retries_used": retries,
            }
        )

    def record_usage_snapshot(
        self,
        budget: BudgetState,
        *,
        attempt_key: str,
        usage: UsageRecord,
    ) -> BudgetState:
        if attempt_key not in budget.provider_attempt_keys:
            raise ValueError("usage must identify a recorded provider attempt")
        previous = budget.usage_by_attempt.get(attempt_key, UsageRecord())
        ledger = dict(budget.usage_by_attempt)
        ledger[attempt_key] = usage
        return budget.model_copy(
            update={
                "usage_by_attempt": ledger,
                "input_tokens": budget.input_tokens
                + usage.input_tokens
                - previous.input_tokens,
                "output_tokens": budget.output_tokens
                + usage.output_tokens
                - previous.output_tokens,
            }
        )

    def record_assistant_turn(
        self, budget: BudgetState, *, grace: bool = False
    ) -> BudgetState:
        field = "grace_turns_used" if grace else "model_turns_used"
        return budget.model_copy(update={field: getattr(budget, field) + 1})

    def record_model_turn(
        self,
        budget: BudgetState,
        profile: OrchestratorProfile,
        *,
        provider_attempts: int,
        usage: UsageRecord | None,
        grace: bool = False,
    ) -> BudgetState:
        updates = {
            "provider_retries_used": budget.provider_retries_used
            + max(0, provider_attempts - 1),
            "input_tokens": budget.input_tokens + (usage.input_tokens if usage else 0),
            "output_tokens": budget.output_tokens
            + (usage.output_tokens if usage else 0),
        }
        if grace:
            updates["grace_turns_used"] = budget.grace_turns_used + 1
        else:
            updates["model_turns_used"] = budget.model_turns_used + 1
        updated = budget.model_copy(update=updates)
        self.before_token_side_effect(updated, profile)
        return updated

    def before_tool_call(
        self, budget: BudgetState, profile: OrchestratorProfile, *, now: datetime
    ) -> None:
        if now >= budget.deadline_at:
            raise BudgetExceeded("deadline")
        if budget.wrap_up_requested:
            raise BudgetExceeded("grace_tools_disabled")
        if budget.agent_calls_used >= profile.max_agent_calls:
            raise BudgetExceeded("tool_calls")

    def record_tool_calls(self, budget: BudgetState, count: int) -> BudgetState:
        return budget.model_copy(
            update={"agent_calls_used": budget.agent_calls_used + count}
        )

    def request_wrap_up(self, budget: BudgetState) -> BudgetState:
        if budget.wrap_up_requested:
            return budget
        return budget.model_copy(update={"wrap_up_requested": True})

    def record_compaction(
        self,
        budget: BudgetState,
        profile: OrchestratorProfile | None = None,
        *,
        provider_attempts: int = 0,
        usage: UsageRecord | None = None,
    ) -> BudgetState:
        updated = budget.model_copy(
            update={
                "compactions_used": budget.compactions_used + 1,
                "provider_retries_used": budget.provider_retries_used
                + max(0, provider_attempts - 1),
                "input_tokens": budget.input_tokens
                + (usage.input_tokens if usage else 0),
                "output_tokens": budget.output_tokens
                + (usage.output_tokens if usage else 0),
            }
        )
        if profile is not None:
            self.before_token_side_effect(updated, profile)
            if updated.provider_retries_used > profile.max_provider_retries_total:
                raise BudgetExceeded("provider_retries")
        return updated

    def before_token_side_effect(
        self, budget: BudgetState, profile: OrchestratorProfile
    ) -> None:
        if (
            profile.max_input_tokens_total is not None
            and budget.input_tokens > profile.max_input_tokens_total
        ):
            raise BudgetExceeded("input_tokens")
        if (
            profile.max_output_tokens_total is not None
            and budget.output_tokens > profile.max_output_tokens_total
        ):
            raise BudgetExceeded("output_tokens")


__all__ = ["AttemptUsageLedger", "BudgetExceeded", "BudgetPolicy"]
