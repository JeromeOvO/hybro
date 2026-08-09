from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from cachetools import TTLCache

from common.protocols import RedisKV
from delivery.config import DeliveryConfig


class DeliveryReservationStatus(StrEnum):
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    ALREADY_DELIVERED = "already_delivered"


@dataclass(frozen=True)
class DeliveryReservation:
    status: DeliveryReservationStatus
    dedup_key: str
    claim_id: str | None = None
    l2_owned: bool = False


@dataclass(frozen=True)
class _CacheEntry:
    state: str
    status: str
    claim_id: str | None = None
    l2_owned: bool = False


class TerminalStatusDeduplicator:
    """Two-phase terminal delivery reservation and confirmation.

    Reservations use a short lease and are distinct from confirmed delivery
    markers. A Redis failure creates an explicitly L1-only reservation that can
    be confirmed locally without later mistaking a missing Redis key for lost
    ownership.
    """

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
        self.reservation_ttl_seconds = min(
            config.terminal_reservation_ttl_seconds,
            max(1, config.terminal_dedup_ttl_seconds - 1),
        )
        confirmed_kwargs = {
            "maxsize": config.terminal_dedup_cache_maxsize,
            "ttl": config.terminal_dedup_ttl_seconds,
        }
        reservation_kwargs = {
            "maxsize": config.terminal_dedup_cache_maxsize,
            "ttl": self.reservation_ttl_seconds,
        }
        if timer is not None:
            confirmed_kwargs["timer"] = timer
            reservation_kwargs["timer"] = timer
        self.cache: TTLCache[str, _CacheEntry] = TTLCache(**confirmed_kwargs)
        self.reservations: TTLCache[str, _CacheEntry] = TTLCache(**reservation_kwargs)

    @staticmethod
    def _dedup_key(
        room_id: str,
        message_id: str | None,
        delivery_id: str | None,
    ) -> str:
        return f"delivery:{delivery_id}" if delivery_id else f"{room_id}:{message_id}"

    async def reserve(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
        delivery_id: str | None = None,
    ) -> DeliveryReservation:
        dedup_key = self._dedup_key(room_id, message_id, delivery_id)
        if self.cache.get(dedup_key) is not None:
            return DeliveryReservation(
                DeliveryReservationStatus.ALREADY_DELIVERED, dedup_key
            )
        if self.reservations.get(dedup_key) is not None:
            return DeliveryReservation(DeliveryReservationStatus.IN_FLIGHT, dedup_key)

        normalized_status = status.strip().lower()
        claim_id = self._claim_id_factory()
        if self.redis_kv is not None:
            redis_key = f"{self.config.redis_terminal_key_prefix}{dedup_key}"
            try:
                was_first = await self.redis_kv.setnx(
                    redis_key,
                    claim_id,
                    ttl=self.reservation_ttl_seconds,
                )
            except Exception:
                self.reservations[dedup_key] = _CacheEntry(
                    "reserved", normalized_status, claim_id, l2_owned=False
                )
                return DeliveryReservation(
                    DeliveryReservationStatus.RESERVED,
                    dedup_key,
                    claim_id,
                    l2_owned=False,
                )
            if not was_first:
                try:
                    existing = await self.redis_kv.get(redis_key)
                except Exception:
                    existing = None
                if isinstance(existing, str) and existing.startswith("delivered:"):
                    self.cache[dedup_key] = _CacheEntry("delivered", normalized_status)
                    return DeliveryReservation(
                        DeliveryReservationStatus.ALREADY_DELIVERED, dedup_key
                    )
                return DeliveryReservation(
                    DeliveryReservationStatus.IN_FLIGHT, dedup_key
                )

        l2_owned = self.redis_kv is not None
        self.reservations[dedup_key] = _CacheEntry(
            "reserved", normalized_status, claim_id, l2_owned=l2_owned
        )
        return DeliveryReservation(
            DeliveryReservationStatus.RESERVED,
            dedup_key,
            claim_id,
            l2_owned=l2_owned,
        )

    async def renew(self, reservation: DeliveryReservation) -> bool:
        """Renew an owned reservation while transport fanout is in progress."""
        if (
            reservation.status != DeliveryReservationStatus.RESERVED
            or reservation.claim_id is None
        ):
            return False
        cached = self.reservations.get(reservation.dedup_key)
        if cached is None or cached.claim_id != reservation.claim_id:
            return False
        if cached.l2_owned:
            if self.redis_kv is None:
                return False
            redis_key = (
                f"{self.config.redis_terminal_key_prefix}{reservation.dedup_key}"
            )
            try:
                renewed = await self.redis_kv.compare_set(
                    redis_key,
                    reservation.claim_id,
                    reservation.claim_id,
                    ttl=self.reservation_ttl_seconds,
                )
            except Exception:
                return False
            if not renewed:
                return False
        self.reservations[reservation.dedup_key] = cached
        return True

    async def confirm(self, reservation: DeliveryReservation) -> bool:
        if (
            reservation.status != DeliveryReservationStatus.RESERVED
            or reservation.claim_id is None
        ):
            return reservation.status == DeliveryReservationStatus.ALREADY_DELIVERED
        cached = self.reservations.get(reservation.dedup_key)
        if (
            cached is None
            or cached.state != "reserved"
            or cached.claim_id != reservation.claim_id
        ):
            return False

        if cached.l2_owned and reservation.l2_owned and self.redis_kv is not None:
            redis_key = (
                f"{self.config.redis_terminal_key_prefix}{reservation.dedup_key}"
            )
            delivered_value = f"delivered:{cached.status}"
            try:
                confirmed = await self.redis_kv.compare_set(
                    redis_key,
                    reservation.claim_id,
                    delivered_value,
                    ttl=self.config.terminal_dedup_ttl_seconds,
                )
            except Exception:
                return False
            if not confirmed:
                return False

        self.reservations.pop(reservation.dedup_key, None)
        self.cache[reservation.dedup_key] = _CacheEntry("delivered", cached.status)
        return True

    async def should_deliver(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
        delivery_id: str | None = None,
    ) -> bool:
        """Compatibility preflight API; new publishers must use two phases."""
        normalized_status = status.strip().lower()
        if (
            not message_id
            or normalized_status not in self.config.terminal_processing_statuses
        ):
            return True
        reservation = await self.reserve(
            room_id=room_id,
            message_id=message_id,
            status=status,
            delivery_id=delivery_id,
        )
        return reservation.status == DeliveryReservationStatus.RESERVED

    async def release(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
        delivery_id: str | None = None,
        reservation: DeliveryReservation | None = None,
    ) -> None:
        """Release only the caller's unconfirmed reservation."""
        normalized_status = status.strip().lower()
        dedup_key = self._dedup_key(room_id, message_id, delivery_id)
        claim = self.reservations.get(dedup_key)
        claim_id = reservation.claim_id if reservation is not None else None
        if (
            claim is None
            or claim.state != "reserved"
            or claim.status != normalized_status
            or (claim_id is not None and claim.claim_id != claim_id)
        ):
            return
        self.reservations.pop(dedup_key, None)

        if self.redis_kv is None or claim.claim_id is None or not claim.l2_owned:
            return
        redis_key = f"{self.config.redis_terminal_key_prefix}{dedup_key}"
        try:
            await self.redis_kv.compare_delete(redis_key, claim.claim_id)
        except Exception:
            return


__all__ = [
    "DeliveryReservation",
    "DeliveryReservationStatus",
    "TerminalStatusDeduplicator",
]
