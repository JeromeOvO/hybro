import asyncio
import contextvars
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, ContextManager, Protocol, runtime_checkable

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hybro_trace_id",
    default=None,
)


@runtime_checkable
class TracingProvider(Protocol):
    def start_span(
        self,
        name: str,
        attributes: dict[str, str] | None = None,
    ) -> ContextManager[None]: ...


class NoopTracingProvider:
    def start_span(
        self,
        name: str,
        attributes: dict[str, str] | None = None,
    ) -> ContextManager[None]:
        return nullcontext()


def traced_create_task(
    coro: Awaitable[Any],
    *,
    name: str | None = None,
) -> asyncio.Task:
    context = contextvars.copy_context()
    return asyncio.create_task(coro, name=name, context=context)


def get_current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_id_context(trace_id: str | None) -> Iterator[None]:
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)


__all__ = [
    "NoopTracingProvider",
    "TracingProvider",
    "get_current_trace_id",
    "trace_id_context",
    "traced_create_task",
]
