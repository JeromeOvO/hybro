from contextlib import nullcontext
from typing import ContextManager, Protocol, runtime_checkable


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


__all__ = ["NoopTracingProvider", "TracingProvider"]
