import asyncio
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.dto import (
    DeliveryEmitStatus,
    DeliveryEvent,
    ProcessingStatusEvent,
    RunEventNotification,
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
from delivery.sse.deduplication import (
    DeliveryReservation,
    DeliveryReservationStatus,
    TerminalStatusDeduplicator,
)
from delivery.sse.manager import SSETransportImpl
from delivery.translator import to_sse_frame


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
    ) -> None:
        self.sse_transport = sse_transport
        self.event_bus = event_bus
        self.deduplicator = deduplicator
        self.config = config
        self._now = now
        self.instance_id = instance_id
        self._metrics = metrics or NoopMetricsCollector()
        self.dead_letters: deque[dict[str, Any]] = deque(
            maxlen=config.dead_letter_memory_maxlen
        )

    async def emit(self, event: DeliveryEvent) -> bool:
        """Compatibility API: only a fresh confirmed delivery returns ``True``."""
        return (await self.emit_checked(event)) == DeliveryEmitStatus.DELIVERED

    async def emit_checked(  # noqa: C901
        self, event: DeliveryEvent
    ) -> DeliveryEmitStatus:
        reservation: DeliveryReservation | None = None
        try:
            reservation_status, reservation = await self._reserve_typed_delivery(event)
            if reservation_status == DeliveryReservationStatus.IN_FLIGHT:
                return DeliveryEmitStatus.IN_FLIGHT
            if reservation_status == DeliveryReservationStatus.ALREADY_DELIVERED:
                self._increment(
                    "hybro_delivery_events_deduplicated_total",
                    {"event_type": event.event_type},
                )
                return DeliveryEmitStatus.ALREADY_DELIVERED
            if reservation_status is None and not await self._should_deliver_typed(
                event
            ):
                self._increment(
                    "hybro_delivery_events_deduplicated_total",
                    {"event_type": event.event_type},
                )
                return DeliveryEmitStatus.DEDUPLICATED
            timestamp = event.timestamp or self._now()
            frame = to_sse_frame(event, timestamp=timestamp)
            trace_id = getattr(event, "trace_id", None) or get_current_trace_id()
            self._inject_typed_trace_id(frame, trace_id)
        except Exception as exc:
            if reservation is not None:
                await self._release_typed_delivery(event, reservation)
            await self._dead_letter("translate", event, exc)
            return DeliveryEmitStatus.FAILED

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
        if not lease_owned:
            return DeliveryEmitStatus.FAILED
        if not delivered:
            await self._release_typed_delivery(event, reservation)
            return DeliveryEmitStatus.FAILED
        if reservation is not None:
            try:
                if not await self.deduplicator.confirm(reservation):
                    return DeliveryEmitStatus.FAILED
            except Exception:
                return DeliveryEmitStatus.FAILED
        return DeliveryEmitStatus.DELIVERED

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
        )

    @staticmethod
    def _dedup_message_id(event: DeliveryEvent) -> str | None:
        return getattr(event, "message_id", None) or getattr(event, "event_id", None)

    @staticmethod
    def _dedup_status(event: DeliveryEvent) -> str:
        return str(getattr(event, "status", "delivered"))

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


__all__ = ["EventPublisherImpl"]
