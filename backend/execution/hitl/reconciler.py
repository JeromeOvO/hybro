from __future__ import annotations

from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.hitl.validation import validate_command_route_consistency
from models.hitl import (
    HITLEventType,
    HITLInteractionStatus,
    HITLRequest,
    HITLResumeCommandStatus,
    HITLStatus,
)
from models.orchestration import TERMINAL_ORCHESTRATION_STATUSES, OrchestrationStatus

logger = get_logger(__name__)

# Failed remote inspections of an uncertain delivery before the owning
# interaction fails terminally instead of staying DELIVERY_UNCERTAIN forever.
_MAX_UNCERTAIN_INSPECT_ATTEMPTS = 3


class HITLLifecycleReconciler:
    """Bounded, independently-fenced repair passes for durable HITL state."""

    def __init__(
        self,
        *,
        lifecycle,
        service,
        application,
        orchestration_run_store=None,
        inspect_remote_command=None,
        limit: int = 100,
    ) -> None:
        self._lifecycle = lifecycle
        self._service = service
        self._application = application
        self._orchestration_run_store = orchestration_run_store
        self._inspect_remote_command = inspect_remote_command
        self._limit = limit

    async def reconcile_lifecycle(self) -> dict[str, int]:
        counts = {
            "materializing": 0,
            "expired": 0,
            "answers": 0,
            "applications": 0,
            "commands": 0,
            "terminal_interactions": 0,
            "terminal_requests": 0,
            "divergence": 0,
            "errors": 0,
        }
        passes = (
            ("materializing", self._resume_materializing),
            ("expired", self._expire_due),
            ("answers", self._repair_answers),
            ("applications", self._resume_applications),
            ("commands", self._reconcile_commands),
            ("terminal_interactions", self._reconcile_terminal_interactions),
            ("terminal_requests", self._reconcile_terminal_requests),
            ("divergence", self._heal_run_divergence),
        )
        for name, operation in passes:
            try:
                counts[name] = await operation()
            except Exception:
                counts["errors"] += 1
                logger.exception("HITL lifecycle reconciliation pass failed: %s", name)
        return counts

    async def _resume_materializing(self) -> int:
        repaired = 0
        async for interaction in self._lifecycle.iter_materializing_interactions(
            limit=self._limit
        ):
            try:
                requests = await self._service.resume_materializing_interaction(
                    interaction
                )
                repaired += int(requests is not None)
            except Exception:
                logger.warning(
                    "HITL interaction remains materializing",
                    extra={"interaction_id": interaction.get("interaction_id")},
                    exc_info=True,
                )
        return repaired

    async def _expire_due(self) -> int:
        repaired = 0
        async for interaction in self._lifecycle.iter_due_interactions(
            utcnow(), limit=self._limit
        ):
            try:
                await self._application.expire_interaction(self._service, interaction)
                repaired += 1
            except Exception:
                logger.exception(
                    "Failed to expire HITL interaction %s",
                    interaction.get("interaction_id"),
                )
        return repaired

    async def _repair_answers(self) -> int:
        repaired = 0
        async for interaction in self._lifecycle.iter_active_interactions(
            limit=self._limit
        ):
            try:
                before = list(interaction.get("answer_refs") or [])
                current = await self._application.repair_persisted_answer_refs(
                    self._service, interaction
                )
                if list(current.get("answer_refs") or []) != before:
                    repaired += 1
                if (
                    current.get("status")
                    == HITLInteractionStatus.ANSWERS_RECORDED.value
                ):
                    await self._application.apply_interaction(self._service, current)
            except Exception:
                logger.warning(
                    "HITL answer reference repair remains pending",
                    extra={"interaction_id": interaction.get("interaction_id")},
                    exc_info=True,
                )
        return repaired

    async def _resume_applications(self) -> int:
        repaired = 0
        async for interaction in self._lifecycle.iter_stale_applications(
            utcnow(), limit=self._limit
        ):
            try:
                if interaction.get("status") == (
                    HITLInteractionStatus.DELIVERY_UNCERTAIN.value
                ):
                    command = (
                        await self._lifecycle.get_resume_command_for_interaction_strict(
                            interaction["interaction_id"],
                            int(interaction.get("application_revision") or 0),
                        )
                    )
                    if (
                        command is not None
                        and command.get("kind") != "supervisor_resume"
                    ):
                        continue
                    if command is not None:
                        await self._lifecycle.mark_resume_command_state(
                            command["command_id"],
                            claim_id=None,
                            expected_statuses=[
                                HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value
                            ],
                            status=HITLResumeCommandStatus.RETRYABLE_ERROR.value,
                            error_code="supervisor_effect_replay",
                            error_message="Replaying idempotent supervisor effect",
                            retry_after_seconds=0,
                        )
                    # Without a remote A2A command no remote delivery could have begun;
                    # reclaim the orphaned aggregate without inventing a resend.
                    resumed = await self._lifecycle.resume_uncertain_interaction(
                        interaction["interaction_id"],
                        claim_id=f"orphan-recovery:{interaction['interaction_id']}",
                    )
                    if resumed is None:
                        continue
                    interaction = resumed
                await self._application.apply_interaction(self._service, interaction)
                repaired += 1
            except Exception:
                logger.warning(
                    "HITL application remains pending",
                    extra={"interaction_id": interaction.get("interaction_id")},
                    exc_info=True,
                )
        return repaired

    async def _finish_confirmed_command(self, command: dict) -> None:
        interaction = await self._lifecycle.get_interaction_strict(
            command["interaction_id"]
        )
        if interaction is None:
            return
        if interaction.get("status") == HITLInteractionStatus.DELIVERY_UNCERTAIN.value:
            resumed = await self._lifecycle.resume_uncertain_interaction(
                interaction["interaction_id"],
                claim_id=f"confirmed:{command['command_id']}",
            )
            if resumed is not None:
                interaction = resumed
        if interaction.get("status") == HITLInteractionStatus.APPLIED.value:
            await self._application.apply_interaction(self._service, interaction)
            return
        if interaction.get("status") == HITLInteractionStatus.APPLYING.value:
            await self._application.apply_interaction(self._service, interaction)

    async def _reconcile_commands(self) -> int:  # noqa: C901
        repaired = 0
        async for command in self._lifecycle.iter_due_resume_commands(
            utcnow(), limit=self._limit
        ):
            try:
                interaction = await self._lifecycle.get_interaction_strict(
                    command["interaction_id"]
                )
                if interaction is None:
                    continue
                await self._application._request_rows(
                    self._service, interaction, require_answers=False
                )
                validate_command_route_consistency(interaction, command)
                status = command.get("status")
                if (
                    status == HITLResumeCommandStatus.DELIVERING.value
                    and command.get("kind") == "supervisor_resume"
                ):
                    retryable = await self._lifecycle.reclaim_stale_resume_command(
                        command["command_id"],
                        observed_claim_id=command.get("claim_id"),
                        observed_version=int(command["version"]),
                        observed_lease_expires_at=command["lease_expires_at"],
                        now=utcnow(),
                        status=HITLResumeCommandStatus.RETRYABLE_ERROR.value,
                        error_code="supervisor_worker_lost",
                        error_message="Replaying idempotent supervisor effect",
                        retry_after_seconds=0,
                    )
                    if retryable is not None:
                        repaired += 1
                    continue
                if status == HITLResumeCommandStatus.DELIVERING.value:
                    uncertain = await self._lifecycle.reclaim_stale_resume_command(
                        command["command_id"],
                        observed_claim_id=command.get("claim_id"),
                        observed_version=int(command["version"]),
                        observed_lease_expires_at=command["lease_expires_at"],
                        now=utcnow(),
                        status=HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value,
                        error_code="worker_lost_after_delivery_started",
                        error_message="Delivery acknowledgement was not persisted",
                    )
                    if uncertain is not None:
                        if interaction.get("application_claim_id"):
                            await self._lifecycle.mark_interaction_application_state(
                                interaction["interaction_id"],
                                claim_id=interaction["application_claim_id"],
                                status=HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
                                error="Remote continuation delivery is uncertain",
                            )
                        repaired += 1
                    continue
                if status in {
                    HITLResumeCommandStatus.ACKNOWLEDGED.value,
                    HITLResumeCommandStatus.PROJECTED.value,
                }:
                    if status == HITLResumeCommandStatus.ACKNOWLEDGED.value:
                        projected = await self._lifecycle.mark_resume_command_state(
                            command["command_id"],
                            claim_id=None,
                            expected_statuses=[status],
                            status=HITLResumeCommandStatus.PROJECTED.value,
                            response_snapshot=command.get("response_snapshot"),
                        )
                        if projected is None:
                            continue
                        command = projected
                    await self._finish_confirmed_command(command)
                    repaired += 1
                    continue
                if status == HITLResumeCommandStatus.PERMANENT_FAILURE.value:
                    await self._application.fail_interaction(
                        self._service,
                        interaction,
                        reason=(
                            command.get("error_message")
                            or "Remote continuation permanently failed"
                        ),
                        application_claim_id=interaction.get("application_claim_id"),
                    )
                    repaired += 1
                    continue
                if status == HITLResumeCommandStatus.RETRYABLE_ERROR.value:
                    await self._application.apply_interaction(
                        self._service, interaction
                    )
                    repaired += 1
                    continue
                if (
                    status == HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value
                    and self._inspect_remote_command is not None
                ):
                    snapshot = await self._inspect_remote_command(command)
                    if snapshot and snapshot.get("advanced") is True:
                        acknowledged = await self._lifecycle.mark_resume_command_state(
                            command["command_id"],
                            claim_id=None,
                            expected_statuses=[status],
                            status=HITLResumeCommandStatus.ACKNOWLEDGED.value,
                            response_snapshot=snapshot,
                        )
                        if acknowledged is not None:
                            projected = await self._lifecycle.mark_resume_command_state(
                                command["command_id"],
                                claim_id=None,
                                expected_statuses=[
                                    HITLResumeCommandStatus.ACKNOWLEDGED.value
                                ],
                                status=HITLResumeCommandStatus.PROJECTED.value,
                                response_snapshot=snapshot,
                            )
                            if projected is not None:
                                await self._finish_confirmed_command(projected)
                                repaired += 1
                        continue
                    if snapshot is None:
                        # Remote state could not be inspected at all (missing
                        # agent URL or fetch failure). Count the failure so an
                        # unreachable agent cannot leave the command stuck in
                        # DELIVERY_UNCERTAIN forever: after a bounded number
                        # of failed inspects the interaction fails terminally.
                        recorded = (
                            await self._lifecycle.record_uncertain_inspect_failure(
                                command["command_id"]
                            )
                        )
                        if (
                            recorded is not None
                            and int(recorded.get("inspect_attempts") or 0)
                            >= _MAX_UNCERTAIN_INSPECT_ATTEMPTS
                        ):
                            failed = await self._lifecycle.mark_resume_command_state(
                                command["command_id"],
                                claim_id=None,
                                expected_statuses=[
                                    HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value
                                ],
                                status=HITLResumeCommandStatus.PERMANENT_FAILURE.value,
                                error_code="uncertain_inspect_exhausted",
                                error_message=(
                                    "Remote delivery could not be confirmed after "
                                    "repeated inspections"
                                ),
                            )
                            if failed is not None:
                                await self._application.fail_interaction(
                                    self._service,
                                    interaction,
                                    reason=(
                                        "Remote continuation delivery could not be "
                                        "confirmed"
                                    ),
                                    application_claim_id=interaction.get(
                                        "application_claim_id"
                                    ),
                                )
                                repaired += 1
            except Exception:
                logger.warning(
                    "HITL resume command remains pending",
                    extra={"command_id": command.get("command_id")},
                    exc_info=True,
                )
        return repaired

    async def _reconcile_terminal_interactions(self) -> int:
        repaired = 0
        async for (
            interaction
        ) in self._lifecycle.iter_unreconciled_terminal_interactions(limit=self._limit):
            try:
                await self._service.reconcile_terminal_interaction(interaction)
                repaired += 1
            except Exception:
                logger.warning(
                    "HITL terminal interaction remains unreconciled",
                    extra={"interaction_id": interaction.get("interaction_id")},
                    exc_info=True,
                )
        return repaired

    async def _reconcile_terminal_requests(self) -> int:
        repaired = 0
        async for row in self._lifecycle.iter_unreconciled_terminal_requests(
            limit=self._limit
        ):
            try:
                request = HITLRequest(**{k: v for k, v in row.items() if k != "_id"})
                event_type = (
                    HITLEventType.INPUT_CANCELED
                    if request.status == HITLStatus.CANCELED
                    else HITLEventType.INPUT_EXPIRED
                )
                await self._service._reconcile_terminal_request(
                    request, event_type=event_type
                )
                repaired += 1
            except Exception:
                logger.warning(
                    "HITL terminal request remains unreconciled",
                    extra={"request_id": row.get("request_id")},
                    exc_info=True,
                )
        return repaired

    async def _heal_run_divergence(self) -> int:
        if self._orchestration_run_store is None:
            return 0
        repaired = 0
        async for interaction in self._lifecycle.iter_active_interactions(
            limit=self._limit
        ):
            if interaction.get("status") in {
                HITLInteractionStatus.OPEN.value,
                HITLInteractionStatus.PARTIALLY_ANSWERED.value,
            }:
                try:
                    repaired += await self._service.recover_open_interaction_projection(
                        interaction
                    )
                except Exception:
                    logger.warning(
                        "Failed to recover HITL open projection",
                        extra={"interaction_id": interaction.get("interaction_id")},
                        exc_info=True,
                    )
            run_id = interaction.get("orchestration_run_id")
            if not run_id:
                continue
            try:
                run = await self._orchestration_run_store.get_run(run_id)
                if run is None:
                    continue
                if run.status in TERMINAL_ORCHESTRATION_STATUSES:
                    target = (
                        HITLInteractionStatus.CANCELED.value
                        if run.status == OrchestrationStatus.CANCELED
                        else HITLInteractionStatus.FAILED.value
                    )
                    updated = await self._lifecycle.terminalize_interaction(
                        interaction["interaction_id"],
                        expected_statuses=[interaction["status"]],
                        status=target,
                        reason=f"Owning run is already {run.status.value}",
                    )
                    repaired += int(updated is not None)
            except Exception:
                logger.warning(
                    "Failed to reconcile HITL/run divergence",
                    extra={
                        "interaction_id": interaction.get("interaction_id"),
                        "run_id": run_id,
                    },
                    exc_info=True,
                )
        return repaired


__all__ = ["HITLLifecycleReconciler"]
