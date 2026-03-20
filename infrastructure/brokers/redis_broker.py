import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from common.utils.logger import get_logger
from config.settings import settings
from infrastructure.event_broker import MessageHandler

logger = get_logger(__name__)


class RedisBroker:
    """Redis Pub/Sub implementation of EventBroker.

    - Single PubSub object + single subscriber background task
    - Dynamic subscribe/unsubscribe as channels are added/removed
    - Tracks subscribed channels for re-subscribe on reconnect
    - Exponential backoff on connection failures
    - publish() never raises — catches exceptions and logs warnings
    """

    def __init__(self, url: str):
        self._url = url
        self._client: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._subscriber_task: asyncio.Task | None = None
        self._subscribed_channels: set[str] = set()
        self._handlers: dict[str, MessageHandler] = {}
        self._connected: bool = False
        self._shutdown: bool = False

    # --- Protocol properties ---

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    # --- Lifecycle ---

    async def start(self) -> None:
        """Connect to Redis, create PubSub, start subscriber loop."""
        if self._client is not None:
            return  # idempotent
        try:
            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                retry_on_timeout=True,
            )
            await self._client.ping()
            self._pubsub = self._client.pubsub()
            # Subscribe to the global cancel channel immediately
            await self._pubsub.subscribe(settings.redis_cancel_channel)
            self._subscribed_channels.add(settings.redis_cancel_channel)
            self._subscriber_task = asyncio.create_task(self._subscriber_loop())
            self._connected = True
            logger.info("RedisBroker connected to %s", self._url)
        except Exception as e:
            logger.warning("RedisBroker connection failed: %s — broker disabled", e)
            self._client = None
            self._connected = False

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._shutdown = True
        if self._subscriber_task:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None
        if self._pubsub:
            try:
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        self._connected = False
        self._subscribed_channels.clear()
        logger.info("RedisBroker stopped")

    # --- Publish ---

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish message. Best-effort — never raises."""
        if not self.is_connected:
            return
        try:
            message = json.dumps(payload)
            await self._client.publish(channel, message)
        except Exception as e:
            logger.warning("RedisBroker publish failed on channel %s: %s", channel, e)
            self._connected = False

    # --- Subscribe/Unsubscribe ---

    async def subscribe(self, channel: str) -> None:
        """Subscribe to channel (idempotent)."""
        if channel in self._subscribed_channels:
            return
        if self._pubsub:
            try:
                await self._pubsub.subscribe(channel)
                self._subscribed_channels.add(channel)
                logger.debug("RedisBroker subscribed to %s", channel)
            except Exception as e:
                logger.warning("RedisBroker subscribe failed for %s: %s", channel, e)

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from channel (no-op if not subscribed)."""
        self._subscribed_channels.discard(channel)
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(channel)
                logger.debug("RedisBroker unsubscribed from %s", channel)
            except Exception as e:
                logger.warning(
                    "RedisBroker unsubscribe failed for %s: %s", channel, e
                )

    # --- Handler registration ---

    def set_handler(self, kind: str, handler: MessageHandler) -> None:
        """Register handler for a message kind."""
        self._handlers[kind] = handler

    # --- Subscriber loop ---

    async def _subscriber_loop(self) -> None:
        """Background task: listen for messages, dispatch to handlers."""
        backoff = settings.redis_reconnect_delay

        while not self._shutdown:
            try:
                async for message in self._pubsub.listen():
                    if self._shutdown:
                        break
                    # Receiving any message proves Redis is alive — recover
                    # from transient publish failures that set _connected=False
                    if not self._connected:
                        self._connected = True
                        logger.info("RedisBroker connection recovered")
                    if message["type"] != "message":
                        continue  # skip subscribe/unsubscribe confirmations
                    try:
                        payload = json.loads(message["data"])
                        kind = payload.get("kind")
                        handler = self._handlers.get(kind) if kind else None
                        if handler:
                            await handler(payload)
                        else:
                            logger.debug(
                                "RedisBroker: no handler for kind=%s, dropping message",
                                kind,
                            )
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("RedisBroker: invalid message: %s", e)
                # Clean exit from listen() — reset backoff
                backoff = settings.redis_reconnect_delay

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                if self._shutdown:
                    break
                logger.warning(
                    "RedisBroker subscriber error: %s. Reconnecting in %.1fs...",
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, settings.redis_reconnect_max_delay)
                # Attempt reconnect
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Reconnect Redis client and re-subscribe to all tracked channels."""
        try:
            if self._pubsub:
                try:
                    await self._pubsub.aclose()
                except Exception:
                    pass
            if self._client:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                retry_on_timeout=True,
            )
            await self._client.ping()
            self._pubsub = self._client.pubsub()
            # Re-subscribe to all tracked channels
            if self._subscribed_channels:
                await self._pubsub.subscribe(*self._subscribed_channels)
                logger.info(
                    "RedisBroker reconnected, re-subscribed to %d channels",
                    len(self._subscribed_channels),
                )
            self._connected = True
        except Exception as e:
            logger.warning("RedisBroker reconnect failed: %s", e)
            self._connected = False
