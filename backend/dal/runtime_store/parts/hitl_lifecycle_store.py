from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.hitl import HITLInteractionStatus, HITLResumeCommandStatus

logger = get_logger(__name__)

_INTERACTION_TERMINAL = {
    HITLInteractionStatus.APPLIED.value,
    HITLInteractionStatus.CANCELED.value,
    HITLInteractionStatus.EXPIRED.value,
    HITLInteractionStatus.FAILED.value,
}
_COMMAND_CLAIMABLE = {
    HITLResumeCommandStatus.PENDING.value,
    HITLResumeCommandStatus.RETRYABLE_ERROR.value,
}


def _legacy_answer_digest(row: dict[str, Any]) -> str:
    existing = row.get("answer_digest")
    if isinstance(existing, str) and existing:
        return existing
    answer = row.get("user_input")
    if not isinstance(answer, str):
        raise ValueError("legacy answered HITL request has no verifiable answer")
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def _validate_legacy_group(
    requests: list[dict[str, Any]], interaction_id: str, expected: int
) -> None:
    request_ids = [row.get("request_id") for row in requests]
    if any(
        not isinstance(request_id, str) or not request_id for request_id in request_ids
    ):
        raise ValueError("legacy HITL group contains a blank request_id")
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("legacy HITL group contains duplicate request IDs")
    totals = {int(row.get("group_total") or 1) for row in requests}
    if totals != {expected}:
        raise ValueError("legacy HITL group has conflicting group_total values")
    if len(requests) > expected:
        raise ValueError("legacy HITL group has more requests than group_total")
    if expected > 1:
        indices = [row.get("group_index") for row in requests]
        if any(
            not isinstance(index, int) or not 0 <= index < expected for index in indices
        ):
            raise ValueError("legacy HITL group has invalid group indices")
        if len(set(indices)) != len(indices):
            raise ValueError("legacy HITL group contains duplicate group indices")
        if len(requests) == expected and set(indices) != set(range(expected)):
            raise ValueError("legacy HITL group indices are incomplete")
        if any(
            row.get("group_id") != interaction_id
            or (
                row.get("interaction_id") is not None
                and row.get("interaction_id") != interaction_id
            )
            for row in requests
        ):
            raise ValueError("legacy HITL group has incompatible interaction IDs")


