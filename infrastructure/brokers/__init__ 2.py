from __future__ import annotations

from infrastructure.event_broker import EventBroker


def create_event_broker() -> EventBroker | None:
    """Factory: return a configured EventBroker, or None if disabled.

    Selection logic:
        - redis_url is set -> RedisBroker
        - redis_url is empty -> None (broker disabled, single-instance mode)

    To add a new broker (e.g., NATS):
        1. Create infrastructure/brokers/nats_broker.py implementing EventBroker
        2. Add a config field (e.g., nats_url) to settings
        3. Add selection logic here
    """
    from config.settings import settings

    if not settings.redis_url:
        return None

    from infrastructure.brokers.redis_broker import RedisBroker

    return RedisBroker(url=settings.redis_url)
