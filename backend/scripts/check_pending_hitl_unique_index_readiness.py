#!/usr/bin/env python3
"""Check whether pending agent HITL uniqueness indexes can be created safely."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from pymongo import MongoClient

COLLECTION_NAME = "hitl_requests"
IDENTITY_FIELDS = ("display_message_id", "continuation_message_id")

EXIT_SUCCESS = 0
EXIT_DUPLICATES_FOUND = 1


def build_duplicate_pipeline(identity_field: str) -> list[dict[str, Any]]:
    """Return an aggregation pipeline for pending agent HITL duplicate groups."""
    if identity_field not in IDENTITY_FIELDS:
        raise ValueError(f"unsupported HITL identity field: {identity_field}")

    return [
        {
            "$match": {
                "status": "pending",
                "source": "agent",
                identity_field: {"$type": "string"},
            }
        },
        {
            "$group": {
                "_id": {
                    "room_id": "$room_id",
                    identity_field: f"${identity_field}",
                },
                "count": {"$sum": 1},
                "request_ids": {"$push": "$request_id"},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {
            "$project": {
                "_id": 0,
                "room_id": "$_id.room_id",
                identity_field: f"$_id.{identity_field}",
                "count": 1,
                "request_ids": 1,
            }
        },
        {"$sort": {"room_id": 1, identity_field: 1}},
    ]


def collect_duplicate_report(database: Any) -> dict[str, list[dict[str, Any]]]:
    collection = database[COLLECTION_NAME]
    return {
        field: list(collection.aggregate(build_duplicate_pipeline(field)))
        for field in IDENTITY_FIELDS
    }


def exit_code_for_report(report: dict[str, list[dict[str, Any]]]) -> int:
    if any(report.get(field) for field in IDENTITY_FIELDS):
        return EXIT_DUPLICATES_FOUND
    return EXIT_SUCCESS


def _default_mongodb_url() -> str:
    return (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URI")
        or "mongodb://127.0.0.1:27017/?replicaSet=rs0"
    )


def _default_db_name() -> str:
    return os.getenv("MONGODB_DB_NAME") or os.getenv("DB_NAME") or "hybro"


def _print_text_report(report: dict[str, list[dict[str, Any]]]) -> None:
    if exit_code_for_report(report) == EXIT_SUCCESS:
        print("Pending agent HITL uniqueness preflight passed: no duplicates found.")
        return

    print("Pending agent HITL uniqueness preflight failed: duplicates found.")
    print(
        "Resolve these pending duplicate HITL rows before creating unique partial "
        "indexes on hitl_requests."
    )
    for field in IDENTITY_FIELDS:
        duplicates = report.get(field) or []
        if not duplicates:
            continue
        print(f"\nDuplicate groups for {field}:")
        for duplicate in duplicates:
            identity_value = duplicate.get(field)
            request_ids = ", ".join(
                str(value) for value in duplicate.get("request_ids", [])
            )
            print(
                f"- room_id={duplicate.get('room_id')} {field}={identity_value} "
                f"count={duplicate.get('count')} request_ids=[{request_ids}]"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect legacy duplicate pending agent HITL rows that would block "
            "pending HITL unique partial indexes."
        )
    )
    parser.add_argument(
        "--uri",
        default=_default_mongodb_url(),
        help="MongoDB URI. Defaults to MONGODB_URL, MONGO_URI, or local replica set.",
    )
    parser.add_argument(
        "--database",
        default=_default_db_name(),
        help="MongoDB database name. Defaults to MONGODB_DB_NAME, DB_NAME, or hybro.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    args = parser.parse_args(argv)

    client = MongoClient(args.uri)
    try:
        report = collect_duplicate_report(client[args.database])
    finally:
        client.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_text_report(report)

    return exit_code_for_report(report)


if __name__ == "__main__":
    sys.exit(main())
