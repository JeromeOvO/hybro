from dataclasses import dataclass


@dataclass(frozen=True)
class EventingConfig:
    handler_queue_maxsize: int = 1000
    enqueue_timeout_seconds: float = 1.0
    shutdown_timeout_seconds: float = 5.0
    dead_letter_memory_maxlen: int = 1000

    def __post_init__(self) -> None:
        for name in (
            "handler_queue_maxsize",
            "enqueue_timeout_seconds",
            "shutdown_timeout_seconds",
            "dead_letter_memory_maxlen",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(f"{name} must be greater than 0")


__all__ = ["EventingConfig"]
