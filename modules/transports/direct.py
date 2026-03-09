"""DirectTransport — cloud SSE/sync transport to cloud-reachable A2A agents.

Wraps the existing ``ResponseProcessor`` logic and integrates it with
``AgentResponseHandler`` for unified terminal-event handling.

Mid-stream SSE (``send_agent_token`` during streaming) stays inside
``DirectTransport`` via its own ``sse_manager`` reference — this is an
accepted asymmetry (see design doc §2.8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from modules.ResponseProcessor import ResponseProcessor
from modules.transports.base import AgentTransport

if TYPE_CHECKING:
    from modules.agent_response_handler import AgentResponseHandler
    from modules.dispatch_middleware import DispatchContext
    from models.processing import ProcessingResult
    from models.room import RoomAgentMessage

logger = get_logger(__name__)


class DirectTransport(AgentTransport):
    """Direct HTTP/SSE transport to cloud-reachable A2A agents.

    Delegates the actual streaming/sync logic to ``ResponseProcessor``
    (which it wraps), and replaces terminal notification calls with
    ``AgentEvent`` emissions through ``AgentResponseHandler``.
    """

    def __init__(
        self,
        response_handler: AgentResponseHandler,
        tsm,
        a2a_service,
        task_service,
        sse_manager,
        database_service,
    ) -> None:
        super().__init__(response_handler)
        self._rp = ResponseProcessor(
            tsm=tsm,
            sse_manager=sse_manager,
            a2a_service=a2a_service,
            task_service=task_service,
            database_service=database_service,
        )
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.a2a_service = a2a_service
        self.database_service = database_service

    @property
    def response_processor(self) -> ResponseProcessor:
        """Expose the underlying ResponseProcessor for callers that need it."""
        return self._rp

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        """Not used directly — AgentMessageProcessor calls handle_streaming/handle_sync."""
        raise NotImplementedError(
            "DirectTransport.dispatch is not used — "
            "AgentMessageProcessor calls handle_streaming_response / handle_sync_response directly"
        )

    async def handle_streaming_response(self, *args, **kwargs):
        """Delegate to ResponseProcessor."""
        return await self._rp.handle_streaming_response(*args, **kwargs)

    async def handle_sync_response(self, *args, **kwargs):
        """Delegate to ResponseProcessor."""
        return await self._rp.handle_sync_response(*args, **kwargs)
