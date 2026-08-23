"""Shared job name constants for leader election.

Import these instead of using string literals to prevent typo-related bugs.
Keep this list in sync with the ``release_all`` call in ``main.py``.
"""

STALE_TASK_CHECKER = "stale_task_checker"
COMPACTION_SWEEP = "compaction_sweep"
ORPHANED_UPLOAD_CLEANER = "orphaned_upload_cleaner"
AGENT_HEALTH_CHECKER = "agent_health_checker"
ORCHESTRATOR_RECOVERY = "orchestrator_recovery"
ORCHESTRATOR_PROJECTION = "orchestrator_projection"
ORCHESTRATOR_CANARY = "orchestrator_canary"

ALL_JOB_NAMES: list[str] = [
    STALE_TASK_CHECKER,
    COMPACTION_SWEEP,
    ORPHANED_UPLOAD_CLEANER,
    AGENT_HEALTH_CHECKER,
    ORCHESTRATOR_RECOVERY,
    ORCHESTRATOR_PROJECTION,
    ORCHESTRATOR_CANARY,
]
