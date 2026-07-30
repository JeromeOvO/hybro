import asyncio
import contextvars
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, ContextManager, Protocol, runtime_checkable

from common.observability.logging import bind_log_context, get_log_context


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
    return get_log_context().get("trace_id")


@contextmanager
def trace_id_context(trace_id: str | None) -> Iterator[None]:
    with bind_log_context(trace_id=trace_id):
        yield


__all__ = [
    "NoopTracingProvider",
    "TracingProvider",
    "get_current_trace_id",
    "trace_id_context",
    "traced_create_task",
]
