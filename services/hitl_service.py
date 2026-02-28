"""Human-in-the-Loop (HITL) service — manages the HITL request/response lifecycle.

Responsibilities:
1. Create HITL requests (triggered by SupervisorExecutor on CLARIFY or agent input_required)
2. Persist requests to MongoDB
3. Emit SSE events to notify the frontend
4. Handle user responses (route to A2A agent or supervisor context)
5. Clean up expired/canceled requests

See docs/HITL_DESIGN.md §6 for full design details.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException

from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.hitl import (
    HITLEventType,
    HITLPromptType,
    HITLRequest,
    HITLStatus,
)

if TYPE_CHECKING:
    from typing import Literal

logger = get_logger(__name__)

MAX_HITL_ROUNDS = 3


class HITLService:
    """Manages the human-in-the-loop interaction lifecycle."""

    def __init__(self) -> None:
        # Lazy imports to avoid circular dependencies at module load time.
        # Resolved on first method call.
        self._db_service = None
        self._sse_manager = None
        self._a2a_service = None

    @property
    def database_service(self):
        if self._db_service is None:
            from services.database_service import db_service
            self._db_service = db_service
        return self._db_service

    @property
    def sse_manager(self):
        if self._sse_manager is None:
            from services.sse_services import sse_manager
            self._sse_manager = sse_manager
        return self._sse_manager

    @property
    def a2a_service(self):
        if self._a2a_service is None:
            from services.a2a_service import a2a_service
            self._a2a_service = a2a_service
        return self._a2a_service

    # ------------------------------------------------------------------
    # Create HITL request
    # ------------------------------------------------------------------

    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: Literal["agent", "supervisor"],
        prompt: str,
        prompt_type: HITLPromptType = HITLPromptType.TEXT,
        choices: list[str] | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        source_step_id: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        expires_in_hours: float = 24.0,
    ) -> HITLRequest | None:
        """Create and emit an HITL request.

        Returns the created request, or None if max rounds exceeded.
        """
        if continuation_message_id:
            existing = await self.database_service.count_hitl_requests_for_message(
                continuation_message_id
            )
            if existing >= MAX_HITL_ROUNDS:
                logger.warning(
                    "Max HITL rounds (%d) exceeded for message %s",
                    MAX_HITL_ROUNDS,
                    continuation_message_id,
                )
                return None

        request = HITLRequest(
            room_id=room_id,
            user_message_id=user_message_id,
            source=source,
            prompt=prompt,
            prompt_type=prompt_type,
            choices=choices,
            agent_id=agent_id,
            agent_name=agent_name,
            source_step_id=source_step_id,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
            continuation_message_id=continuation_message_id,
            expires_at=utcnow() + timedelta(hours=expires_in_hours),
        )

        # 1. Persist FIRST (so it survives SSE drops)
        doc = request.model_dump(mode="json")
        saved = await self.database_service.create_hitl_request(doc)
        if not saved:
            logger.error(
                "Failed to persist HITL request %s", request.request_id
            )
            return None

        # 2. Emit SSE event
        await self._emit_hitl_event(
            room_id=room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=request,
        )

        logger.info(
            "hitl_request_created",
            extra={
                "hitl_request_id": request.request_id,
                "hitl_source": source,
                "hitl_prompt_type": prompt_type,
                "room_id": room_id,
            },
        )
        return request

    # ------------------------------------------------------------------
    # Handle user response
    # ------------------------------------------------------------------

    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict:
        """Handle user's reply to an HITL request.

        Uses a fenced two-phase CAS pattern:
          pending -> processing  (atomic claim, generates claim_id)
          processing -> responded  (fenced by claim_id)
        All writes after the claim are fenced by claim_id so that if
        recovery reclaims the request and another worker re-claims it
        with a new claim_id, the original worker's writes are no-ops.
        """
        from uuid import uuid4

        claim_id = uuid4().hex

        # Phase 1: Atomically claim pending -> processing with claim_id
        claimed_doc = await self.database_service.claim_hitl_request(
            request_id,
            status=HITLStatus.PROCESSING.value,
            claim_id=claim_id,
            user_input=user_input,
            responded_at=utcnow(),
            responded_by_user_id=user_id,
        )
        if not claimed_doc:
            doc = await self.database_service.get_hitl_request(request_id)
            if not doc:
                raise HTTPException(404, "HITL request not found")
            raise HTTPException(409, f"Request already {doc.get('status', 'unknown')}")

        request = HITLRequest(**{k: v for k, v in claimed_doc.items() if k != "_id"})
        if request.room_id != room_id:
            await self.database_service.fenced_update_hitl_request(
                request_id, claim_id,
                status=HITLStatus.PENDING.value,
                claim_id=None,
                user_input=None, responded_at=None, responded_by_user_id=None,
            )
            raise HTTPException(403, "Room mismatch")

        # Phase 2: Route — revert to PENDING on failure (fenced).
        # A background heartbeat task bumps responded_at every
        # LEASE_HEARTBEAT_SECONDS while routing is in-flight, preventing
        # the stale checker from reclaiming a legitimately long-running route.
        import asyncio

        async def _lease_heartbeat() -> None:
            while True:
                await asyncio.sleep(self.LEASE_HEARTBEAT_SECONDS)
                await self.database_service.fenced_update_hitl_request(
                    request_id, claim_id,
                    responded_at=utcnow(),
                )

        heartbeat_task = asyncio.create_task(_lease_heartbeat())
        try:
            if request.source == "agent":
                await self._handle_agent_response(request, user_input)
            elif request.source == "supervisor":
                await self._handle_supervisor_response(request, user_input)
        except HTTPException:
            await self.database_service.fenced_update_hitl_request(
                request_id, claim_id,
                status=HITLStatus.PENDING.value,
                claim_id=None,
                user_input=None, responded_at=None, responded_by_user_id=None,
            )
            raise
        except Exception as exc:
            logger.error(
                "HITL routing failed for request %s: %s",
                request_id,
                exc,
                exc_info=True,
            )
            await self.database_service.fenced_update_hitl_request(
                request_id, claim_id,
                status=HITLStatus.PENDING.value,
                claim_id=None,
                user_input=None, responded_at=None, responded_by_user_id=None,
            )
            await self._emit_hitl_event(
                room_id=room_id,
                event_type=HITLEventType.ERROR,
                request=request,
                error=str(exc),
            )
            raise HTTPException(
                502,
                f"Failed to deliver response to {request.source}: {exc}",
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # Phase 2b: Stamp routing_completed_at (fenced).
        # If our claim was reclaimed, this is a no-op — we bail.
        stamped = await self.database_service.fenced_update_hitl_request(
            request_id, claim_id,
            routing_completed_at=utcnow(),
        )
        if not stamped:
            logger.warning(
                "HITL request %s claim_id %s no longer matches — "
                "reclaimed by recovery. Abandoning finalization.",
                request_id, claim_id,
            )
            return {"status": "ok", "request_id": request_id, "reclaimed": True}

        # Phase 3: Finalize processing -> responded (fenced).
        finalized = await self.database_service.fenced_update_hitl_request(
            request_id, claim_id,
            status=HITLStatus.RESPONDED.value,
        )
        if not finalized:
            finalized = await self.database_service.fenced_update_hitl_request(
                request_id, claim_id,
                status=HITLStatus.RESPONDED.value,
            )
        if not finalized:
            logger.critical(
                "Failed to finalize HITL request %s (claim %s) to responded "
                "after retry. recover_stale_processing will finalize it via "
                "routing_completed_at.",
                request_id, claim_id,
            )

        await self._emit_hitl_event(
            room_id=room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=request,
        )

        logger.info(
            "hitl_response_handled",
            extra={
                "hitl_request_id": request_id,
                "hitl_source": request.source,
                "room_id": room_id,
                "hitl_status": HITLStatus.RESPONDED,
            },
        )
        return {"status": "ok", "request_id": request_id}

    # ------------------------------------------------------------------
    # Agent response routing
    # ------------------------------------------------------------------

    async def _handle_agent_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Send user's reply to the waiting A2A agent."""
        # Reset last_notified_state so multi-round input_required works
        await self.database_service.reset_last_notified_state(
            request.continuation_message_id
        )

        await self.a2a_service.reply_to_task(
            message_id=request.continuation_message_id,
            task_id=request.a2a_task_id,
            context_id=request.a2a_context_id,
            user_input=user_input,
        )
        # Agent will process and send webhook → resume_queue_from_continuation
        # which routes to _resume_supervisor_v2(kind=HITL_AGENT)

    # ------------------------------------------------------------------
    # Supervisor response routing
    # ------------------------------------------------------------------

    async def _handle_supervisor_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Resume V2 supervisor loop with user's answer injected into trajectory."""
        from modules.RoomMessageCenter import room_message_center

        continuation = await self.database_service.get_pending_continuation_on_message(
            request.continuation_message_id
        )
        if not continuation:
            raise RuntimeError(
                f"No continuation found for message {request.continuation_message_id} — "
                "the supervisor loop may have already been cleaned up or recovered"
            )

        if continuation.get("supervisor_v2"):
            traj = continuation.get("trajectory", {})
            traj["hitl_user_reply"] = user_input
            traj["hitl_original_message_id"] = continuation.get("user_message_id")
            continuation["trajectory"] = traj

            saved = await self.database_service.save_continuation_on_user_message(
                request.continuation_message_id, continuation
            )
            if not saved:
                raise RuntimeError(
                    f"Failed to persist patched continuation for message "
                    f"{request.continuation_message_id} — user reply would be lost"
                )

        resumed = await room_message_center.resume_queue_from_continuation(
            message_id=request.continuation_message_id,
            task_result_text=None,
        )
        if not resumed:
            raise RuntimeError(
                f"Supervisor resume failed for message {request.continuation_message_id} — "
                "continuation is preserved for retry"
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_pending_requests(self, room_id: str) -> list[HITLRequest]:
        """Get all pending HITL requests for a room (SSE reconnect catch-up)."""
        docs = await self.database_service.get_pending_hitl_requests(room_id)
        return [
            HITLRequest(**{k: v for k, v in d.items() if k != "_id"})
            for d in docs
        ]

    async def get_pending_requests_for_message(
        self, user_message_id: str
    ) -> list[HITLRequest]:
        """Get pending HITL requests associated with a specific user message."""
        docs = await self.database_service.get_pending_hitl_requests_for_message(
            user_message_id
        )
        return [
            HITLRequest(**{k: v for k, v in d.items() if k != "_id"})
            for d in docs
        ]

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_request(
        self, request_id: str, room_id: str | None = None
    ) -> None:
        """Cancel a pending HITL request."""
        doc = await self.database_service.get_hitl_request(request_id)
        if not doc:
            raise HTTPException(404, "HITL request not found")
        request = HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
        if room_id is not None and request.room_id != room_id:
            raise HTTPException(403, "Room mismatch")
        if request.status != HITLStatus.PENDING:
            return  # Already resolved, no-op

        await self.database_service.update_hitl_request(
            request_id, status=HITLStatus.CANCELED
        )

        # Clear the orphaned continuation
        if request.continuation_message_id:
            await self.database_service.get_and_clear_continuation_on_message(
                request.continuation_message_id
            )
            # Also try clearing from user messages (HITL_SUPERVISOR)
            await self.database_service.get_and_clear_continuation_on_user_message(
                request.continuation_message_id
            )

        # Notify frontend
        await self._emit_hitl_event(
            room_id=request.room_id,
            event_type=HITLEventType.INPUT_CANCELED,
            request=request,
        )

        logger.info(
            "hitl_request_canceled",
            extra={
                "hitl_request_id": request_id,
                "room_id": request.room_id,
            },
        )

    async def cancel_requests_for_message(
        self, user_message_id: str
    ) -> None:
        """Cancel all pending HITL requests for a given user message."""
        pending = await self.get_pending_requests_for_message(user_message_id)
        for req in pending:
            await self.cancel_request(req.request_id)

    PROCESSING_TIMEOUT_SECONDS = 600
    LEASE_HEARTBEAT_SECONDS = 120

    async def recover_stale_processing(self) -> int:
        """Recover HITL requests stuck in 'processing' after a crash.

        For each stale request, checks the ``routing_completed_at`` field
        (set by handle_response immediately after successful routing):
        - Field is set   -> finalize to 'responded' (routing succeeded,
          only the status write was lost)
        - Field is absent -> revert to 'pending' (routing never completed,
          safe to let the user retry)

        All writes use CAS (expected_status='processing') to prevent
        overwriting a newer state. Reverts also clear ``claim_id`` so the
        original worker's fenced writes become no-ops.
        """
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=self.PROCESSING_TIMEOUT_SECONDS)
        cursor = self.database_service.mongo.db.hitl_requests.find(
            {"status": "processing", "responded_at": {"$lt": cutoff}},
        )
        recovered = 0
        async for doc in cursor:
            req_id = doc.get("request_id")
            routing_done = doc.get("routing_completed_at") is not None

            if routing_done:
                ok = await self.database_service.cas_update_hitl_request(
                    req_id,
                    expected_status=HITLStatus.PROCESSING.value,
                    status=HITLStatus.RESPONDED.value,
                )
                if ok:
                    logger.warning(
                        "Finalized stale PROCESSING HITL request %s to RESPONDED "
                        "(routing_completed_at is set)",
                        req_id,
                    )
                    recovered += 1
                else:
                    logger.info(
                        "Skipped recovery of HITL request %s — status already changed",
                        req_id,
                    )
            else:
                ok = await self.database_service.cas_update_hitl_request(
                    req_id,
                    expected_status=HITLStatus.PROCESSING.value,
                    status=HITLStatus.PENDING.value,
                    claim_id=None,
                    routing_completed_at=None,
                    user_input=None,
                    responded_at=None,
                    responded_by_user_id=None,
                )
                if ok:
                    logger.warning(
                        "Reverted stale PROCESSING HITL request %s to PENDING "
                        "(routing never completed)",
                        req_id,
                    )
                    recovered += 1
                else:
                    logger.info(
                        "Skipped recovery of HITL request %s — status already changed",
                        req_id,
                    )

        if recovered:
            logger.warning(
                "Recovered %d stale PROCESSING HITL requests (threshold: %ds)",
                recovered,
                self.PROCESSING_TIMEOUT_SECONDS,
            )
        return recovered

    # ------------------------------------------------------------------
    # SSE emission helper
    # ------------------------------------------------------------------

    async def _emit_hitl_event(
        self,
        room_id: str,
        event_type: HITLEventType,
        request: HITLRequest,
        error: str | None = None,
    ) -> None:
        """Emit an HITL lifecycle event via SSE."""
        data: dict = {
            "request_id": request.request_id,
            "message_id": (
                request.continuation_message_id or request.user_message_id
            ),
            "source": request.source,
        }

        if event_type == HITLEventType.INPUT_REQUESTED:
            message_type = "hitl_input_requested"
            data.update({
                "prompt": request.prompt,
                "prompt_type": request.prompt_type,
                "choices": request.choices,
                "agent_id": request.agent_id,
                "agent_name": request.agent_name,
                "source_step_id": request.source_step_id,
            })
        else:
            message_type = "hitl_status_update"
            _status_map = {
                HITLEventType.INPUT_RECEIVED: HITLStatus.RESPONDED.value,
                HITLEventType.INPUT_EXPIRED: HITLStatus.EXPIRED.value,
                HITLEventType.INPUT_CANCELED: HITLStatus.CANCELED.value,
                HITLEventType.ERROR: "error",
            }
            data["status"] = _status_map.get(event_type, request.status)
            if error:
                data["error_message"] = error

        await self.sse_manager.broadcast_to_room(
            room_id, message_type, data
        )


hitl_service = HITLService()
