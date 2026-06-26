from __future__ import annotations

from typing import Any

from a2a_adapter.remote_task import fetch_remote_task
from common.types import AgentCard, Task


class RemoteTaskReader:
    async def get_task_from_agent(
        self,
        agent_card: AgentCard | dict[str, Any],
        task_id: str,
        *,
        agent_id: str | None = None,
    ) -> Task | None:
        del agent_id
        return await fetch_remote_task(agent_card, task_id)


__all__ = ["RemoteTaskReader"]
