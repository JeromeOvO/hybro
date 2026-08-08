from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from common.eventing.eventing_config import EventingConfig
from common.eventing.models import EventDeadLetter, EventEnvelope
from common.eventing.protocols import EventHandler, InternalEventTransport
from common.eventing.registry import EventModelRegistry
from common.observability import get_current_trace_id, trace_id_context


@dataclass(slots=True)
class _QueuedEvent:
    event: BaseModel
    trace_id: str | None
    completion: asyncio.Future[None]


@dataclass(slots=True)
class _HandlerState:
    event_type: str
    handler: EventHandler
    queue: asyncio.Queue[_QueuedEvent]
    task: asyncio.Task[None] | None = None


class BoundedInternalEventBus:
    """Process-local ordered handlers with optional generic remote fan-out."""

    def __init__(
        self,
        *,
        registry: EventModelRegistry,
        instance_id: str,
        now: Callable[[], datetime],
        config: EventingConfig | None = None,
        transport: InternalEventTransport | None = None,
    ) -> None:
        self.registry = registry
        self.instance_id = instance_id
        self.config = config or EventingConfig()
        self.transport = transport
        self._now = now
        self._handlers: dict[str, list[_HandlerState]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._admission_condition = asyncio.Condition()
        self._active_publications = 0
        self._accepting = False
        self._started = False
        self._starting = False
        self._stopping = False
        self.dead_letters: deque[EventDeadLetter] = deque(
            maxlen=self.config.dead_letter_memory_maxlen
        )

    @property
    def is_connected(self) -> bool:
        return bool(self.transport and self.transport.is_connected)

    @property
    def worker_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(
            state.task
            for states in self._handlers.values()
            for state in states
            if state.task is not None
        )

    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        if self.registry.frozen or self._started:
            raise RuntimeError(
                "Internal event handlers must be registered before start"
            )
        if not callable(handler):
            raise TypeError("handler must be callable")
        state = _HandlerState(
            event_type=event_type,
            handler=handler,
            queue=asyncio.Queue(maxsize=self.config.handler_queue_maxsize),
        )
        self._handlers.setdefault(event_type, []).append(state)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started and self._accepting:
                return
            self._stopping = False
            self._starting = True
            self._started = True
            await self._set_accepting(False)
            self.registry.freeze()
            for states in self._handlers.values():
                for index, state in enumerate(states):
                    if state.task is not None and not state.task.done():
                        continue
                    state.task = asyncio.create_task(
                        self._worker(state),
                        name=f"eventing-handler-{state.event_type}-{index}",
                    )
            try:
                if self.transport is not None:
                    await self.transport.start(self.handle_remote_message)
                    await self.transport.refresh_health()
                await self._complete_start(success=True)
            except BaseException:
                await self._complete_start(success=False)
                if self.transport is not None:
                    try:
                        await self.transport.stop()
                    except BaseException:
                        pass
                await self._cancel_workers()
                raise

    async def stop(self) -> None:
        cancelled: asyncio.CancelledError | None = None
        async with self._lifecycle_lock:
            cleanup = asyncio.create_task(
                self._stop_cleanup(),
                name="eventing-stop-cleanup",
            )
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:
                    cancelled = cancelled or exc
            await asyncio.gather(cleanup, return_exceptions=True)
            cleanup_error = cleanup.exception()
        if cleanup_error is not None:
            raise cleanup_error
        if cancelled is not None:
            raise cancelled

    async def refresh_health(self) -> None:
        if self.transport is not None:
            await self.transport.refresh_health()

    async def publish(
        self,
        event: BaseModel,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ) -> None:
        if not await self._admit_publication():
            raise RuntimeError("Internal event bus is not running")
        completions: list[asyncio.Future[None]] = []
        try:
            event_type = self.registry.event_type_for(event)
            trace_id = get_current_trace_id()
            deadline = self._publication_deadline()
            completions = await self._enqueue_handlers(
                event_type,
                event,
                trace_id,
                deadline=deadline,
            )
            if fanout and self.transport is not None:
                envelope = EventEnvelope(
                    origin=self.instance_id,
                    event_type=event_type,
                    event=event.model_dump(mode="json"),
                    trace_id=trace_id,
                    timestamp=self._now(),
                )
                try:
                    await asyncio.wait_for(
                        self.transport.publish(envelope.model_dump_json()),
                        timeout=self._remaining(deadline),
                    )
                except Exception as exc:
                    await self._safe_dead_letter(
                        "fanout",
                        event,
                        exc,
                        event_type=event_type,
                        timeout=self._remaining(deadline),
                    )
        finally:
            await asyncio.shield(self._finish_publication())
        if wait_for_handlers and completions:
            await asyncio.gather(*completions)

    async def handle_remote_message(self, message: str) -> None:
        envelope: EventEnvelope | None = None
        try:
            envelope = EventEnvelope.model_validate_json(message)
            if envelope.kind != "internal_event":
                raise ValueError("Unexpected internal event envelope kind")
            if envelope.origin == self.instance_id:
                return
            event = self.registry.deserialize(envelope.event_type, envelope.event)
            if getattr(event, "event_type", None) != envelope.event_type:
                raise ValueError("Internal event envelope type mismatch")
        except Exception as exc:
            with trace_id_context(envelope.trace_id if envelope else None):
                await self._safe_dead_letter(
                    "deserialization",
                    envelope.model_dump(mode="json") if envelope else message,
                    exc,
                    event_type=envelope.event_type if envelope else None,
                )
            return
        if not await self._admit_remote_publication():
            return
        try:
            with trace_id_context(envelope.trace_id):
                await self._enqueue_handlers(
                    envelope.event_type,
                    event,
                    envelope.trace_id,
                    deadline=self._publication_deadline(),
                )
        finally:
            await asyncio.shield(self._finish_publication())

    async def _enqueue_handlers(
        self,
        event_type: str,
        event: BaseModel,
        trace_id: str | None,
        *,
        deadline: float,
    ) -> list[asyncio.Future[None]]:
        completions: list[asyncio.Future[None]] = []
        for state in self._handlers.get(event_type, []):
            completion = asyncio.get_running_loop().create_future()
            completions.append(completion)
            item = _QueuedEvent(event=event, trace_id=trace_id, completion=completion)
            try:
                await asyncio.wait_for(
                    state.queue.put(item),
                    timeout=self._remaining(deadline),
                )
            except TimeoutError as exc:
                await self._safe_dead_letter(
                    "queue_full",
                    event,
                    exc,
                    event_type=event_type,
                    metadata={"handler": self._handler_name(state.handler)},
                    timeout=self._remaining(deadline),
                )
                completion.set_result(None)
        return completions

    async def _worker(self, state: _HandlerState) -> None:
        while True:
            try:
                item = await state.queue.get()
            except BaseException as exc:
                if self._is_worker_cancellation(exc):
                    raise
                await self._safe_dead_letter(
                    "worker",
                    {"handler": self._handler_name(state.handler)},
                    exc,
                    event_type=state.event_type,
                )
                await asyncio.sleep(0)
                continue
            await self._process_item(state, item)

    async def _process_item(
        self,
        state: _HandlerState,
        item: _QueuedEvent,
    ) -> None:
        try:
            with trace_id_context(item.trace_id):
                try:
                    result = state.handler(item.event)
                    if inspect.isawaitable(result):
                        await result
                except BaseException as exc:
                    if self._is_worker_cancellation(exc):
                        raise
                    await self._safe_dead_letter(
                        "handler",
                        item.event,
                        exc,
                        event_type=state.event_type,
                        metadata={"handler": self._handler_name(state.handler)},
                    )
        except BaseException as exc:
            if self._is_worker_cancellation(exc):
                raise
            await self._safe_dead_letter(
                "worker",
                item.event,
                exc,
                event_type=state.event_type,
                metadata={"handler": self._handler_name(state.handler)},
            )
        finally:
            if not item.completion.done():
                item.completion.set_result(None)
            try:
                state.queue.task_done()
            except BaseException as exc:
                if self._is_worker_cancellation(exc):
                    raise
                await self._safe_dead_letter(
                    "worker",
                    item.event,
                    exc,
                    event_type=state.event_type,
                    metadata={"handler": self._handler_name(state.handler)},
                )

    async def _safe_dead_letter(
        self,
        stage: str,
        payload: Any,
        exc: BaseException,
        *,
        event_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        try:
            await self._dead_letter(
                stage,
                payload,
                exc,
                event_type=event_type,
                metadata=metadata,
                timeout=timeout,
            )
        except BaseException as dead_letter_exc:
            if self._is_worker_cancellation(dead_letter_exc):
                raise

    async def _dead_letter(
        self,
        stage: str,
        payload: Any,
        exc: BaseException,
        *,
        event_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        serialized = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else payload
        )
        dead_letter = EventDeadLetter(
            origin=self.instance_id,
            failure_stage=stage,
            event_type=event_type or getattr(payload, "event_type", None),
            trace_id=get_current_trace_id(),
            payload=serialized,
            exception_class=exc.__class__.__name__,
            exception_message=str(exc),
            timestamp=self._now(),
            metadata=metadata or {},
        )
        self.dead_letters.append(dead_letter)
        if self.transport is None:
            return
        try:
            await asyncio.wait_for(
                self.transport.publish_dead_letter(dead_letter.model_dump_json()),
                timeout=(
                    self.config.enqueue_timeout_seconds
                    if timeout is None
                    else max(timeout, 0.001)
                ),
            )
        except BaseException as publish_exc:
            if self._is_worker_cancellation(publish_exc):
                raise

    async def _set_accepting(self, accepting: bool) -> None:
        async with self._admission_condition:
            self._accepting = accepting
            self._admission_condition.notify_all()

    async def _is_accepting(self) -> bool:
        async with self._admission_condition:
            return self._started and self._accepting and not self._stopping

    async def _complete_start(self, *, success: bool) -> None:
        async with self._admission_condition:
            self._starting = False
            self._started = success
            self._accepting = success
            self._admission_condition.notify_all()

    async def _admit_publication(self) -> bool:
        async with self._admission_condition:
            if not self._started or not self._accepting or self._stopping:
                return False
            self._active_publications += 1
            return True

    async def _admit_remote_publication(self) -> bool:
        async with self._admission_condition:
            while self._starting and not self._stopping:
                await self._admission_condition.wait()
            if not self._started or not self._accepting or self._stopping:
                return False
            self._active_publications += 1
            return True

    async def _finish_publication(self) -> None:
        async with self._admission_condition:
            self._active_publications -= 1
            if self._active_publications == 0:
                self._admission_condition.notify_all()

    async def _wait_for_active_publications(self) -> None:
        async with self._admission_condition:
            while self._active_publications:
                await self._admission_condition.wait()

    async def _stop_cleanup(self) -> None:
        self._stopping = True
        deadline = (
            asyncio.get_running_loop().time() + self.config.shutdown_timeout_seconds
        )
        try:
            await self._set_accepting(False)
            await self._stop_transport_ingress(self._remaining(deadline))
            # Publications are intrinsically bounded by their own deadline.
            # Waiting for every admitted publication prevents any queue put or
            # fanout from racing worker cancellation.
            await self._wait_for_active_publications()
            await self._stop_transport(self._remaining(deadline))
            await self._drain_handler_queues(self._remaining(deadline))
        finally:
            await self._cancel_workers()
            self._started = False

    async def _stop_transport_ingress(self, timeout: float) -> None:
        if self.transport is None:
            return
        stop_ingress = getattr(self.transport, "stop_ingress", None)
        if not callable(stop_ingress):
            return
        try:
            await asyncio.wait_for(stop_ingress(), timeout=timeout)
        except TimeoutError:
            pass
        except Exception:
            pass

    async def _stop_transport(self, timeout: float) -> None:
        if self.transport is None:
            return
        try:
            await asyncio.wait_for(
                self.transport.stop(),
                timeout=timeout,
            )
        except TimeoutError:
            pass
        except Exception:
            pass

    async def _drain_handler_queues(self, timeout: float) -> None:
        queues = [state.queue for states in self._handlers.values() for state in states]
        if not queues:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(queue.join() for queue in queues)),
                timeout=timeout,
            )
        except TimeoutError:
            pass

    async def _cancel_workers(self) -> None:
        tasks = list(self.worker_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for states in self._handlers.values():
            for state in states:
                state.task = None
                while not state.queue.empty():
                    item = state.queue.get_nowait()
                    if not item.completion.done():
                        item.completion.set_result(None)
                    state.queue.task_done()

    def _publication_deadline(self) -> float:
        timeout = min(
            self.config.enqueue_timeout_seconds,
            max(self.config.shutdown_timeout_seconds / 2, 0.001),
        )
        return asyncio.get_running_loop().time() + timeout

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(deadline - asyncio.get_running_loop().time(), 0.001)

    @staticmethod
    def _is_worker_cancellation(exc: BaseException) -> bool:
        if not isinstance(exc, asyncio.CancelledError):
            return False
        task = asyncio.current_task()
        return bool(task and task.cancelling())

    @staticmethod
    def _handler_name(handler: EventHandler) -> str:
        return getattr(handler, "__qualname__", handler.__class__.__name__)


__all__ = ["BoundedInternalEventBus"]
