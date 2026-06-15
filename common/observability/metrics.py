from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsCollector(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: dict[str, str] | None = None,
    ) -> None: ...
    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None: ...
    def timing(
        self,
        name: str,
        value_ms: float,
        tags: dict[str, str] | None = None,
    ) -> None: ...


class NoopMetricsCollector:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: dict[str, str] | None = None,
    ) -> None:
        return None

    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        return None

    def timing(
        self,
        name: str,
        value_ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        return None


__all__ = ["MetricsCollector", "NoopMetricsCollector"]
