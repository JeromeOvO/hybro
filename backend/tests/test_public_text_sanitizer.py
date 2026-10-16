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
        "Bearer ab12cd34ef56gh78 https://user:pass@example.test/path?api_key=xyz123 "
        "password: hunter2"
    )
    public = sanitize_public_text(raw)
    assert "ab12cd34ef56gh78" not in public
    assert "user:pass" not in public
    assert "xyz123" not in public
    assert "hunter2" not in public
    assert public.count(REDACTION) == 4


@pytest.mark.parametrize(
    "token",
    [
        # Bearer: 16+ token characters with a digit.
        "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
        "bearer ab12cd34ef56gh78",
        "Bearer 0123456789abcdef",
        "Bearer 12345678901234567890",
        "Bearer sk-proj-ABCDEF1234567890",
        "Bearer ghp_A1b2C3d4E5f6G7h8I9j0",
        "Bearer dead-beef-cafe-babe-0001",
        "Bearer abcdefghij1234567890-xyz",
        "Bearer\nab12cd34ef56gh78",
        # Basic: standard base64 of user:password.
        "Basic dXNlcjpwYXNz",
        "Basic dXNlcjpwYXNzd29yZA==",
        "Basic YWxhZGRpbjpvcGVuc2VzYW1l",
    ],
)
def test_auth_scheme_tokens_are_redacted(token: str):
    assert sanitize_public_text(f"use {token} here") == f"use {REDACTION} here"


@pytest.mark.parametrize(
    "prose",
    [
        "Language: Basic French greetings are appreciated.",
        "A basic understanding of the city helps.",
        "Bearer bonds were common then.",
        "Start with Basic 101 lessons.",
        "Learn basic JavaScript today",
        "The math is basic arithmetic.",
        "Learn Basic PowerPoint",
        "A basic step-by-step guide",
        "Use Basic English.",
        "basic knowledge-sharing",
        "A basic\nunderstanding.",
        "Learn basic input/output.",
        "basic HTML/CSS",
        "basic read/write ops",
        "Basic 10-minute setup",
        "basic C++/Java",
        "basic JavaScript-TypeScript",
        "Basic IPv6-only networks",
        "BASIC TRAINING",
        "## BASIC REQUIREMENTS",
        "THE BEARER INSTRUMENT",
        "basic JavaScript/TypeScript",
        "basic IPv4/IPv6",
        "Basic iOS17/Android14",
        "basic internationalization",
        "Basic test",
        "Bearer Passw0rd",
        "pallbearer ab12cd34ef56gh78 ok",
    ],
)
def test_ordinary_words_after_basic_or_bearer_are_kept(prose: str):
    assert sanitize_public_text(prose) == prose


def test_sentence_punctuation_after_a_token_is_kept():
    assert sanitize_public_text("Use Bearer ab12cd34ef56gh78.") == f"Use {REDACTION}."


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
        "Bearer abcd1234efgh5678",
        "Basic dXNlcjpwYXNzd29yZA==",
        "https://user:password@example.test/path",
        "?api_key=abcdefghijklmnop&ok=1",
        " token: abcdefghijklmnop; done",
    ],
)
@pytest.mark.parametrize("offset", [1, 63, 255, 511, 512, 513])
def test_credential_forms_are_never_split_at_release_boundaries(
    credential: str, offset: int
):
    # The padding ends in a space: "xBearer" is not an auth scheme.
    raw = f"{'x' * (offset - 1)} {credential}{' z' * 400}"
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


@pytest.mark.parametrize(
    "raw",
    [
        "Use Bearer ab12cd34ef56gh78. Then BASIC TRAINING starts.",
        "basic JavaScript/TypeScript and Basic dXNlcjpwYXNz- ok",
        "pallbearer ab12cd34ef56gh78 ok",
        # A scheme word inside a token is part of that token.
        "Use Bearer abcdefghij1.Bearer now",
        "Use Bearer ab12cd34ef/basic now",
        "Bearer abc.def.123-Basic\tx",
        "Bearer abc1defghij=basic x",
        "Bearer abc1defghij.bearer now",
        "Bearer sk-1234567-basic ok",
        "Basic dXNlcjpwYXNzd29yZA==basic end",
        # A scalar marker inside a token does not split it either.
        "bearer\tVQVxG45xssu9V1-token:BASIC",
    ],
)
def test_streaming_matches_one_shot_for_auth_schemes_at_every_split(raw: str):
    expected = sanitize_public_text(raw)
    for split in range(len(raw) + 1):
        sanitizer = PublicTextSanitizer()
        sanitizer.feed(raw[:split])
        sanitizer.feed(raw[split:])
        sanitizer.finish()
        assert sanitizer.public_text == expected


@pytest.mark.parametrize(
    ("raw", "public"),
    [
        # A scheme word is never the other scheme's token.
        (
            "use basic Bearer 0123456789abcdef0123 now",
            f"use basic {REDACTION} now",
        ),
        ("Bearer Basic YWxpY2U6czNjcmV0", f"Bearer {REDACTION}"),
        (
            "Schemes: Basic\nBearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig123",
            f"Schemes: Basic\n{REDACTION}",
        ),
        # The trailing "-" counts toward the token's length but stays visible.
        ("Bearer ab12cd34ef56gh7-", f"{REDACTION}-"),
        # A scheme word followed by a token character is part of the token.
        ("Bearer Bearer-ab12cd34ef56gh78ij90", REDACTION),
        ("Bearer basic.ab12cd34ef56gh78ij90", REDACTION),
        # A sentence-final "." does not count toward the 16 characters.
        ("Bearer abcdefghijklmn1. ok", "Bearer abcdefghijklmn1. ok"),
        ("Bearer abcdefghijklmn12. ok", f"{REDACTION}. ok"),
        # Basic values must be padded standard base64 (RFC 7617).
        ("Basic dXNlcjpwYXNzd29yZA", "Basic dXNlcjpwYXNzd29yZA"),
    ],
)
def test_auth_scheme_edge_cases(raw: str, public: str):
    assert sanitize_public_text(raw) == public


@pytest.mark.parametrize(
    "raw",
    [
        "Superbasic dXNlcjpwYXNz ok",
        "tribasic dXNlcjpwYXNzd29yZA== then",
        "nonbearer ab12cd34ef56gh78ij90 ok",
        "use basic Bearer 0123456789abcdef0123 now",
        "Bearer Basic YWxpY2U6czNjcmV0 end",
        "0123456789aBearer ab12cd34ef56gh78ij90 ok",
        "0123456789abBasic dXNlcjpwYXNz ok",
    ],
)
def test_streaming_one_character_at_a_time_matches_one_shot(raw: str):
    sanitizer = PublicTextSanitizer()
    for character in raw:
        sanitizer.feed(character)
    sanitizer.finish()
    assert sanitizer.public_text == sanitize_public_text(raw)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Use Bearer ab12cd34ef56basic ", "now"),
        ("Use Bearer ab12cd34ef56Bearer ", "now"),
        ("Basic dXNlcjpwYXNzd29yZA==basic ", "end"),
        ("Use Bearer abcdefghij1.Bearer ", "now"),
    ],
)
def test_streaming_never_splits_a_token_at_an_embedded_scheme_word(
    first: str, second: str
):
    sanitizer = PublicTextSanitizer()
    sanitizer.feed(first)
    sanitizer.feed(second)
    sanitizer.finish()
    assert sanitizer.public_text == sanitize_public_text(first + second)