class HITLLifecycleRuntimeStorePart:
    """CAS/fenced persistence for HITL aggregates and remote commands."""

    def __init__(self, *, interactions, resume_commands, hitl_requests) -> None:
        self._interactions = interactions
        self._resume_commands = resume_commands
        self._hitl_requests = hitl_requests

    async def materialize_interaction(
        self, interaction_data: dict[str, Any]
    ) -> dict[str, Any]:
        doc = dict(interaction_data)
        try:
            await self._interactions.insert_one(doc)
            return doc
        except DuplicateKeyError:
            existing = await self.get_interaction_strict(doc["interaction_id"])
            if existing is None:
                raise
            immutable = (
                "room_id",
                "user_message_id",
                "source",
                "expected_request_count",
            )
            if any(existing.get(key) != doc.get(key) for key in immutable):
                raise ValueError("conflicting HITL interaction metadata") from None
            return existing

    async def attach_interaction_request(
        self,
        interaction_id: str,
        *,
        request_id: str,
        required: bool,
        expires_at: datetime | None,
        group_index: int | None = None,
    ) -> dict[str, Any] | None:
        for _attempt in range(8):
            current = await self.get_interaction_strict(interaction_id)
            if current is None:
                return None
            request_ids = list(current.get("request_ids") or [])
            request_order = list(current.get("request_order") or [])
            if not request_order:
                request_order = [
                    {"request_id": existing, "index": index}
                    for index, existing in enumerate(request_ids)
                ]
            if request_id not in request_ids:
                request_order.append(
                    {
                        "request_id": request_id,
                        "index": (
                            group_index
                            if group_index is not None
                            else len(request_order)
                        ),
                    }
                )
            request_order.sort(key=lambda item: (item["index"], item["request_id"]))
            request_ids = [item["request_id"] for item in request_order]
            required_ids = list(current.get("required_request_ids") or [])
            if required and request_id not in required_ids:
                required_ids.append(request_id)
            current_expiry = current.get("expires_at")
            shared_expiry = (
                min(
                    value for value in (current_expiry, expires_at) if value is not None
                )
                if current_expiry is not None or expires_at is not None
                else None
            )
            expected = int(current["expected_request_count"])
            status = current.get("status")
            if status == HITLInteractionStatus.MATERIALIZING.value:
                if len(request_ids) >= expected:
                    status = HITLInteractionStatus.OPEN.value
            updated = await self._interactions.find_one_and_update(
                {
                    "interaction_id": interaction_id,
                    "version": current.get("version", 1),
                    "status": {"$nin": list(_INTERACTION_TERMINAL)},
                },
                {
                    "$set": {
                        "request_ids": request_ids,
                        "request_order": request_order,
                        "required_request_ids": [
                            request_id
                            for request_id in request_ids
                            if request_id in required_ids
                        ],
                        "expires_at": shared_expiry,
                        "status": status,
                        "updated_at": utcnow(),
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                return updated
        return None

    async def get_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        try:
            return await self.get_interaction_strict(interaction_id)
        except Exception:
            logger.error("Failed to read HITL interaction", exc_info=True)
            return None

    async def get_interaction_strict(
        self, interaction_id: str
    ) -> dict[str, Any] | None:
        return await self._interactions.find_one({"interaction_id": interaction_id})

    async def get_interaction_for_request_strict(
        self, request_id: str
    ) -> dict[str, Any] | None:
        return await self._interactions.find_one({"request_ids": request_id})

    async def synthesize_interaction_from_requests(  # noqa: C901
        self, requests: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not requests:
            return None
        first = requests[0]
        interaction_id = (
            first.get("group_id") or first.get("interaction_id") or first["request_id"]
        )
        expected = int(first.get("group_total") or 1)
        _validate_legacy_group(requests, interaction_id, expected)
        source = first.get("source")
        room_id = first.get("room_id")
        user_message_id = first.get("user_message_id")
        if any(
            (row.get("group_id") or row.get("interaction_id") or row["request_id"])
            != interaction_id
            or row.get("source") != source
            or row.get("room_id") != room_id
            or row.get("user_message_id") != user_message_id
            for row in requests
        ):
            raise ValueError("conflicting legacy HITL interaction records")
        statuses = {row.get("status") for row in requests}
        answered = [
            row["request_id"]
            for row in requests
            if row.get("status")
            in {
                "answer_recorded",
                "processing",
                "responded",
            }
        ]
        if statuses == {"responded"}:
            status = HITLInteractionStatus.APPLIED.value
        elif "processing" in statuses:
            status = HITLInteractionStatus.ANSWERS_RECORDED.value
        elif statuses & {"canceled"}:
            status = HITLInteractionStatus.CANCELED.value
        elif statuses & {"expired"}:
            status = HITLInteractionStatus.EXPIRED.value
        elif answered:
            status = HITLInteractionStatus.PARTIALLY_ANSWERED.value
        elif len(requests) >= expected:
            status = HITLInteractionStatus.OPEN.value
        else:
            status = HITLInteractionStatus.MATERIALIZING.value
        expiries = [row.get("expires_at") for row in requests if row.get("expires_at")]
        now = utcnow()
        doc = {
            "schema_version": 2,
            "interaction_id": interaction_id,
            "room_id": room_id,
            "user_message_id": user_message_id,
            "orchestration_run_id": first.get("orchestration_run_id"),
            "source": source,
            "request_ids": [],
            "request_order": [],
            "expected_request_count": expected,
            "required_request_ids": [],
            "status": HITLInteractionStatus.MATERIALIZING.value,
            "version": 1,
            "expires_at": min(expiries) if expiries else None,
            "answer_request_ids": [],
            "answer_refs": [],
            "application_revision": 0,
            "application_attempts": 0,
            # Legacy request rows cannot prove the owning-run event was appended;
            # replay through the idempotent run projection journal.
            "run_projection_status": "pending",
            "terminal_reconciled": False,
            "created_at": min(
                (row.get("created_at") or now for row in requests), default=now
            ),
            "updated_at": now,
        }
        interaction = await self.materialize_interaction(doc)
        for row in sorted(requests, key=lambda item: item.get("group_index") or 0):
            interaction = await self.attach_interaction_request(
                interaction_id,
                request_id=row["request_id"],
                required=True,
                expires_at=row.get("expires_at"),
                group_index=row.get("group_index"),
            )
            if interaction is None:
                return None
        if answered:
            for row in requests:
                if row["request_id"] not in answered:
                    continue
                interaction = await self.record_interaction_answer(
                    interaction_id,
                    request_id=row["request_id"],
                    answer_digest=_legacy_answer_digest(row),
                )
                if interaction is None:
                    return None
        if status in {
            HITLInteractionStatus.APPLIED.value,
            HITLInteractionStatus.CANCELED.value,
            HITLInteractionStatus.EXPIRED.value,
        }:
            current = await self.get_interaction_strict(interaction_id)
            if current and current.get("status") not in _INTERACTION_TERMINAL:
                updates: dict[str, Any] = {
                    "status": status,
                    "updated_at": utcnow(),
                }
                if status == HITLInteractionStatus.APPLIED.value:
                    updates["applied_at"] = utcnow()
                interaction = await self._interactions.find_one_and_update(
                    {
                        "interaction_id": interaction_id,
                        "version": current.get("version", 1),
                    },
                    {"$set": updates, "$inc": {"version": 1}},
                    return_document=ReturnDocument.AFTER,
                )
        return interaction

    async def record_interaction_answer(
        self,
        interaction_id: str,
        *,
        request_id: str,
        answer_digest: str,
    ) -> dict[str, Any] | None:
        for _attempt in range(8):
            current = await self.get_interaction_strict(interaction_id)
            if current is None or request_id not in (current.get("request_ids") or []):
                return None
            if request_id in (current.get("answer_request_ids") or []):
                return current
            if current.get("status") not in {
                HITLInteractionStatus.OPEN.value,
                HITLInteractionStatus.PARTIALLY_ANSWERED.value,
            }:
                return None
            answer_ids = [*(current.get("answer_request_ids") or []), request_id]
            required = set(current.get("required_request_ids") or [])
            complete = required.issubset(answer_ids)
            status = (
                HITLInteractionStatus.ANSWERS_RECORDED.value
                if complete
                else HITLInteractionStatus.PARTIALLY_ANSWERED.value
            )
            updated = await self._interactions.find_one_and_update(
                {
                    "interaction_id": interaction_id,
                    "version": current.get("version", 1),
                    "status": current.get("status"),
                    "$or": [
                        {"expires_at": {"$gt": utcnow()}},
                        {"expires_at": None},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "answer_request_ids": answer_ids,
                        "status": status,
                        "updated_at": utcnow(),
                    },
                    "$push": {
                        "answer_refs": {
                            "request_id": request_id,
                            "digest": answer_digest,
                        }
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                return updated
        return None

    async def claim_interaction_application(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now = utcnow()
        lease = now + timedelta(seconds=lease_seconds)
        claimed = await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "status": HITLInteractionStatus.ANSWERS_RECORDED.value,
            },
            {
                "$set": {
                    "status": HITLInteractionStatus.APPLYING.value,
                    "application_claim_id": claim_id,
                    "application_lease_expires_at": lease,
                    "application_started_at": now,
                    "application_error": None,
                    "updated_at": now,
                },
                "$inc": {
                    "application_revision": 1,
                    "application_attempts": 1,
                    "version": 1,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed is not None:
            return claimed
        return await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "status": HITLInteractionStatus.APPLYING.value,
                "$or": [
                    {"application_lease_expires_at": {"$lte": now}},
                    {"application_lease_expires_at": None},
                    {"application_lease_expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "application_claim_id": claim_id,
                    "application_lease_expires_at": lease,
                    "application_error": None,
                    "updated_at": now,
                },
                "$inc": {"application_attempts": 1, "version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def renew_interaction_application(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._interactions.update_one(
            {
                "interaction_id": interaction_id,
                "status": HITLInteractionStatus.APPLYING.value,
                "application_claim_id": claim_id,
            },
            {
                "$set": {
                    "application_lease_expires_at": utcnow()
                    + timedelta(seconds=lease_seconds),
                    "updated_at": utcnow(),
                },
                "$inc": {"version": 1},
            },
        )
        return bool(getattr(result, "matched_count", result))

    async def resume_uncertain_interaction(
        self,
        interaction_id: str,
        *,
        claim_id: str,
    ) -> dict[str, Any] | None:
        now = utcnow()
        return await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "status": HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
            },
            {
                "$set": {
                    "status": HITLInteractionStatus.APPLYING.value,
                    "application_claim_id": claim_id,
                    "application_lease_expires_at": now,
                    "application_error": None,
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def claim_run_answer_projection(
        self,
        interaction_id: str,
        *,
        application_revision: int,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now = utcnow()
        return await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "application_revision": application_revision,
                "$or": [
                    {"run_projection_status": "pending"},
                    {"run_projection_status": "failed"},
                    {
                        "run_projection_status": "applying",
                        "run_projection_lease_expires_at": {"$lte": now},
                    },
                    {"run_projection_status": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "run_projection_status": "applying",
                    "run_projection_claim_id": claim_id,
                    "run_projection_lease_expires_at": now
                    + timedelta(seconds=lease_seconds),
                    "run_projection_error": None,
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def renew_run_answer_projection(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._interactions.update_one(
            {
                "interaction_id": interaction_id,
                "run_projection_status": "applying",
                "run_projection_claim_id": claim_id,
            },
            {
                "$set": {
                    "run_projection_lease_expires_at": utcnow()
                    + timedelta(seconds=lease_seconds),
                    "updated_at": utcnow(),
                },
                "$inc": {"version": 1},
            },
        )
        return bool(getattr(result, "matched_count", result))

    async def mark_run_answer_projection(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"applied", "failed"}:
            raise ValueError("invalid run projection status")
        return await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "run_projection_status": "applying",
                "run_projection_claim_id": claim_id,
            },
            {
                "$set": {
                    "run_projection_status": status,
                    "run_projection_claim_id": None,
                    "run_projection_lease_expires_at": None,
                    "run_projection_error": error,
                    "updated_at": utcnow(),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def mark_interaction_application_state(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        allowed = {
            HITLInteractionStatus.ANSWERS_RECORDED.value,
            HITLInteractionStatus.APPLYING.value,
            HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
            HITLInteractionStatus.APPLIED.value,
            HITLInteractionStatus.FAILED.value,
        }
        if status not in allowed:
            raise ValueError(f"invalid application target status {status!r}")
        now = utcnow()
        updates: dict[str, Any] = {
            "status": status,
            "application_error": error,
            "updated_at": now,
        }
        if status == HITLInteractionStatus.APPLYING.value:
            # Preserve the fencing token; an immediate retry is enabled by an
            # expired lease, never by dropping ownership while still APPLYING.
            updates["application_lease_expires_at"] = now
        else:
            updates["application_claim_id"] = None
            updates["application_lease_expires_at"] = None
        if status == HITLInteractionStatus.APPLIED.value:
            updates["applied_at"] = now
        query: dict[str, Any] = {
            "interaction_id": interaction_id,
            "status": HITLInteractionStatus.APPLYING.value,
            "application_claim_id": claim_id,
        }
        if status == HITLInteractionStatus.APPLIED.value:
            query["run_projection_status"] = "applied"
        return await self._interactions.find_one_and_update(
            query,
            {"$set": updates, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def terminalize_interaction(
        self,
        interaction_id: str,
        *,
        expected_statuses: list[str],
        status: str,
        reason: str,
    ) -> dict[str, Any] | None:
        if status not in {
            HITLInteractionStatus.CANCELED.value,
            HITLInteractionStatus.EXPIRED.value,
            HITLInteractionStatus.FAILED.value,
        }:
            raise ValueError("invalid interaction terminal status")
        return await self._interactions.find_one_and_update(
            {
                "interaction_id": interaction_id,
                "status": {"$in": expected_statuses},
            },
            {
                "$set": {
                    "status": status,
                    "terminal_reason": reason,
                    "terminal_reconciled": False,
                    "application_claim_id": None,
                    "application_lease_expires_at": None,
                    "updated_at": utcnow(),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def mark_interaction_terminal_reconciled(
        self, interaction_id: str, *, version: int
    ) -> bool:
        return await self._interactions.update_one(
            {"interaction_id": interaction_id, "version": version},
            {
                "$set": {"terminal_reconciled": True, "updated_at": utcnow()},
                "$inc": {"version": 1},
            },
        )

    async def iter_due_interactions(
        self, now: datetime, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        rows = await self._interactions.find(
            {
                "status": {
                    "$in": [
                        HITLInteractionStatus.OPEN.value,
                        HITLInteractionStatus.PARTIALLY_ANSWERED.value,
                    ]
                },
                "expires_at": {"$ne": None, "$lte": now},
            },
            sort=[("expires_at", 1), ("interaction_id", 1)],
            limit=limit,
        )
        for row in rows:
            yield row

    async def iter_stale_applications(
        self, now: datetime, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        rows = await self._interactions.find(
            {
                "$or": [
                    {"status": HITLInteractionStatus.ANSWERS_RECORDED.value},
                    {
                        "status": HITLInteractionStatus.APPLYING.value,
                        "$or": [
                            {"application_lease_expires_at": {"$lte": now}},
                            {"application_lease_expires_at": None},
                            {"application_lease_expires_at": {"$exists": False}},
                        ],
                    },
                    {"status": HITLInteractionStatus.DELIVERY_UNCERTAIN.value},
                    {
                        "status": HITLInteractionStatus.APPLIED.value,
                        "terminal_reconciled": {"$ne": True},
                    },
                ]
            },
            sort=[("updated_at", 1), ("interaction_id", 1)],
            limit=limit,
        )
        for row in rows:
            yield row

    async def iter_active_interactions(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        rows = await self._interactions.find(
            {
                "status": {
                    "$in": [
                        HITLInteractionStatus.OPEN.value,
                        HITLInteractionStatus.PARTIALLY_ANSWERED.value,
                        HITLInteractionStatus.ANSWERS_RECORDED.value,
                        HITLInteractionStatus.APPLYING.value,
                        HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
                    ]
                }
            },
            sort=[("updated_at", 1), ("interaction_id", 1)],
            limit=limit,
        )
        for row in rows:
            yield row

    async def iter_unreconciled_terminal_requests(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        rows = await self._hitl_requests.find(
            {
                "status": {"$in": ["canceled", "expired"]},
                "cancellation_reconciled": {"$ne": True},
            },
            sort=[("created_at", 1), ("request_id", 1)],
            limit=limit,
        )
        for row in rows:
            yield row

    async def create_resume_command(
        self, command_data: dict[str, Any]
    ) -> dict[str, Any]:
        doc = dict(command_data)
        try:
            await self._resume_commands.insert_one(doc)
            return doc
        except DuplicateKeyError:
            existing = await self.get_resume_command_strict(doc["command_id"])
            if existing is None:
                raise
            immutable = (
                "kind",
                "interaction_id",
                "application_revision",
                "task_id",
                "context_id",
                "outbound_message_id",
                "orchestration_run_id",
                "answer_digest",
            )
            if any(existing.get(key) != doc.get(key) for key in immutable):
                raise ValueError("conflicting HITL resume command metadata") from None
            return existing

    async def get_resume_command_strict(self, command_id: str) -> dict[str, Any] | None:
        return await self._resume_commands.find_one({"command_id": command_id})

    async def get_resume_command_for_interaction_strict(
        self, interaction_id: str, application_revision: int
    ) -> dict[str, Any] | None:
        return await self._resume_commands.find_one(
            {
                "interaction_id": interaction_id,
                "application_revision": application_revision,
            }
        )

    async def claim_resume_command(
        self,
        command_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        now = utcnow()
        return await self._resume_commands.find_one_and_update(
            {
                "command_id": command_id,
                "status": {"$in": sorted(_COMMAND_CLAIMABLE)},
                "$or": [
                    {"next_attempt_at": None},
                    {"next_attempt_at": {"$exists": False}},
                    {"next_attempt_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": HITLResumeCommandStatus.DELIVERING.value,
                    "claim_id": claim_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempts": 1, "version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def renew_resume_command(
        self,
        command_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._resume_commands.update_one(
            {
                "command_id": command_id,
                "status": HITLResumeCommandStatus.DELIVERING.value,
                "claim_id": claim_id,
            },
            {
                "$set": {
                    "lease_expires_at": utcnow() + timedelta(seconds=lease_seconds),
                    "updated_at": utcnow(),
                },
                "$inc": {"version": 1},
            },
        )
        return bool(getattr(result, "matched_count", result))

    async def mark_resume_command_state(
        self,
        command_id: str,
        *,
        claim_id: str | None,
        expected_statuses: list[str],
        status: str,
        response_snapshot: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "command_id": command_id,
            "status": {"$in": expected_statuses},
        }
        if claim_id is not None:
            query["claim_id"] = claim_id
        now = utcnow()
        updates: dict[str, Any] = {
            "status": status,
            "claim_id": None,
            "lease_expires_at": None,
            "response_snapshot": response_snapshot,
            "error_code": error_code,
            "error_message": error_message,
            "updated_at": now,
        }
        if status == HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value:
            updates["uncertain_since"] = now
        if retry_after_seconds is not None:
            updates["next_attempt_at"] = now + timedelta(seconds=retry_after_seconds)
        return await self._resume_commands.find_one_and_update(
            query,
            {"$set": updates, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def mark_resume_command_aggregate_applied(self, command_id: str) -> bool:
        return await self._resume_commands.update_one(
            {
                "command_id": command_id,
                "status": HITLResumeCommandStatus.PROJECTED.value,
                "aggregate_applied_at": {"$exists": False},
            },
            {"$set": {"aggregate_applied_at": utcnow(), "updated_at": utcnow()}},
        )

    async def iter_due_resume_commands(
        self, now: datetime, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        rows = await self._resume_commands.find(
            {
                "$or": [
                    {
                        "status": HITLResumeCommandStatus.RETRYABLE_ERROR.value,
                        "next_attempt_at": {"$lte": now},
                    },
                    {"status": HITLResumeCommandStatus.ACKNOWLEDGED.value},
                    {
                        "status": HITLResumeCommandStatus.PROJECTED.value,
                        "aggregate_applied_at": {"$exists": False},
                    },
                    {"status": HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value},
                    {
                        "status": HITLResumeCommandStatus.DELIVERING.value,
                        "lease_expires_at": {"$lte": now},
                    },
                ]
            },
            sort=[("updated_at", 1), ("command_id", 1)],
            limit=limit,
        )
        for row in rows:
            yield row

    async def ensure_hitl_lifecycle_indexes(self) -> None:
        interaction_indexes = [
            ([("interaction_id", 1)], {"unique": True}),
            ([("room_id", 1), ("status", 1), ("expires_at", 1)], {}),
            ([("status", 1), ("application_lease_expires_at", 1)], {}),
            ([("orchestration_run_id", 1), ("status", 1)], {}),
        ]
        command_indexes = [
            ([("command_id", 1)], {"unique": True}),
            (
                [("interaction_id", 1), ("application_revision", 1), ("kind", 1)],
                {"unique": True},
            ),
            ([("status", 1), ("next_attempt_at", 1)], {}),
            ([("status", 1), ("lease_expires_at", 1)], {}),
        ]
        for collection, indexes in (
            (self._interactions, interaction_indexes),
            (self._resume_commands, command_indexes),
        ):
            for keys, options in indexes:
                await collection.create_index(keys, **options)
