from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from common.utils.time import utcnow
from jobs.stale_task_checker import (
    StaleCancellationReconciliationDeps,
    StaleTaskChecker,
)


@pytest.mark.asyncio
async def test_reconcile_pending_cancellations_uses_orphan_threshold_cutoff():
    reconciliation = AsyncMock()
    checker = StaleTaskChecker(orphan_threshold_minutes=7)
    checker.set_cancellation_reconciliation_deps(
        StaleCancellationReconciliationDeps(reconciliation=reconciliation)
    )
    earliest_cutoff = utcnow() - timedelta(minutes=7)

    await checker._reconcile_pending_cancellations()

    latest_cutoff = utcnow() - timedelta(minutes=7)
    reconciliation.reconcile_pending.assert_awaited_once()
    settle_cutoff = reconciliation.reconcile_pending.await_args.kwargs["settle_cutoff"]
    assert earliest_cutoff <= settle_cutoff <= latest_cutoff
