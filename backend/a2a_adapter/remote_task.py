"""Adapter for fetching task state from remote A2A agents."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.types import AgentCard

from common.types import Task

from .card_data import sdk_agent_card_data
from .docker_host_fallback import with_docker_host_fallback
from .message_factory import from_sdk_task
from .task_requests import (
    build_get_task_request,
    extract_get_task_result,
    is_jsonrpc_error_response,
)

logger = logging.getLogger(__name__)


async def fetch_remote_task(
    agent_card_data: Any,
    task_id: str,
    *,
    timeout: float = 30.0,
) -> Task | None:
    """Fetch a task from a remote A2A agent and return an internal task."""
    try:
        from a2a.client import A2AClient

        card = AgentCard(**sdk_agent_card_data(agent_card_data))
        request = build_get_task_request(task_id)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await with_docker_host_fallback(
                card,
                lambda candidate: A2AClient(
                    client,
                    agent_card=candidate,
                ).get_task(request),
            )

        if not response or is_jsonrpc_error_response(response):
            logger.error(
                "Failed to get task from agent, error: %s",
                getattr(getattr(response, "root", None), "error", "Unknown error")
                if response
                else "No response",
            )
            return None

        result = extract_get_task_result(response)
        if result is None:
            logger.error("Failed to get task from agent, error: missing result")
            return None
        return from_sdk_task(result)
    except Exception as exc:
        logger.error("Failed to get task from agent: %s", exc, exc_info=True)
        return None


__all__ = ["fetch_remote_task"]
