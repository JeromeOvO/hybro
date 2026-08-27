import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from common.dto import (
    AgentMessageFinal,
    DeliveryEmitStatus,
    DeliveryEvent,
    HITLRequestEvent,
    HITLResolvedEvent,
    ProcessingStatusEvent,
    RunEventNotification,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)
from common.observability import (
    MetricsCollector,
    NoopMetricsCollector,
    get_current_trace_id,
    trace_id_context,
    traced_create_task,
)
from delivery.config import DeliveryConfig
from delivery.room_events import RoomEventStore
from delivery.sse.deduplication import (
    DeliveryReservation,
    DeliveryReservationStatus,
    TerminalStatusDeduplicator,
)
from delivery.sse.manager import SSETransportImpl
from delivery.translator import to_sse_frame


class ProjectionSettlementReader(Protocol):
    """Reads terminal-fact settlement from the private run fact log.

    Defense-in-depth for the Room Stream Snapshot plan's terminal gating
    (§4 / §5): the publisher asks this reader whether the fact behind a
    terminal frame still has durable side-effect steps in ``{pending,
    running}`` before persisting/emitting it. The primary gate remains the
    two-phase ``TerminalProjectionFinalizer``; this reader only blocks when
    the projection has not settled.
    """

    async def is_terminal_settled(self, event: DeliveryEvent) -> bool: ...


