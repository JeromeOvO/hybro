from collections.abc import Callable
from uuid import uuid4

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
        claim_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.redis_kv = redis_kv
        self._claim_id_factory = claim_id_factory or (lambda: uuid4().hex)
        cache_kwargs = {
            "maxsize": config.terminal_dedup_cache_maxsize,
            "ttl": config.terminal_dedup_ttl_seconds,
        }
        if timer is not None:
            cache_kwargs["timer"] = timer
        self.cache: TTLCache[str, tuple[str, str]] = TTLCache(**cache_kwargs)

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

        claim_id = self._claim_id_factory()
        if self.redis_kv is not None:
            redis_key = f"{self.config.redis_terminal_key_prefix}{dedup_key}"
            try:
                was_first = await self.redis_kv.setnx(
                    redis_key,
                    claim_id,
                    ttl=self.config.terminal_dedup_ttl_seconds,
                )
            except Exception:
                self.cache[dedup_key] = (normalized_status, claim_id)
                return True
            if not was_first:
                return False

        self.cache[dedup_key] = (normalized_status, claim_id)
        return True

    async def release(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
    ) -> None:
        """Release a reservation when the claimed terminal event was not delivered."""

        normalized_status = status.strip().lower()
        if (
            not message_id
            or normalized_status not in self.config.terminal_processing_statuses
        ):
            return

        dedup_key = f"{room_id}:{message_id}"
        claim = self.cache.get(dedup_key)
        if claim is None or claim[0] != normalized_status:
            return
        self.cache.pop(dedup_key, None)

        if self.redis_kv is None:
            return
        redis_key = f"{self.config.redis_terminal_key_prefix}{dedup_key}"
        try:
            await self.redis_kv.compare_delete(redis_key, claim[1])
        except Exception:
            return


__all__ = ["TerminalStatusDeduplicator"]
