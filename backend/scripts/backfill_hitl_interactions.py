"""Readiness report and optional backfill for Milestone-2 HITL interactions.

Dry-run is the default. Supply ``--apply`` explicitly to write conflict-free
aggregates. The service also synthesizes aggregates lazily, so this script is
operational tooling rather than a blocking startup migration.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient, UpdateOne


def _interaction_id(row: dict[str, Any]) -> str:
    return str(row.get("group_id") or row.get("interaction_id") or row["request_id"])


def build_backfill_plan(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_interaction_id(row)].append(row)
    aggregates: list[dict] = []
    conflicts: list[dict] = []
    now = datetime.now(UTC)
    for interaction_id, members in sorted(grouped.items()):
        members.sort(key=lambda row: (row.get("group_index") or 0, row["request_id"]))
        first = members[0]
        immutable_values = {
            field: {row.get(field) for row in members}
            for field in (
                "room_id",
                "user_message_id",
                "source",
                "orchestration_run_id",
            )
        }
        disagreement = {
            field: sorted(str(value) for value in values)
            for field, values in immutable_values.items()
            if len(values) > 1
        }
        expected = int(first.get("group_total") or 1)
        totals = {int(row.get("group_total") or 1) for row in members}
        request_ids = [row.get("request_id") for row in members]
        indices = [row.get("group_index") for row in members]
        invalid_indices = expected > 1 and any(
            not isinstance(index, int) or not 0 <= index < expected for index in indices
        )
        incomplete_indices = (
            expected > 1
            and len(members) >= expected
            and set(indices) != set(range(expected))
        )
        incompatible_ids = expected > 1 and any(
            row.get("group_id") != interaction_id
            or (
                row.get("interaction_id") is not None
                and row.get("interaction_id") != interaction_id
            )
            for row in members
        )
        structural_conflicts = {
            "group_total": sorted(totals) if totals != {expected} else None,
            "duplicate_request_ids": (
                request_ids if len(set(request_ids)) != len(request_ids) else None
            ),
            "invalid_group_indices": indices if invalid_indices else None,
            "incomplete_group_indices": indices if incomplete_indices else None,
            "incompatible_interaction_ids": incompatible_ids or None,
        }
        structural_conflicts = {
            key: value
            for key, value in structural_conflicts.items()
            if value is not None
        }
        if disagreement or structural_conflicts or len(members) > expected:
            conflicts.append(
                {
                    "interaction_id": interaction_id,
                    "conflicting_fields": {
                        **disagreement,
                        **structural_conflicts,
                    },
                    "request_ids": [row["request_id"] for row in members],
                    "expected_request_count": expected,
                }
            )
            continue
        statuses = {row.get("status", "pending") for row in members}
        answer_ids = [
            row["request_id"]
            for row in members
            if row.get("status") in {"answer_recorded", "processing", "responded"}
        ]
        if statuses == {"responded"}:
            status = "applied"
        elif "processing" in statuses:
            status = "answers_recorded"
        elif "expired" in statuses:
            status = "expired"
        elif "canceled" in statuses:
            status = "canceled"
        elif answer_ids:
            status = "partially_answered"
        elif len(members) == expected:
            status = "open"
        else:
            status = "materializing"
        expiries = [row["expires_at"] for row in members if row.get("expires_at")]
        aggregates.append(
            {
                "schema_version": 2,
                "interaction_id": interaction_id,
                "room_id": first.get("room_id"),
                "user_message_id": first.get("user_message_id"),
                "orchestration_run_id": first.get("orchestration_run_id"),
                "source": first.get("source"),
                "request_ids": [row["request_id"] for row in members],
                "expected_request_count": expected,
                "required_request_ids": [row["request_id"] for row in members],
                "status": status,
                "version": 1,
                "expires_at": min(expiries) if expiries else None,
                "answer_request_ids": answer_ids,
                "answer_refs": [
                    {
                        "request_id": row["request_id"],
                        "digest": row.get("answer_digest")
                        or hashlib.sha256(
                            str(row.get("user_input") or "").encode("utf-8")
                        ).hexdigest(),
                    }
                    for row in members
                    if row["request_id"] in answer_ids
                ],
                "application_revision": 1 if status == "applied" else 0,
                "application_attempts": 0,
                "run_projection_status": "pending",
                "applied_at": now if status == "applied" else None,
                # Applied legacy interactions still require idempotent run/UI
                # projection replay before terminal reconciliation is complete.
                "terminal_reconciled": False,
                "created_at": min(
                    (row.get("created_at") or now for row in members), default=now
                ),
                "updated_at": now,
            }
        )
    return aggregates, conflicts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = MongoClient(args.mongo_uri)
    database = client[args.database]
    rows = list(database.hitl_requests.find({}))
    aggregates, conflicts = build_backfill_plan(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry-run",
            "requests": len(rows),
            "interactions": len(aggregates),
            "conflicts": len(conflicts),
        }
    )
    for conflict in conflicts:
        print({"conflict": conflict})
    if args.apply and aggregates:
        operations = [
            UpdateOne(
                {"interaction_id": doc["interaction_id"]},
                {"$setOnInsert": doc},
                upsert=True,
            )
            for doc in aggregates
        ]
        result = database.hitl_interactions.bulk_write(operations, ordered=False)
        print({"inserted": result.upserted_count, "existing": result.matched_count})
    return 2 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