def _frame_data_digest(frame_data: dict[str, Any]) -> str:
    canonical = json.dumps(frame_data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class EventPublisherImpl:
    def __init__(
        self,
        *,
        sse_transport: SSETransportImpl,
        event_bus: Any,
        deduplicator: TerminalStatusDeduplicator,
        config: DeliveryConfig,
        now: Callable[[], datetime],
        instance_id: str,
        metrics: MetricsCollector | None = None,
        room_events: RoomEventStore | None = None,
        projection_settlement: ProjectionSettlementReader | None = None,
    ) -> None:
        self.sse_transport = sse_transport
        self.event_bus = event_bus
        self.deduplicator = deduplicator
        self.config = config
        self._now = now
        self.instance_id = instance_id
        self._metrics = metrics or NoopMetricsCollector()
        self.room_events = room_events
        self.projection_settlement = projection_settlement
        # Publisher-maintained per-stream monotonic counters used to build
        # deterministic idempotency keys for non-terminal events (plan §5).
        # Keyed by entity/stream identity, never a DTO field or caller kwarg.
        self._stream_counters: dict[str, int] = {}
        self.dead_letters: deque[dict[str, Any]] = deque(
            maxlen=config.dead_letter_memory_maxlen
        )

    async def emit(self, event: DeliveryEvent) -> bool:
        """Compatibility API: only a fresh confirmed delivery returns ``True``."""
        status, _ = await self.emit_checked_identified(event)
        return status == DeliveryEmitStatus.DELIVERED

    async def emit_checked(  # noqa: C901
        self, event: DeliveryEvent
    ) -> DeliveryEmitStatus:
        status, _ = await self.emit_checked_identified(event)
        return status

    async def emit_checked_identified(  # noqa: C901
        self, event: DeliveryEvent, *, parent_event_id: str | None = None
    ) -> tuple[DeliveryEmitStatus, str | None]:
        """Persist-before-broadcast emit returning the persisted room event id.

        Callers that need to reference the persisted event later (to pass its
        ``room_event_id`` as a child's ``parent_event_id``) use this instead of
        ``emit_checked``. Existing signatures/return types are unchanged.
        """

        reservation: DeliveryReservation | None = None
        try:
            reservation_status, reservation = await self._reserve_typed_delivery(event)
            if reservation_status == DeliveryReservationStatus.IN_FLIGHT:
                return DeliveryEmitStatus.IN_FLIGHT, None
            if reservation_status == DeliveryReservationStatus.ALREADY_DELIVERED:
                self._increment(
                    "hybro_delivery_events_deduplicated_total",
                    {"event_type": event.event_type},
                )
                return DeliveryEmitStatus.ALREADY_DELIVERED, None
            if reservation_status is None and not await self._should_deliver_typed(
                event
            ):
                self._increment(
                    "hybro_delivery_events_deduplicated_total",
                    {"event_type": event.event_type},
                )
                return DeliveryEmitStatus.DEDUPLICATED, None

            timestamp = event.timestamp or self._now()

            # ── Terminal settlement gate (defense-in-depth) ──────────────
            # A terminal run_event/processing_status frame is only emitted once
            # every durable side-effect step of its fact is completed or
            # blocked. The two-phase finalizer is the primary gate; a blocked
            # emit here returns FAILED so the finalizer retries the step.
            settlement_state = await self._settlement_state(event)
            if settlement_state == "pending" and self._settlement_gate_blocked(event):
                if reservation is not None:
                    await self._release_typed_delivery(event, reservation)
                    reservation = None
                return DeliveryEmitStatus.FAILED, None

            frame = to_sse_frame(event, timestamp=timestamp)
            trace_id = getattr(event, "trace_id", None) or get_current_trace_id()
            frame, room_seq, room_event_id = await self._persist_frame(
                event,
                frame,
                trace_id=trace_id,
                parent_event_id=parent_event_id,
                settlement_state=settlement_state,
                timestamp=timestamp,
            )
        except Exception as exc:
            if reservation is not None:
                await self._release_typed_delivery(event, reservation)
            await self._dead_letter("translate", event, exc)
            return DeliveryEmitStatus.FAILED, None

        self._increment(
            "hybro_delivery_events_emitted_total",
            {"event_type": frame["type"]},
        )
        delivered, lease_owned = await self._deliver_with_reservation(
            event,
            frame,
            trace_id=trace_id,
            reservation=reservation,
        )
        if not delivered:
            await self._release_typed_delivery(event, reservation)
            return DeliveryEmitStatus.FAILED, room_event_id

        # Transport acceptance is the delivery boundary. Losing the lease or
        # Redis confirmation after fanout must not make durable reconciliation
        # send the already-accepted terminal frame again.
        if reservation is not None:
            confirmed = False
            if lease_owned and reservation.l2_owned:
                try:
                    confirmed = await self.deduplicator.confirm(reservation)
                except Exception:
                    confirmed = False
            if not confirmed:
                await self.deduplicator.mark_delivered_after_acceptance(
                    reservation,
                    status=self._dedup_status(event),
                )
        return DeliveryEmitStatus.DELIVERED, room_event_id

    async def _persist_frame(
        self,
        event: DeliveryEvent,
        frame: dict[str, Any],
        *,
        trace_id: str | None,
        parent_event_id: str | None,
        settlement_state: str | None,
        timestamp: datetime,
    ) -> tuple[dict[str, Any], int | None, str | None]:
        """Persist-before-broadcast (§5): write the room_events doc BEFORE the
        delivery layer fans out the frame. Delivery never precedes durability.

        Returns the frame (with room_seq/room_event_id threaded in when the
        store is bound), the assigned room_seq, and the persisted event id.
        """

        self._inject_typed_trace_id(frame, trace_id)
        if self.room_events is None:
            return frame, None, None
        frame_data = dict(frame["data"])
        persist_state = settlement_state or "settled"
        append = await self.room_events.append(
            room_id=event.room_id,
            kind=frame["type"],
            payload_public=frame_data,
            event_id=self._persisted_event_id(event),
            idempotency_key=self._idempotency_key(event, frame_data),
            parent_event_id=parent_event_id,
            run_id=getattr(event, "run_id", None),
            persist_state=persist_state,
            ts=timestamp,
        )
        if not append.persisted:
            return frame, None, None
        frame = to_sse_frame(
            event,
            timestamp=timestamp,
            room_seq=append.room_seq,
            room_event_id=append.room_event_id,
            parent_event_id=parent_event_id,
        )
        self._inject_typed_trace_id(frame, trace_id)
        return frame, append.room_seq, append.room_event_id

    async def _settlement_state(self, event: DeliveryEvent) -> str | None:
        """Resolve the persist_state label for terminal-typed events.

        Returns ``"settled"`` / ``"pending"`` when a settlement reader is
        bound and the event is terminal-typed; ``None`` otherwise (persistence
        then defaults to ``settled``).
        """

        if self.projection_settlement is None:
            return None
        if not self._is_terminal_typed(event):
            return None
        try:
            settled = await self.projection_settlement.is_terminal_settled(event)
        except Exception:
            # Reader failures must never dead-letter the emit: fall back to
            # the settled label and let the finalizer gate hold.
            return "settled"
        return "settled" if settled else "pending"

    def _settlement_gate_blocked(self, event: DeliveryEvent) -> bool:
        """True when a terminal run_event/processing_status frame must wait.

        Only the two gated kinds are blocked here; terminal task_update frames
        (descendant_cleanup / system_task_delivery) remain gated by their
        per-step dependencies, not by this check.
        """

        if self.projection_settlement is None:
            return False
        if isinstance(event, RunEventNotification) and self._is_terminal_typed(event):
            return True
        if isinstance(event, ProcessingStatusEvent) and self._is_terminal_typed(event):
            return True
        return False

    async def _deliver_with_reservation(
        self,
        event,
        frame,
        *,
        trace_id,
        reservation,
    ) -> tuple[bool, bool]:
        lease_lost = asyncio.Event()
        heartbeat = (
            traced_create_task(
                self._heartbeat_reservation(reservation, lease_lost),
                name=f"delivery-reservation:{reservation.dedup_key}",
            )
            if reservation is not None
            else None
        )
        try:
            delivered = await self._deliver_frontend(
                event.room_id,
                frame,
                event,
                "sse_fanout",
                trace_id=trace_id,
            )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
        return delivered, not lease_lost.is_set()

    async def _heartbeat_reservation(
        self,
        reservation: DeliveryReservation,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(0.05, self.deduplicator.reservation_ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not await self.deduplicator.renew(reservation):
                lease_lost.set()
                return

    async def _deliver_frontend(
        self,
        room_id: str,
        frame: dict[str, Any],
        payload: Any,
        failure_stage: str,
        *,
        trace_id: str | None,
    ) -> bool:
        local_delivered = False
        try:
            local_count = await self.sse_transport.broadcast_frame_to_room(
                room_id, frame
            )
            local_delivered = isinstance(local_count, int) and local_count > 0
        except Exception:
            pass

        remote_delivered = False
        with trace_id_context(trace_id):
            try:
                remote_delivered = (
                    await self.event_bus.publish_sse(room_id, frame)
                ) is True
            except Exception as exc:
                await self._dead_letter(failure_stage, payload, exc)
        return local_delivered or remote_delivered

    async def _reserve_typed_delivery(
        self, event: DeliveryEvent
    ) -> tuple[DeliveryReservationStatus | None, DeliveryReservation | None]:
        if not self._is_terminal_typed(event):
            return None, None
        reserve = getattr(self.deduplicator, "reserve", None)
        if not callable(reserve):
            return None, None
        reservation = await reserve(
            room_id=event.room_id,
            message_id=self._dedup_message_id(event),
            status=self._dedup_status(event),
            delivery_id=getattr(event, "delivery_id", None),
        )
        return reservation.status, reservation

    async def _should_deliver_typed(self, event: DeliveryEvent) -> bool:
        if self._is_terminal_typed(event):
            try:
                return await self.deduplicator.should_deliver(
                    room_id=event.room_id,
                    message_id=self._dedup_message_id(event),
                    status=self._dedup_status(event),
                    delivery_id=getattr(event, "delivery_id", None),
                )
            except TypeError as exc:
                if "delivery_id" not in str(exc):
                    raise
                return await self.deduplicator.should_deliver(
                    room_id=event.room_id,
                    message_id=self._dedup_message_id(event),
                    status=self._dedup_status(event),
                )
        return True

    async def _release_typed_delivery(
        self,
        event: DeliveryEvent,
        reservation: DeliveryReservation | None = None,
    ) -> None:
        if not self._is_terminal_typed(event):
            return
        release = getattr(self.deduplicator, "release", None)
        if release is None:
            return
        try:
            await release(
                room_id=event.room_id,
                message_id=self._dedup_message_id(event),
                status=self._dedup_status(event),
                delivery_id=getattr(event, "delivery_id", None),
                reservation=reservation,
            )
        except TypeError as exc:
            if "delivery_id" not in str(exc) and "reservation" not in str(exc):
                raise
            await release(
                room_id=event.room_id,
                message_id=self._dedup_message_id(event),
                status=self._dedup_status(event),
            )

    def _is_terminal_typed(self, event: DeliveryEvent) -> bool:
        return (
            (
                isinstance(event, ProcessingStatusEvent)
                and event.status in self.config.terminal_processing_statuses
            )
            or (
                isinstance(event, TaskUpdateEvent)
                and event.delivery_id is not None
                and event.status in self.config.terminal_processing_statuses
            )
            or (
                isinstance(event, RunEventNotification)
                and event.delivery_id is not None
            )
            or (isinstance(event, AgentMessageFinal) and event.delivery_id is not None)
        )

    @staticmethod
    def _dedup_message_id(event: DeliveryEvent) -> str | None:
        return getattr(event, "message_id", None) or getattr(event, "event_id", None)

    @staticmethod
    def _dedup_status(event: DeliveryEvent) -> str:
        return str(getattr(event, "status", "delivered"))

    def _persisted_event_id(self, event: DeliveryEvent) -> str | None:
        """Stable logical event identity stored on the room_events doc."""

        if isinstance(event, RunEventNotification):
            return event.event_id
        delivery_id = getattr(event, "delivery_id", None)
        if delivery_id:
            return str(delivery_id)
        if isinstance(event, TaskSubmittedEvent):
            return event.task_id
        if isinstance(event, TaskUpdateEvent) and event.run_id:
            return (
                event.delivery_id
                or f"{event.run_id}:{event.opaque_public_call_id}:{event.status}"
            )
        if isinstance(event, HITLRequestEvent):
            return f"{event.request_id}:{event.question_index}"
        if isinstance(event, HITLResolvedEvent):
            return f"{event.request_id}:{event.status}"
        message_id = getattr(event, "message_id", None)
        if message_id:
            return str(message_id)
        return None

    def _idempotency_key(self, event: DeliveryEvent, frame_data: dict[str, Any]) -> str:
        """Deterministic ``_id`` so retries reuse the persisted doc (plan §5).

        Terminal events use their delivery_id/dedup key (they already re-emit
        after a failed delivery releases the reservation). Non-terminal events
        derive from stable identity fields plus a per-stream monotonic
        component so distinct streaming deltas with identical content do not
        collapse into one doc.
        """

        if self._is_terminal_typed(event):
            return (
                getattr(event, "delivery_id", None)
                or f"terminal:{event.room_id}:{self._dedup_message_id(event)}:"
                f"{self._dedup_status(event)}"
            )
        stable = self._persisted_event_id(event)
        if stable is not None and isinstance(
            event,
            (
                RunEventNotification,
                TaskSubmittedEvent,
                TaskUpdateEvent,
                HITLRequestEvent,
                HITLResolvedEvent,
                AgentMessageFinal,
            ),
        ):
            return f"{event.event_type}:{stable}"
        stream_key = self._stream_key(event)
        counter = self._stream_counters.get(stream_key, 0) + 1
        self._stream_counters[stream_key] = counter
        digest = _frame_data_digest(frame_data)
        return f"{event.event_type}:{stream_key}:{counter}:{digest}"

    @staticmethod
    def _stream_key(event: DeliveryEvent) -> str:
        """Per-stream identity for the monotonic idempotency component."""

        if isinstance(event, HITLResolvedEvent):
            return f"{event.request_id}:{event.status}"
        message_id = getattr(event, "message_id", None)
        if message_id:
            return str(message_id)
        request_id = getattr(event, "request_id", None)
        if request_id:
            return str(request_id)
        event_id = getattr(event, "event_id", None)
        if event_id:
            return str(event_id)
        return event.event_type

    async def _dead_letter(self, stage: str, payload: Any, exc: Exception) -> None:
        envelope = {
            "origin": self.instance_id,
            "failure_stage": stage,
            "event_type": getattr(payload, "event_type", None)
            or (payload.get("type") if isinstance(payload, dict) else None),
            "trace_id": get_current_trace_id(),
            "payload": self._serialize_payload(payload),
            "exception_class": exc.__class__.__name__,
            "exception_message": str(exc),
            "timestamp": self._now().isoformat(),
        }
        self.dead_letters.append(envelope)
        try:
            await self.event_bus.publish_dead_letter(envelope)
        except Exception:
            return

    def _serialize_payload(self, payload: Any) -> Any:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json")
        return payload

    def _inject_typed_trace_id(
        self, frame: dict[str, Any], trace_id: str | None
    ) -> None:
        if not trace_id:
            return
        data = frame.get("data")
        if isinstance(data, dict) and "trace_id" not in data:
            data["trace_id"] = trace_id

    def _increment(self, name: str, tags: dict[str, str]) -> None:
        try:
            self._metrics.increment(name, tags=tags)
        except Exception:
            return


__all__ = ["EventPublisherImpl", "ProjectionSettlementReader"]
