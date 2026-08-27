from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from execution.orchestrator.public_text import (
    REDACTION,
    PublicTextCoalescer,
    PublicTextSanitizer,
    sanitize_public_text,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("split", range(1, len("prefix top-secret suffix")))
def test_configured_secret_is_redacted_at_every_provider_split(split: int):
    raw = "prefix top-secret suffix"
    sanitizer = PublicTextSanitizer(secret_values=["top-secret"])
    sanitizer.feed(raw[:split])
    sanitizer.feed(raw[split:])
    public = sanitizer.finish()
    assert public == f"prefix {REDACTION} suffix"
    assert "top-secret" not in public


@pytest.mark.parametrize(
    "dsn",
    [
        "mongodb://dbuser:dbpass@example.test/database",
        "mongodb+srv://dbuser:dbpass@example.test/database",
        "redis://:redispass@example.test/0",
        "rediss://redisuser:redispass@example.test/0",
    ],
)
def test_configured_dsn_credentials_are_redacted_at_every_stream_split(dsn: str):
    expected = sanitize_public_text(f"connect {dsn} now")
    assert "dbpass" not in expected
    assert "redispass" not in expected
    for split in range(1, len(dsn)):
        sanitizer = PublicTextSanitizer()
        sanitizer.feed(f"connect {dsn}"[:split])
        sanitizer.feed(f"connect {dsn}"[split:] + " now")
        public = sanitizer.finish()
        assert "dbpass" not in public
        assert "redispass" not in public
        assert "[REDACTED]" in public


def test_configured_secret_inventory_extracts_percent_decoded_dsn_credentials():
    from types import SimpleNamespace

    from orchestrator_composition import configured_public_secret_values

    class Config(SimpleNamespace):
        model_fields = {"mongodb_url": object(), "redis_url": object()}

    config = Config(
        mongodb_url="mongodb://dbuser:db%2Dpass@example.test/db",
        redis_url="redis://:redispass@example.test/0",
    )
    assert set(configured_public_secret_values(config)) == {
        "dbuser",
        "db-pass",
        "redispass",
    }


def test_credential_patterns_cover_headers_urls_queries_and_scalar_keys():
    raw = (
        "Bearer abcdefgh https://user:pass@example.test/path?api_key=xyz123 "
        "password: hunter2"
    )
    public = sanitize_public_text(raw)
    assert "abcdefgh" not in public
    assert "user:pass" not in public
    assert "xyz123" not in public
    assert "hunter2" not in public
    assert public.count(REDACTION) == 4


def test_coalescer_uses_unicode_offsets_and_deterministic_identity():
    first = PublicTextCoalescer(
        run_id="run-1",
        internal_turn_id="turn-1",
        message_id="message-1",
        max_utf8_bytes=4,
    )
    second = PublicTextCoalescer(
        run_id="run-1",
        internal_turn_id="turn-1",
        message_id="message-1",
        max_utf8_bytes=4,
    )
    a = first.add("好a", now=NOW)[0]
    b = second.add("好a", now=NOW + timedelta(seconds=10))[0]
    assert (a.start_offset, a.end_offset, a.delta_index) == (0, 2, 0)
    assert a.event_id == b.event_id


@pytest.mark.parametrize("start", [1, 7, 31, 63, 127, 255, 510, 511, 512, 513])
def test_configured_secret_is_never_split_at_release_boundaries(start: int):
    raw = f"{'a' * start}top-secret{'z' * 700}"
    sanitizer = PublicTextSanitizer(secret_values=["top-secret"])
    chunks = [sanitizer.feed(raw[: start + 2]), sanitizer.feed(raw[start + 2 :])]
    chunks.append(sanitizer.finish())
    public = sanitizer.public_text
    assert "top-secret" not in public
    assert public == sanitize_public_text(raw, secret_values=["top-secret"])
    assert "".join(part for part in chunks[:-1] if part) in public


@pytest.mark.parametrize(
    "credential",
    [
        "Bearer abcdefghijklmnop",
        "Basic abcdefghijklmnop",
        "https://user:password@example.test/path",
        "?api_key=abcdefghijklmnop&ok=1",
        " token: abcdefghijklmnop; done",
    ],
)
@pytest.mark.parametrize("offset", [1, 63, 255, 511, 512, 513])
def test_credential_forms_are_never_split_at_release_boundaries(
    credential: str, offset: int
):
    raw = f"{'x' * offset}{credential}{' z' * 400}"
    expected = sanitize_public_text(raw)
    for split in range(offset, offset + len(credential) + 1):
        sanitizer = PublicTextSanitizer()
        emitted = sanitizer.feed(raw[:split]) + sanitizer.feed(raw[split:])
        sanitizer.finish()
        assert sanitizer.public_text == expected
        assert credential not in sanitizer.public_text
        assert emitted in expected


def test_sanitizer_releases_short_proven_safe_answer_immediately():
    sanitizer = PublicTextSanitizer(secret_values=["top-secret"])
    released = sanitizer.feed("A short safe answer.")
    assert released == "A short safe answer."
    assert sanitizer.public_text == released


def test_coalescer_splits_at_utf8_bound_without_splitting_code_points():
    coalescer = PublicTextCoalescer(
        run_id="run-1",
        internal_turn_id="turn-1",
        message_id="message-1",
        max_utf8_bytes=4,
    )
    deltas = coalescer.add("好ab好", now=NOW)
    deltas += coalescer.semantic_flush(now=NOW)
    assert [item.delta for item in deltas] == ["好a", "b好"]
    assert all(len(item.delta.encode("utf-8")) <= 4 for item in deltas)
    assert [(item.start_offset, item.end_offset) for item in deltas] == [
        (0, 2),
        (2, 4),
    ]


def test_coalescer_flushes_on_time_size_and_semantic_boundary():
    coalescer = PublicTextCoalescer(
        run_id="run-1",
        internal_turn_id="turn-1",
        message_id="message-1",
        interval_ms=50,
        max_utf8_bytes=5,
    )
    assert coalescer.add("a", now=NOW) == []
    timed = coalescer.add("b", now=NOW + timedelta(milliseconds=50))
    assert [item.delta for item in timed] == ["ab"]
    sized = coalescer.add("12345", now=NOW + timedelta(milliseconds=51))
    assert [item.delta for item in sized] == ["12345"]
    assert coalescer.add("tail", now=NOW + timedelta(milliseconds=52)) == []
    semantic = coalescer.semantic_flush(now=NOW + timedelta(milliseconds=53))
    assert [item.delta for item in semantic] == ["tail"]
