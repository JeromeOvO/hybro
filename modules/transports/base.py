"""AgentTransport — abstract base class for all agent transport mechanisms.

Subclasses own the *how* of talking to agents.
``AgentResponseHandler`` owns the *what* of processing results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.processing import ProcessingResult
    from models.room import RoomAgentMessage
    from modules.agent_response_handler import AgentResponseHandler
    from modules.dispatch_middleware import DispatchContext


class AgentTransport(ABC):
    """Base class for all agent transport mechanisms."""

    def __init__(self, response_handler: AgentResponseHandler) -> None:
        self.response_handler = response_handler

    @abstractmethod
    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        """Send message to agent and process results via response_handler."""
        ...
