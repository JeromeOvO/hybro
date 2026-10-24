"""Agent mention parsing and canonical mention building for room messages."""

from __future__ import annotations

import re
from typing import Any

from common.utils.logger import get_logger
from models.response import RoomCenterUserMessageResponse, ScopeResolutionError

logger = get_logger(__name__)

_MENTION_PATTERN = re.compile(r"<@([^|]+)\|([^>]+)>")


def parse_agent_mentions(message_text: str, room_agent_set: dict) -> list[dict]:
    """Parse ``<@agent_id|name>`` mentions of room members, in text order.

    The agent name comes from ``room_agent_set``; mentions of agents outside the
    room are logged and dropped.
    """
    mentions = []
    for match in _MENTION_PATTERN.finditer(message_text):
        agent_id = match.group(1).strip()
        position = match.start()
        if agent_id in room_agent_set:
            mentions.append(
                {
                    "agent_id": agent_id,
                    "agent_name": room_agent_set[agent_id],
                    "mention_text": match.group(0),
                    "position": position,
                }
            )
        else:
            logger.warning(
                "Inline mention %s not in room agent set — ignored", agent_id
            )
    mentions.sort(key=lambda x: x["position"])
    return mentions


def canonical_mentions_or_error(
    agents: list[Any], invalid_ids: list[str]
) -> list[dict] | RoomCenterUserMessageResponse:
    """Build canonical mention dicts, or the 400 response for invalid targets."""
    canonical_mentions = [
        {
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_card.name,
            "mention_text": f"<@{agent.agent_id}|{agent.agent_card.name}>",
        }
        for agent in agents
    ]
    if invalid_ids:
        error_msg = f"Invalid or unauthorized mention targets: {', '.join(invalid_ids)}"
        logger.warning(
            "Canonical mention targets rejected (invalid/unauthorized): %s",
            invalid_ids,
        )
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error=error_msg,
            scope_resolution_error=ScopeResolutionError(
                code="unauthorized_mention",
                message=error_msg,
            ),
            status_code=400,
        )
    return canonical_mentions


__all__ = ["canonical_mentions_or_error", "parse_agent_mentions"]
