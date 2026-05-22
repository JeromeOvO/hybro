from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


LEGACY_WORKFLOW_COLLECTIONS = [
    "base_tasks",
    "meta_tasks",
    "task_sessions",
    "chat_contexts",
]


@dataclass(frozen=True)
class CleanupReadiness:
    cleanup_allowed: bool
    collections: list[str]
    blockers: list[str]


def assess_cleanup_readiness(root: Path) -> CleanupReadiness:
    blockers = [
        rel
        for rel in ["api/orchestration_center.py", "api/task.py"]
        if (root / rel).exists()
    ]
    return CleanupReadiness(
        cleanup_allowed=not blockers,
        collections=list(LEGACY_WORKFLOW_COLLECTIONS),
        blockers=blockers,
    )


async def run_cleanup_if_ready(root: Path, mongo, *, enabled: bool = False) -> CleanupReadiness:
    readiness = assess_cleanup_readiness(root)
    if not enabled or not readiness.cleanup_allowed:
        return readiness
    for name in LEGACY_WORKFLOW_COLLECTIONS:
        await mongo.collection(name).delete_many({})
    return readiness


__all__ = [
    "CleanupReadiness",
    "LEGACY_WORKFLOW_COLLECTIONS",
    "assess_cleanup_readiness",
    "run_cleanup_if_ready",
]
