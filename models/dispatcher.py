from __future__ import annotations

from pydantic import BaseModel

from models.agent import Agent


class AssignResult(BaseModel):
    """Result of ``assign_agent``.

    Replaces the old ``self._last_resolve_failure`` pattern which stored the
    failure reason on the singleton instance — a concurrency hazard when
    multiple asyncio tasks process different rooms simultaneously (Issue 16).
    """

    agent: Agent | None
    failure_reason: str | None = None
