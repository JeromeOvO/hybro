"""Privacy-safe Assistant text streaming and deterministic public coalescing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

REDACTION = "[REDACTED]"
DEFAULT_COALESCE_INTERVAL_MS = 65
DEFAULT_COALESCE_MAX_UTF8_BYTES = 384
DEFAULT_SANITIZER_LOOKBEHIND_CHARS = 512

_PUBLIC_DSN_SCHEMES = (
    r"(?:https?|mongodb(?:\+srv)?|redis(?:s)?|amqps?|postgres(?:ql)?|mysql)"
)

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(rf"(?i)({_PUBLIC_DSN_SCHEMES}://)([^/@\s:]*):([^/@\s]+)@"),
    re.compile(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth|password|secret|token)=)"
        r"([^&#\s]+)"
    ),
    re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|password|secret|token)"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
)
# Credential values are unbounded, so an unterminated value is retained from
# its marker rather than cut at a fixed look-behind boundary. These expressions
# intentionally also match values shorter than the complete redaction regexes.
_ACTIVE_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]*$"),
    re.compile(rf"(?i){_PUBLIC_DSN_SCHEMES}://[^@\s]*$"),
    re.compile(
        r"(?i)[?&](?:api[_-]?key|access[_-]?token|auth|password|secret|token)="
        r"[^&#\s]*$"
    ),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|token)"
        r"\s*[:=]\s*[^\s,;]*$"
    ),
)

# Suffixes that could grow into a credential marker must also stay raw. The
# flexible scalar forms are covered by retaining a bounded suffix and testing
# whether a harmless probe can make one of the active expressions match.
_CREDENTIAL_MARKER_PROBES = (
    "bearer ",
    "basic ",
    "http://",
    "https://",
    "mongodb://",
    "mongodb+srv://",
    "redis://",
    "rediss://",
    "amqp://",
    "amqps://",
    "postgres://",
    "postgresql://",
    "mysql://",
    "?api_key=",
    "&api_key=",
    "?api-key=",
    "&api-key=",
    "?access_token=",
    "&access_token=",
    "?access-token=",
    "&access-token=",
    "?auth=",
    "&auth=",
    "?password=",
    "&password=",
    "?secret=",
    "&secret=",
    "?token=",
    "&token=",
    "api_key=",
    "api_key:",
    "api-key=",
    "api-key:",
    "access_token=",
    "access_token:",
    "access-token=",
    "access-token:",
    "password=",
    "password:",
    "secret=",
    "secret:",
    "token=",
    "token:",
)
_DSN_MARKER_PROBES = frozenset(
    {
        "mongodb://",
        "mongodb+srv://",
        "redis://",
        "rediss://",
        "amqp://",
        "amqps://",
        "postgres://",
        "postgresql://",
        "mysql://",
    }
)
_STRICT_CREDENTIAL_MARKER_PROBES = tuple(
    marker for marker in _CREDENTIAL_MARKER_PROBES if marker not in _DSN_MARKER_PROBES
)


class PublicTextSanitizer:
    """Release decidable sanitized prefixes while retaining bounded look-behind.

    The retained suffix covers configured secrets and credential patterns split
    across provider chunks. A semantic ``flush`` decides the remaining suffix.
    If an unterminated credential would exceed the bound, the producer receives
    a typed overflow error instead of any unsafe partial value.
    """

    def __init__(
        self,
        *,
        secret_values: Iterable[str] = (),
        replacement: str = REDACTION,
        max_buffer_chars: int = 32_000,
        lookbehind_chars: int = DEFAULT_SANITIZER_LOOKBEHIND_CHARS,
    ) -> None:
        self._secrets = tuple(
            sorted(
                {value for value in secret_values if isinstance(value, str) and value},
                key=len,
                reverse=True,
            )
        )
        self._replacement = replacement
        self._max_public_chars = max_buffer_chars
        self._lookbehind_chars = max(
            lookbehind_chars,
            max((len(secret) for secret in self._secrets), default=1) - 1,
        )
        self._pending = ""
        self._raw_context = ""
        self._public_parts: list[str] = []
        self._public_length = 0

    @property
    def public_text(self) -> str:
        return "".join(self._public_parts)

    def feed(self, chunk: str) -> str:
        if not isinstance(chunk, str):
            raise TypeError("public text chunks must be strings")
        self._pending += chunk
        if self._public_length + len(self._pending) > self._max_public_chars:
            raise ValueError("public text limit exceeded")

        release_at = self._proven_safe_prefix_length()
        if release_at <= 0:
            return ""
        decided = self._pending[:release_at]
        self._pending = self._pending[release_at:]
        return self._append_public(decided)

    def _proven_safe_prefix_length(self) -> int:  # noqa: C901
        """Return a raw prefix that cannot be extended into a protected value.

        Sanitization is applied only after this boundary is chosen. Therefore a
        configured value or credential match is either wholly in the emitted
        prefix (and redacted as one unit) or wholly retained in ``_pending``.
        """

        if not self._pending:
            return 0
        release_at = len(self._pending)
        lowered = self._pending.casefold()

        # Retain a suffix that is a proper prefix of any configured secret.
        for secret in self._secrets:
            folded_secret = secret.casefold()
            if lowered.endswith(folded_secret):
                continue
            max_prefix = min(len(secret) - 1, len(self._pending))
            for size in range(max_prefix, 0, -1):
                if lowered.endswith(folded_secret[:size]):
                    release_at = min(release_at, len(self._pending) - size)
                    break

        # Credential markers themselves are safe to stream. Keep a short raw
        # context of already-released marker text so a marker split across
        # chunks still causes the following value to be retained/redacted,
        # without stalling harmless answers that merely end in (for example)
        # the letter ``r`` from ``redis://``.
        detector_text = self._raw_context + self._pending
        context_length = len(self._raw_context)
        for pattern in _ACTIVE_CREDENTIAL_PATTERNS:
            match = pattern.search(detector_text)
            if match is not None:
                release_at = min(
                    release_at,
                    max(0, match.start() - context_length),
                )

        # Header/query/scalar markers retain partial prefixes so stateful and
        # complete sanitization remain byte-for-byte identical. DSN scheme
        # prefixes use the released raw context above; retaining a one-letter
        # suffix such as ``r`` would otherwise stall ordinary short answers.
        marker_limit = min(
            len(self._pending),
            max(len(value) for value in _STRICT_CREDENTIAL_MARKER_PROBES),
        )
        completed_secret_suffix = max(
            (
                len(secret)
                for secret in self._secrets
                if lowered.endswith(secret.casefold())
            ),
            default=0,
        )
        for size in range(marker_limit, 0, -1):
            if completed_secret_suffix and size <= completed_secret_suffix:
                continue
            suffix = lowered[-size:]
            if any(
                marker.startswith(suffix) for marker in _STRICT_CREDENTIAL_MARKER_PROBES
            ):
                release_at = min(release_at, len(self._pending) - size)
                break

        retained = len(self._pending) - release_at
        if retained > self._lookbehind_chars:
            raise ValueError("public credential token exceeds sanitizer bound")
        return release_at

    def _append_public(self, value: str) -> str:
        combined = self._raw_context + value
        sanitized_context = sanitize_public_text(
            self._raw_context,
            secret_values=self._secrets,
            replacement=self._replacement,
        )
        sanitized_combined = sanitize_public_text(
            combined,
            secret_values=self._secrets,
            replacement=self._replacement,
        )
        sanitized = (
            sanitized_combined[len(sanitized_context) :]
            if sanitized_combined.startswith(sanitized_context)
            else sanitize_public_text(
                value,
                secret_values=self._secrets,
                replacement=self._replacement,
            )
        )
        context_chars = max(len(value) for value in _CREDENTIAL_MARKER_PROBES)
        self._raw_context = combined[-context_chars:]
        self._public_parts.append(sanitized)
        self._public_length += len(sanitized)
        if self._public_length > self._max_public_chars:
            raise ValueError("public text limit exceeded")
        return sanitized

    def flush(self) -> str:
        if not self._pending:
            return ""
        pending = self._pending
        self._pending = ""
        return self._append_public(pending)

    def finish(self) -> str:
        self.flush()
        return self.public_text


def enforce_public_label_policy(
    value: object,
    *,
    secret_values: Iterable[str] = (),
    fallback: str = "Agent",
) -> str:
    """Producer-side policy for canonical Agent/tool/card display labels."""

    label = sanitize_public_text(str(value or ""), secret_values=secret_values).strip()[
        :160
    ]
    return label or fallback


def sanitize_public_text(
    text: str,
    *,
    secret_values: Iterable[str] = (),
    replacement: str = REDACTION,
) -> str:
    """Sanitize one complete public text checkpoint deterministically."""

    sanitized = text
    for secret in sorted(
        {value for value in secret_values if isinstance(value, str) and value},
        key=len,
        reverse=True,
    ):
        sanitized = sanitized.replace(secret, replacement)
    sanitized = _CREDENTIAL_PATTERNS[0].sub(replacement, sanitized)
    sanitized = _CREDENTIAL_PATTERNS[1].sub(
        lambda match: f"{match.group(1)}{replacement}@", sanitized
    )
    for pattern in _CREDENTIAL_PATTERNS[2:]:
        sanitized = pattern.sub(
            lambda match: f"{match.group(1)}{replacement}", sanitized
        )
    return sanitized


@dataclass(frozen=True, slots=True)
class PublicTextDelta:
    event_id: str
    content_index: int
    delta_index: int
    start_offset: int
    end_offset: int
    delta: str


class PublicTextCoalescer:
    """Coalesce sanitized text and assign restart-stable semantic identity."""

    def __init__(
        self,
        *,
        run_id: str,
        internal_turn_id: str,
        message_id: str,
        content_index: int = 0,
        start_offset: int = 0,
        next_delta_index: int = 0,
        interval_ms: int = DEFAULT_COALESCE_INTERVAL_MS,
        max_utf8_bytes: int = DEFAULT_COALESCE_MAX_UTF8_BYTES,
    ) -> None:
        self.run_id = run_id
        self.internal_turn_id = internal_turn_id
        self.message_id = message_id
        self.content_index = content_index
        self.offset = start_offset
        self.delta_index = next_delta_index
        self.interval = timedelta(milliseconds=interval_ms)
        self.max_utf8_bytes = max_utf8_bytes
        self._pending = ""
        self._last_flush_at: datetime | None = None

    def add(self, text: str, *, now: datetime) -> list[PublicTextDelta]:
        if text:
            self._pending += text
            if self._last_flush_at is None:
                self._last_flush_at = now
        deltas: list[PublicTextDelta] = []
        while len(self._pending.encode("utf-8")) >= self.max_utf8_bytes:
            deltas.append(self._flush_prefix(now=now, force_bound=True))
        if (
            self._pending
            and self._last_flush_at is not None
            and now - self._last_flush_at >= self.interval
        ):
            deltas.append(self._flush_prefix(now=now, force_bound=False))
        return deltas

    def timed_flush(self, *, now: datetime) -> list[PublicTextDelta]:
        """Flush pending safe text when the wall-clock timer fires."""

        if not self._pending:
            return []
        return [self._flush_prefix(now=now, force_bound=False)]

    def semantic_flush(self, *, now: datetime) -> list[PublicTextDelta]:
        deltas: list[PublicTextDelta] = []
        while self._pending:
            deltas.append(self._flush_prefix(now=now, force_bound=True))
        return deltas

    def flush(self, *, now: datetime) -> PublicTextDelta:
        if not self._pending:
            raise ValueError("cannot flush an empty public text coalescer")
        return self._flush_prefix(now=now, force_bound=True)

    def _flush_prefix(self, *, now: datetime, force_bound: bool) -> PublicTextDelta:
        if not self._pending:
            raise ValueError("cannot flush an empty public text coalescer")
        delta = self._bounded_prefix() if force_bound else self._pending
        self._pending = self._pending[len(delta) :]
        start = self.offset
        end = start + len(delta)
        digest = hashlib.sha256(delta.encode("utf-8")).hexdigest()[:24]
        identity = ":".join(
            (
                self.run_id,
                self.internal_turn_id,
                self.message_id,
                str(self.content_index),
                str(start),
                str(end),
                digest,
            )
        )
        result = PublicTextDelta(
            event_id=f"public:text:{hashlib.sha256(identity.encode()).hexdigest()}",
            content_index=self.content_index,
            delta_index=self.delta_index,
            start_offset=start,
            end_offset=end,
            delta=delta,
        )
        self.offset = end
        self.delta_index += 1
        self._last_flush_at = now
        return result

    def _bounded_prefix(self) -> str:
        used = 0
        chars: list[str] = []
        for char in self._pending:
            width = len(char.encode("utf-8"))
            if chars and used + width > self.max_utf8_bytes:
                break
            if not chars and width > self.max_utf8_bytes:
                raise ValueError("UTF-8 chunk bound is smaller than one code point")
            chars.append(char)
            used += width
        return "".join(chars)


__all__ = [
    "DEFAULT_COALESCE_INTERVAL_MS",
    "DEFAULT_COALESCE_MAX_UTF8_BYTES",
    "DEFAULT_SANITIZER_LOOKBEHIND_CHARS",
    "PublicTextCoalescer",
    "PublicTextDelta",
    "PublicTextSanitizer",
    "REDACTION",
    "sanitize_public_text",
]
