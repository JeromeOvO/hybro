"""Direct and relay adapters behind the transport-neutral dispatch port."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol

from .errors import (
    AmbiguousRemoteEffectError,
    RecoverableAdapterError,
    RecoverableTransportError,
)
from .models import (
    A2ACancellationCommand,
    A2AContinuationCommand,
    A2ADispatchCommand,
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from .ports import NormalizedObservationRecorder


class DirectA2AStream(Protocol):
    def __aiter__(self) -> AsyncIterator[NormalizedA2AObservation]: ...

    async def close(self, *, reason: str) -> None: ...


class DirectA2AClient(Protocol):
    """SDK-confined client boundary; only provider-neutral contracts cross it."""

    async def send(self, command: A2ADispatchCommand) -> A2ADispatchReceipt: ...

    async def start_poll(self, command: A2ADispatchCommand) -> A2ADispatchReceipt: ...

    async def open_stream(self, command: A2ADispatchCommand) -> DirectA2AStream: ...

    async def inspect(self, command: A2ADispatchCommand) -> A2ADispatchReceipt: ...

    async def continue_task(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt: ...

    async def inspect_continuation(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt: ...

    async def cancel(self, command: A2ACancellationCommand) -> A2ADispatchReceipt: ...

    async def inspect_cancellation(
        self, command: A2ACancellationCommand
    ) -> A2ADispatchReceipt: ...


class RelayCommandJournal(Protocol):
    async def persist_dispatch(self, command: A2ADispatchCommand) -> str: ...

    async def persist_continuation(self, command: A2AContinuationCommand) -> str: ...

    async def persist_cancellation(self, command: A2ACancellationCommand) -> str: ...

    async def inspect(self, command_id: str) -> A2ADispatchReceipt: ...


class RelayCommandSender(Protocol):
    async def send_dispatch(
        self, command: A2ADispatchCommand
    ) -> A2ADispatchReceipt: ...

    async def send_continuation(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt: ...

    async def send_cancellation(
        self, command: A2ACancellationCommand
    ) -> A2ADispatchReceipt: ...


class DirectA2ADispatchAdapter:
    def __init__(
        self,
        client: DirectA2AClient,
        *,
        observations: NormalizedObservationRecorder | None = None,
    ) -> None:
        self.client = client
        self.observations = observations
        self._active_streams: dict[str, DirectA2AStream] = {}
        self._stream_lock = asyncio.Lock()

    async def dispatch(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        if command.transport_kind != "direct":
            raise ValueError("direct adapter received a relay command")
        if command.direct_mode == "sync":
            try:
                return await self.client.send(command)
            except (ConnectionError, TimeoutError) as exc:
                raise AmbiguousRemoteEffectError(
                    "direct send acknowledgement is ambiguous"
                ) from exc
        if command.direct_mode == "poll":
            try:
                return await self.client.start_poll(command)
            except (ConnectionError, TimeoutError) as exc:
                raise AmbiguousRemoteEffectError(
                    "direct poll-start acknowledgement is ambiguous"
                ) from exc
        if command.direct_mode == "stream":
            return await self._dispatch_stream(command)
        raise ValueError("frozen direct dispatch mode is missing or unsupported")

    async def _dispatch_stream(  # noqa: C901
        self, command: A2ADispatchCommand
    ) -> A2ADispatchReceipt:
        if self.observations is None:
            raise ValueError("direct streaming requires durable observation ingress")
        remaining = (command.deadline_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return A2ADispatchReceipt(outcome="delivery_uncertain")
        stream: DirectA2AStream | None = None
        close_reason = "process_death"
        task_id: str | None = None
        context_id: str | None = None
        try:
            async with asyncio.timeout(remaining):
                stream = await self.client.open_stream(command)
                async with self._stream_lock:
                    if command.call_record_id in self._active_streams:
                        raise ValueError("direct stream already active for call")
                    self._active_streams[command.call_record_id] = stream
                async for event in stream:
                    if event.call_record_id not in {None, command.call_record_id}:
                        raise ValueError("stream event call identity changed")
                    normalized = (
                        event
                        if event.call_record_id == command.call_record_id
                        else event.model_copy(
                            update={"call_record_id": command.call_record_id}
                        )
                    )
                    await self.observations.record(normalized)
                    task_id = normalized.task_id or task_id
                    context_id = normalized.context_id or context_id
                    if normalized.event_kind == "terminal":
                        close_reason = "terminal"
                        return A2ADispatchReceipt(
                            outcome="terminal",
                            task_id=task_id,
                            context_id=context_id,
                            terminal_observation=normalized,
                        )
            return A2ADispatchReceipt(
                outcome="delivery_uncertain",
                task_id=task_id,
                context_id=context_id,
            )
        except TimeoutError:
            close_reason = "deadline"
            return A2ADispatchReceipt(
                outcome="delivery_uncertain",
                task_id=task_id,
                context_id=context_id,
            )
        except ConnectionError as exc:
            close_reason = "transport_ambiguous"
            raise AmbiguousRemoteEffectError(
                "direct stream delivery is ambiguous"
            ) from exc
        except (AmbiguousRemoteEffectError, RecoverableTransportError):
            return A2ADispatchReceipt(
                outcome="delivery_uncertain",
                task_id=task_id,
                context_id=context_id,
            )
        except RecoverableAdapterError:
            close_reason = "persistence_unavailable"
            raise
        except asyncio.CancelledError:
            close_reason = "cancelled"
            raise
        finally:
            if stream is not None:
                async with self._stream_lock:
                    self._active_streams.pop(command.call_record_id, None)
                try:
                    await stream.close(reason=close_reason)
                except (
                    RecoverableAdapterError,
                    RecoverableTransportError,
                    AmbiguousRemoteEffectError,
                    TimeoutError,
                ):
                    # Evidence already entered durable ingress; close failure is
                    # reconciled by inspection rather than escaping as a new outcome.
                    pass

    async def inspect(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        try:
            return await self.client.inspect(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "direct inspection is temporarily unavailable"
            ) from exc

    async def continue_task(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        try:
            return await self.client.continue_task(command)
        except (ConnectionError, TimeoutError) as exc:
            raise AmbiguousRemoteEffectError(
                "direct continuation acknowledgement is ambiguous"
            ) from exc

    async def inspect_continuation(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        try:
            return await self.client.inspect_continuation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "direct continuation inspection is temporarily unavailable"
            ) from exc

    async def cancel(self, command: A2ACancellationCommand) -> A2ADispatchReceipt:
        async with self._stream_lock:
            stream = self._active_streams.get(command.call_record_id)
        if stream is not None:
            try:
                await stream.close(reason="cancelled")
            except (
                RecoverableAdapterError,
                RecoverableTransportError,
                AmbiguousRemoteEffectError,
                TimeoutError,
            ):
                pass
        try:
            return await self.client.cancel(command)
        except (ConnectionError, TimeoutError) as exc:
            raise AmbiguousRemoteEffectError(
                "direct cancellation acknowledgement is ambiguous"
            ) from exc

    async def inspect_cancellation(
        self, command: A2ACancellationCommand
    ) -> A2ADispatchReceipt:
        try:
            return await self.client.inspect_cancellation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "direct cancellation inspection is temporarily unavailable"
            ) from exc

    def is_command_retry_safe(self, transport_kind: str) -> bool:
        return transport_kind == "direct"


class RelayA2ADispatchAdapter:
    def __init__(
        self, *, journal: RelayCommandJournal, sender: RelayCommandSender
    ) -> None:
        self.journal = journal
        self.sender = sender

    async def dispatch(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        if command.transport_kind != "relay":
            raise ValueError("relay adapter received a direct command")
        try:
            outcome = await self.journal.persist_dispatch(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay dispatch journal is temporarily unavailable"
            ) from exc
        if outcome not in {"accepted", "replayed"}:
            raise RuntimeError(f"relay journal rejected dispatch: {outcome}")
        try:
            return await self.sender.send_dispatch(command)
        except (ConnectionError, TimeoutError) as exc:
            raise AmbiguousRemoteEffectError(
                "relay dispatch acknowledgement is ambiguous"
            ) from exc

    async def inspect(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        try:
            return await self.journal.inspect(command.command_id)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay inspection is temporarily unavailable"
            ) from exc

    async def continue_task(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        try:
            outcome = await self.journal.persist_continuation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay continuation journal is temporarily unavailable"
            ) from exc
        if outcome not in {"accepted", "replayed"}:
            raise RuntimeError(f"relay journal rejected continuation: {outcome}")
        try:
            return await self.sender.send_continuation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise AmbiguousRemoteEffectError(
                "relay continuation acknowledgement is ambiguous"
            ) from exc

    async def inspect_continuation(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        try:
            return await self.journal.inspect(command.command_id)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay continuation inspection is temporarily unavailable"
            ) from exc

    async def cancel(self, command: A2ACancellationCommand) -> A2ADispatchReceipt:
        try:
            outcome = await self.journal.persist_cancellation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay cancellation journal is temporarily unavailable"
            ) from exc
        if outcome not in {"accepted", "replayed"}:
            raise RuntimeError(f"relay journal rejected cancellation: {outcome}")
        try:
            return await self.sender.send_cancellation(command)
        except (ConnectionError, TimeoutError) as exc:
            raise AmbiguousRemoteEffectError(
                "relay cancellation acknowledgement is ambiguous"
            ) from exc

    async def inspect_cancellation(
        self, command: A2ACancellationCommand
    ) -> A2ADispatchReceipt:
        try:
            return await self.journal.inspect(command.command_id)
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableTransportError(
                "relay cancellation inspection is temporarily unavailable"
            ) from exc

    def is_command_retry_safe(self, transport_kind: str) -> bool:
        return transport_kind == "relay"


class RoutedA2ADispatchPort:
    def __init__(
        self,
        *,
        direct: DirectA2ADispatchAdapter,
        relay: RelayA2ADispatchAdapter,
    ) -> None:
        self.direct = direct
        self.relay = relay

    def _for(self, transport_kind: str):
        if transport_kind == "direct":
            return self.direct
        if transport_kind == "relay":
            return self.relay
        raise ValueError(f"unsupported A2A transport {transport_kind!r}")

    async def dispatch(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).dispatch(command)

    async def inspect(self, command: A2ADispatchCommand) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).inspect(command)

    async def continue_task(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).continue_task(command)

    async def inspect_continuation(
        self, command: A2AContinuationCommand
    ) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).inspect_continuation(command)

    async def cancel(self, command: A2ACancellationCommand) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).cancel(command)

    async def inspect_cancellation(
        self, command: A2ACancellationCommand
    ) -> A2ADispatchReceipt:
        return await self._for(command.transport_kind).inspect_cancellation(command)

    def is_command_retry_safe(self, transport_kind: str) -> bool:
        return self._for(transport_kind).is_command_retry_safe(transport_kind)
