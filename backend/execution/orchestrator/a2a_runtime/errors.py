"""Provider-neutral recoverable adapter and persistence failures."""

from __future__ import annotations


class RecoverableAdapterError(RuntimeError):
    """An accepted operation could not classify a transient adapter/store outage.

    Adapters translate provider-specific operational exceptions to this boundary.
    Contract violations, invalid durable documents, and programming errors must not
    be translated.
    """


class RecoverableCheckpointError(RecoverableAdapterError):
    """A durable generic checkpoint could not be read temporarily."""


class RecoverableAuthorizationError(RecoverableAdapterError):
    """Authorization owner state is temporarily unavailable."""


class RecoverableEpochError(RecoverableAdapterError):
    """Room epoch authority is temporarily unavailable."""


class StaleRoomEpochError(RecoverableEpochError):
    """A side effect targeted a Room incarnation that is no longer active.

    Raised when a write-lease fence observes an inactive room epoch. Treated as
    recoverable so callers suspend into delivery-uncertain recovery instead of
    converting epoch loss into a terminal agent failure.
    """


class RecoverableResourceError(RecoverableAdapterError):
    """Resource owner/materialization is temporarily unavailable."""


class RecoverableTransportError(RecoverableAdapterError):
    """Transport failed before a remote side effect was known to be possible."""


class AmbiguousRemoteEffectError(RecoverableAdapterError):
    """Transport may have produced a remote effect before acknowledgement failed."""
