"""Fail-closed authorization refresh helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .errors import RecoverableAuthorizationError
from .models import AgentToolBindingRecord

AuthorizationCheck = Callable[
    [AgentToolBindingRecord, str, str, int, list[str]],
    str | Awaitable[str],
]
AuthReferenceCheck = Callable[
    [
        str,
        str,
        str,
        str,
        str,
        str,
        int,
        str,
        int,
        str,
        str,
        str,
        str,
        str,
    ],
    str | Awaitable[str],
]


class CallableAuthorizationRefresh:
    def __init__(self, check: AuthorizationCheck) -> None:
        self.check = check

    async def authorize(
        self,
        *,
        binding: AgentToolBindingRecord,
        requesting_subject_id: str,
        room_id: str,
        room_epoch: int,
        resource_refs: list[str],
    ) -> str:
        try:
            result = self.check(
                binding,
                requesting_subject_id,
                room_id,
                room_epoch,
                resource_refs,
            )
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableAuthorizationError(
                "authorization owner is temporarily unavailable"
            ) from exc
        if result not in {"authorized", "denied", "transient_failure"}:
            return "transient_failure"
        return result


class CallableAuthReferenceVerification:
    """Fail-closed adapter for owner-issued, call-bound authorization references."""

    def __init__(self, check: AuthReferenceCheck) -> None:
        self.check = check

    async def verify(
        self,
        authorization_reference: str,
        *,
        authenticated_answerer_id: str,
        call_record_id: str,
        binding_id: str,
        binding_digest: str,
        room_id: str,
        room_epoch: int,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        interaction_fingerprint: str,
        question_id: str,
        challenge_digest: str,
        answer_digest: str,
    ) -> str:
        try:
            result = self.check(
                authorization_reference,
                authenticated_answerer_id,
                call_record_id,
                binding_id,
                binding_digest,
                room_id,
                room_epoch,
                interaction_id,
                interaction_revision,
                route_fingerprint,
                interaction_fingerprint,
                question_id,
                challenge_digest,
                answer_digest,
            )
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
        except (ConnectionError, TimeoutError) as exc:
            raise RecoverableAuthorizationError(
                "authorization proof owner is temporarily unavailable"
            ) from exc
        if not isinstance(result, str) or not result:
            raise PermissionError("authorization reference verification failed")
        return result
