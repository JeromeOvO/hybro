"""Injected Mongo implementation of the generic OrchestratorRunStore port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pymongo.errors import DuplicateKeyError

from execution.orchestrator.a2a_runtime.errors import RecoverableAdapterError
from execution.orchestrator.models import OrchestratorRunState, RecoveryClaim
from execution.orchestrator.settlement import transition_projection_intent

from .stores import (
    AsyncMongoCollection,
    _bounded,
    _to_list,
    _without_mongo_id,
)


@dataclass(frozen=True, slots=True)
class MongoRunStoreResult:
    outcome: str
    run: OrchestratorRunState | None


class MongoOrchestratorRunStore:
    def __init__(self, collection: AsyncMongoCollection) -> None:
        self.collection = _bounded(collection)

    async def create(
        self, run: OrchestratorRunState, *, command_id: str
    ) -> MongoRunStoreResult:
        existing = await self.load(run.run_id)
        if existing is not None:
            return MongoRunStoreResult(
                "replayed" if existing == run else "conflict", existing
            )
        duplicate = None
        if run.client_request_id is not None:
            value = await self.collection.find_one(
                {"room_id": run.room_id, "client_request_id": run.client_request_id}
            )
            duplicate = OrchestratorRunState.model_validate(value) if value else None
        if duplicate is not None:
            replay = (
                duplicate.request.request_fingerprint == run.request.request_fingerprint
            )
            return MongoRunStoreResult("replayed" if replay else "conflict", duplicate)
        candidate = run.model_copy(
            update={"processed_command_ids": [*run.processed_command_ids, command_id]}
        )
        try:
            await self.collection.insert_one(candidate.model_dump(mode="json"))
        except DuplicateKeyError:
            existing = await self.load(run.run_id)
            return MongoRunStoreResult(
                "replayed" if existing == candidate else "conflict", existing
            )
        except RecoverableAdapterError:
            existing = await self.load(run.run_id)
            if existing == candidate:
                return MongoRunStoreResult("replayed", existing)
            raise
        return MongoRunStoreResult("accepted", candidate)

    async def load(self, run_id: str) -> OrchestratorRunState | None:
        value = await self.collection.find_one({"run_id": run_id})
        return (
            OrchestratorRunState.model_validate(_without_mongo_id(value))
            if value
            else None
        )

    async def cas_mutate(
        self,
        run: OrchestratorRunState,
        *,
        expected_state_version: int,
        command_id: str,
    ) -> MongoRunStoreResult:
        current = await self.load(run.run_id)
        if current is None:
            return MongoRunStoreResult("error", None)
        if command_id in current.processed_command_ids:
            return MongoRunStoreResult("replayed", current)
        if (
            current.state_version != expected_state_version
            or run.state_version != expected_state_version + 1
        ):
            return MongoRunStoreResult("conflict", current)
        candidate = run.model_copy(
            update={"processed_command_ids": [*run.processed_command_ids, command_id]}
        )
        try:
            result = await self.collection.replace_one(
                {"run_id": run.run_id, "state_version": expected_state_version},
                candidate.model_dump(mode="json"),
            )
        except DuplicateKeyError:
            return MongoRunStoreResult("conflict", await self.load(run.run_id))
        except RecoverableAdapterError:
            winner = await self.load(run.run_id)
            if winner == candidate:
                return MongoRunStoreResult("replayed", winner)
            raise
        if int(getattr(result, "modified_count", 0)) != 1:
            winner = await self.load(run.run_id)
            return MongoRunStoreResult(
                "replayed" if winner == candidate else "conflict", winner
            )
        return MongoRunStoreResult("accepted", candidate)

    async def claim_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        if run is None or run.state_version != expected_state_version:
            return MongoRunStoreResult("conflict", run)
        candidate = run.model_copy(
            update={
                "recovery_claim": RecoveryClaim(
                    owner_id=owner_id, lease_expires_at=lease_expires_at
                ),
                "state_version": run.state_version + 1,
            }
        )
        return await self.cas_mutate(
            candidate,
            expected_state_version=run.state_version,
            command_id=f"claim:{owner_id}:{run.state_version}",
        )

    async def renew_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        if (
            run is None
            or run.state_version != expected_state_version
            or run.recovery_claim.owner_id != owner_id
        ):
            return MongoRunStoreResult("conflict", run)
        candidate = run.model_copy(
            update={
                "recovery_claim": run.recovery_claim.model_copy(
                    update={"lease_expires_at": lease_expires_at}
                ),
                "state_version": run.state_version + 1,
            }
        )
        return await self.cas_mutate(
            candidate,
            expected_state_version=run.state_version,
            command_id=f"renew:{owner_id}:{run.state_version}",
        )

    async def release_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        next_attempt_at: datetime | None,
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        if (
            run is None
            or run.state_version != expected_state_version
            or run.recovery_claim.owner_id != owner_id
        ):
            return MongoRunStoreResult("conflict", run)
        candidate = run.model_copy(
            update={
                "recovery_claim": RecoveryClaim(next_attempt_at=next_attempt_at),
                "state_version": run.state_version + 1,
            }
        )
        return await self.cas_mutate(
            candidate,
            expected_state_version=run.state_version,
            command_id=f"release:{owner_id}:{run.state_version}",
        )

    async def list_due_runs(
        self, *, due_at: datetime, limit: int
    ) -> list[OrchestratorRunState]:
        query = {
            "status": {
                "$in": [
                    "queued",
                    "running",
                    "waiting_external",
                    "awaiting_user",
                    "finalizing",
                ]
            },
            "$and": [
                {
                    "$or": [
                        {"recovery_claim.next_attempt_at": None},
                        {"recovery_claim.next_attempt_at": {"$lte": due_at}},
                    ]
                },
                {
                    "$or": [
                        {"recovery_claim.lease_expires_at": None},
                        {"recovery_claim.lease_expires_at": {"$lte": due_at}},
                    ]
                },
            ],
        }
        return [
            OrchestratorRunState.model_validate(value)
            for value in await _to_list(self.collection.find(query), length=limit)
        ]

    async def claim_projection_intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> MongoRunStoreResult:
        return await self._intent(
            run_id,
            intent_id,
            expected_state_version=expected_state_version,
            to_status="claimed",
            command_id=f"claim-intent:{intent_id}:{expected_state_version}",
            claim_owner=owner_id,
            claim_expires_at=lease_expires_at,
        )

    async def complete_projection_intent(
        self, run_id: str, intent_id: str, *, expected_state_version: int, owner_id: str
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        item = _find_intent(run, intent_id)
        if item is None or item.claim_owner != owner_id:
            return MongoRunStoreResult("conflict", run)
        return await self._intent(
            run_id,
            intent_id,
            expected_state_version=expected_state_version,
            to_status="completed",
            command_id=f"complete-intent:{intent_id}:{expected_state_version}",
        )

    async def block_projection_intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        reason: str,
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        item = _find_intent(run, intent_id)
        if item is None or item.claim_owner not in {None, owner_id}:
            return MongoRunStoreResult("conflict", run)
        return await self._intent(
            run_id,
            intent_id,
            expected_state_version=expected_state_version,
            to_status="blocked",
            command_id=f"block-intent:{intent_id}:{expected_state_version}",
            blocked_reason=reason,
        )

    async def _intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        to_status: str,
        command_id: str,
        **kwargs: object,
    ) -> MongoRunStoreResult:
        run = await self.load(run_id)
        if run is None or run.state_version != expected_state_version:
            return MongoRunStoreResult("conflict", run)
        index = next(
            (
                index
                for index, item in enumerate(run.projection_outbox)
                if item.intent_id == intent_id
            ),
            None,
        )
        if index is None:
            return MongoRunStoreResult("conflict", run)
        intents = list(run.projection_outbox)
        intents[index] = transition_projection_intent(
            intents[index], to_status=to_status, **kwargs
        )  # type: ignore[arg-type]
        candidate = run.model_copy(
            update={
                "projection_outbox": intents,
                "state_version": run.state_version + 1,
            }
        )
        return await self.cas_mutate(
            candidate, expected_state_version=run.state_version, command_id=command_id
        )


def _find_intent(run: OrchestratorRunState | None, intent_id: str):
    if run is None:
        return None
    return next(
        (item for item in run.projection_outbox if item.intent_id == intent_id), None
    )
