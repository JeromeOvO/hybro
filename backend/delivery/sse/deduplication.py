from collections.abc import Callable

from cachetools import TTLCache

from common.protocols import RedisKV
from delivery.config import DeliveryConfig


class TerminalStatusDeduplicator:
    def __init__(
        self,
        *,
        config: DeliveryConfig,
        redis_kv: RedisKV | None = None,
        timer: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.redis_kv = redis_kv
        cache_kwargs = {
            "maxsize": config.terminal_dedup_cache_maxsize,
            "ttl": config.terminal_dedup_ttl_seconds,
        }
        if timer is not None:
            cache_kwargs["timer"] = timer
        self.cache: TTLCache[str, str] = TTLCache(**cache_kwargs)

    async def should_deliver(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
    ) -> bool:
        normalized_status = status.strip().lower()
        if (
            not message_id
            or normalized_status not in self.config.terminal_processing_statuses
        ):
            return True

        dedup_key = f"{room_id}:{message_id}"
        if dedup_key in self.cache:
            return False

        if self.redis_kv is not None:
            redis_key = f"{self.config.redis_terminal_key_prefix}{dedup_key}"
            try:
                was_first = await self.redis_kv.setnx(
                    redis_key,
                    normalized_status,
                    ttl=self.config.terminal_dedup_ttl_seconds,
                )
            except Exception:
                self.cache[dedup_key] = normalized_status
                return True
            if not was_first:
                self.cache[dedup_key] = normalized_status
                return False

        self.cache[dedup_key] = normalized_status
        return True


__all__ = ["TerminalStatusDeduplicator"]
