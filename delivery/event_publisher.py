import asyncio
import inspect
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from common.dto import DeliveryEvent, InternalEvent, ProcessingStatusEvent
from common.observability import (
    MetricsCollector,
    NoopMetricsCollector,
    get_current_trace_id,
    trace_id_context,
)
from delivery.config import DeliveryConfig
from delivery.sse.deduplication import TerminalStatusDeduplicator
from delivery.sse.manager import SSETransportImpl
from delivery.translator import to_sse_frame
from delivery.types import TaskRunner


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
        task_runner: TaskRunner,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.sse_transport = sse_transport
        self.event_bus = event_bus
        self.deduplicator = deduplicator
        self.config = config
        self._now = now
        self.instance_id = instance_id
        self._task_runner = task_runner
        self._metrics = metrics or NoopMetricsCollector()
        self._handlers: dict[str, list[Callable[[Any], Any]]] = defaultdict(list)
        self._handler_tasks: set[asyncio.Task] = set()
        self.dead_letters: deque[dict[str, Any]] = deque(
            maxlen=config.dead_letter_memory_maxlen
        )
        self._internal_event_adapter = TypeAdapter(InternalEvent)
        self._stopping = False

    async def emit(self, event: DeliveryEvent) -> None:
        try:
            if not await self._should_deliver_typed(event):
                self._increment(
                    "hybro_delivery_events_deduplicated_total",
                    {"event_type": "processing_status"},
                )
                return
            timestamp = event.timestamp or self._now()
            frame = to_sse_frame(event, timestamp=timestamp)
            trace_id = getattr(event, "trace_id", None) or get_current_trace_id()
            self._inject_typed_trace_id(frame, trace_id)
        except Exception as exc:
            await self._dead_letter("translate", event, exc)
            return

        self._increment(
            "hybro_delivery_events_emitted_total",
            {"event_type": frame["type"]},
        )
        await self._deliver_frontend(event.room_id, frame, event, "sse_fanout")

    async def emit_internal(
        self,
        event: InternalEvent,
        *,
        wait_for_local_handlers: bool = False,
        broadcast: bool = True,
    ) -> None:
        handler_tasks = self._schedule_internal_handlers(event)
        if broadcast:
            try:
                await self.event_bus.publish_internal(event)
            except Exception as exc:
                await self._dead_letter("internal_fanout", event, exc)
        if wait_for_local_handlers and handler_tasks:
            await asyncio.gather(*handler_tasks, return_exceptions=True)

    def register_internal_handler(self, event_type: str, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    async def start(self) -> None:
        self._stopping = False

    async def stop(self) -> None:
        self._stopping = True
        if self._handler_tasks:
            _, pending = await asyncio.wait(
                self._handler_tasks,
                timeout=self.config.handler_shutdown_timeout_seconds,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def handle_remote_internal_event(self, envelope: dict[str, Any]) -> None:
        if envelope.get("origin") == self.instance_id:
            return
        try:
            event = self._internal_event_adapter.validate_python(envelope.get("event"))
        except Exception as exc:
            await self._dead_letter("internal_deserialize", envelope, exc)
            return
        if envelope.get("event_type") != event.event_type:
            return
        with trace_id_context(envelope.get("trace_id")):
            self._schedule_internal_handlers(event)

    async def _deliver_frontend(
        self,
        room_id: str,
        frame: dict[str, Any],
        payload: Any,
        failure_stage: str,
    ) -> None:
        try:
            await self.sse_transport.broadcast_frame_to_room(room_id, frame)
        except Exception:
            pass

        try:
            await self.event_bus.publish_sse(room_id, frame)
        except Exception as exc:
            await self._dead_letter(failure_stage, payload, exc)

    async def _should_deliver_typed(self, event: DeliveryEvent) -> bool:
        if (
            isinstance(event, ProcessingStatusEvent)
            and event.status in self.config.terminal_processing_statuses
        ):
            return await self.deduplicator.should_deliver(
                room_id=event.room_id,
                message_id=event.message_id,
                status=event.status,
            )
        return True

    def _schedule_internal_handlers(self, event: InternalEvent) -> list[asyncio.Task]:
        if self._stopping:
            return []
        tasks: list[asyncio.Task] = []
        for handler in self._handlers.get(event.event_type, []):
            task = self._task_runner(
                self._run_handler(handler, event),
                name=f"delivery-handler-{event.event_type}",
            )
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)
            tasks.append(task)
        return tasks

    async def _run_handler(self, handler: Callable, event: InternalEvent) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            await self._dead_letter("internal_handler", event, exc)

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

    def _inject_typed_trace_id(self, frame: dict[str, Any], trace_id: str | None) -> None:
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
