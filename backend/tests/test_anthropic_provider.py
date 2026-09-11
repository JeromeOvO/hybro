"""Messages wire fixtures follow Anthropic's documented SSE examples; no live I/O."""

import asyncio
import gzip
import json
import zlib
from contextlib import aclosing

import httpx
import pytest

from common.observability.logging import bind_log_context
from llm_gateway.error_classification import classify_gateway_error
from llm_gateway.errors import LLMProviderFailure
from llm_gateway.providers import AnthropicProvider
from llm_gateway.providers import anthropic_provider as adapter
from llm_gateway.turn_types import (
    GatewayTextPart,
    GatewayToolCallPart,
    GatewayToolDefinition,
    GatewayToolResultPart,
    GatewayTurnMessage,
    GatewayTurnRequest,
)

MODEL = "claude-haiku-4-5-20251001"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}
TOOL = GatewayToolDefinition(
    name="lookup",
    description="Find a city",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


def turn(**updates):
    return GatewayTurnRequest(
        **{
            "provider": "openai",
            "model_id": MODEL,
            "api": "chat_completions",
            "system_prompt": "System rules",
            "messages": [
                GatewayTurnMessage(role="user", parts=[GatewayTextPart(text="hello")])
            ],
            "tools": [],
            "tool_strategy": "native",
            "max_output_tokens": 100,
            "timeout_seconds": 1,
            "turn_id": "turn-1",
            **updates,
        }
    )


def start():
    return {
        "type": "message_start",
        "message": {
            "id": "msg-not-client-id",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 1,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        },
    }


def text_block(text, index=0):
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": index},
    ]


def tool_block(index=1, call_id="toolu-exact", arguments='{"city":"杭州"}'):
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": "lookup",
                "input": {},
            },
        },
        *[
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": piece,
                },
            }
            for piece in [arguments[:5], arguments[5:]]
        ],
        {"type": "content_block_stop", "index": index},
    ]


def stop(reason="end_turn"):
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": 7},
        },
        {"type": "message_stop"},
    ]


def wire(events):
    return "".join(
        f"event: {event['type']}\r\ndata: {json.dumps(event, ensure_ascii=False)}\r\n\r\n"
        for event in events
    ).encode()


class Body(httpx.AsyncByteStream):
    def __init__(self, data, *, block=False, error=None, chunk_size=7):
        self.data, self.block, self.error = data, block, error
        self.chunk_size = chunk_size
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        # Split inside UTF-8 and CRLF as well as JSON/tool-argument fragments.
        for i in range(0, len(self.data), self.chunk_size):
            yield self.data[i : i + self.chunk_size]
        if self.error:
            raise self.error
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class Transport(httpx.MockTransport):
    def __init__(self, body, *, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}
        self.calls = []
        self.closed = False
        super().__init__(self.handle)

    def handle(self, request):
        self.calls.append(request)
        return httpx.Response(
            self.status,
            stream=self.body,
            headers={
                "content-type": "text/event-stream",
                "request-id": "req-provider-id",
                **self.headers,
            },
        )

    async def aclose(self):
        self.closed = True


def runtime(transport):
    return AnthropicProvider(api_key="test-only-key", transport=transport)


async def collect(transport, request=None):
    return [
        event
        async for event in runtime(transport).stream_turn_once(
            request or turn(), model=MODEL
        )
    ]


async def test_messages_order_tools_results_usage_and_exact_correlation():
    body = Body(
        wire(
            [
                start(),
                *text_block("查找"),
                *tool_block(),
                *tool_block(2, "toolu-second", "{}"),
                *stop("tool_use"),
            ]
        )
    )
    transport = Transport(body)
    messages = [
        GatewayTurnMessage(role="user", parts=[GatewayTextPart(text="hello")]),
        GatewayTurnMessage(
            role="assistant",
            parts=[
                GatewayTextPart(text="before"),
                GatewayToolCallPart(
                    call_id="persisted-a",
                    tool_name="lookup",
                    arguments={"city": "Boston"},
                ),
                GatewayTextPart(text="between"),
                GatewayToolCallPart(
                    call_id="persisted-b", tool_name="lookup", arguments={}
                ),
            ],
        ),
        GatewayTurnMessage(
            role="tool",
            parts=[
                GatewayToolResultPart(
                    call_id="persisted-a",
                    tool_name="lookup",
                    content="sunny",
                )
            ],
        ),
        GatewayTurnMessage(
            role="tool",
            parts=[
                GatewayToolResultPart(
                    call_id="persisted-b",
                    tool_name="lookup",
                    content="unavailable",
                    is_error=True,
                )
            ],
        ),
        GatewayTurnMessage(role="user", parts=[GatewayTextPart(text="continue")]),
    ]
    with bind_log_context(client_request_id="ambient-wrong"):
        events = await collect(
            transport, turn(messages=messages, tools=[TOOL], tool_choice="required")
        )
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.method == "POST"
    assert request.headers["x-api-key"] == "test-only-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["system"] == "System rules"
    assert payload["model"] == MODEL and payload["stream"] is True
    assert payload["max_tokens"] == 100
    assert "thinking" not in payload and "metadata" not in payload
    assert payload["tools"] == [
        {
            "name": TOOL.name,
            "description": TOOL.description,
            "input_schema": TOOL.input_schema,
        }
    ]
    assert payload["tool_choice"] == {"type": "any"}
    assert payload["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "tool_use",
                    "id": "persisted-a",
                    "name": "lookup",
                    "input": {"city": "Boston"},
                },
                {"type": "text", "text": "between"},
                {
                    "type": "tool_use",
                    "id": "persisted-b",
                    "name": "lookup",
                    "input": {},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "persisted-a",
                    "content": "sunny",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "persisted-b",
                    "content": "unavailable",
                    "is_error": True,
                },
                {"type": "text", "text": "continue"},
            ],
        },
    ]
    assert {event.provider_request_id for event in events} == {"req-provider-id"}
    assert events[0].delta == "查找"
    assert [event.call_id for event in events if event.kind == "tool_call_start"] == [
        "toolu-exact",
        "toolu-second",
    ]
    assert (
        "".join(
            event.delta
            for event in events
            if event.kind == "tool_call_arguments_delta"
            and event.call_id == "toolu-exact"
        )
        == '{"city":"杭州"}'
    )
    assert [event.call_id for event in events if event.kind == "tool_call_end"] == [
        "toolu-exact",
        "toolu-second",
    ]
    assert events[-2].usage.model_dump() == {
        "input_tokens": 15,
        "output_tokens": 7,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
    }
    assert events[-1].finish_reason == "tool_calls"
    assert body.closed and transport.closed


@pytest.mark.parametrize(
    "choice,wire_choice",
    [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
    ],
)
async def test_tool_choices(choice, wire_choice):
    tools = choice != "none"
    transport = Transport(
        Body(
            wire(
                [
                    start(),
                    *(tool_block() if tools else text_block("ok")),
                    *stop("tool_use" if tools else "end_turn"),
                ]
            )
        )
    )
    await collect(transport, turn(tools=[TOOL], tool_choice=choice))
    assert json.loads(transport.calls[0].content)["tool_choice"] == wire_choice


async def test_argument_free_tool_emits_empty_object_and_can_be_replayed():
    events = [
        start(),
        tool_block()[0],
        {"type": "content_block_stop", "index": 1},
        *stop("tool_use"),
    ]
    transport = Transport(Body(wire(events)))
    result = await collect(transport, turn(tools=[TOOL]))
    assert result[1].kind == "tool_call_arguments_delta" and result[1].delta == "{}"


@pytest.mark.parametrize(
    "reason,normalized",
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        ("pause_turn", "error"),
        ("future_reason", "error"),
    ],
)
async def test_finish_reasons(reason, normalized):
    transport = Transport(Body(wire([start(), *text_block("ok"), *stop(reason)])))
    assert (await collect(transport))[-1].finish_reason == normalized


@pytest.mark.parametrize(
    "status,kind,retryable",
    [
        (401, "authentication", False),
        (403, "authentication", False),
        (429, "rate_limit", True),
        (500, "provider_5xx", True),
        (529, "provider_5xx", True),
        (504, "timeout", True),
        (400, "invalid_request", False),
        (413, "invalid_request", False),
    ],
)
async def test_http_error_classification_redaction_single_attempt(
    status, kind, retryable
):
    body = Body(
        json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "test-only-key private error",
                }
            }
        ).encode()
    )
    transport = Transport(body, status=status, headers={"retry-after": "2.5"})
    with (
        bind_log_context(client_request_id="wrong"),
        pytest.raises(LLMProviderFailure) as error,
    ):
        await collect(transport)
    assert error.value.client_request_id is None
    classified = classify_gateway_error(error.value)
    assert (classified.error_class, classified.retryable) == (kind, retryable)
    if retryable:
        assert classified.retry_after_seconds == 2.5
    assert "test-only-key" not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert len(transport.calls) == 1 and body.closed and transport.closed


@pytest.mark.parametrize(
    "error_type,kind",
    [
        ("authentication_error", "authentication"),
        ("rate_limit_error", "rate_limit"),
        ("overloaded_error", "provider_5xx"),
        ("api_error", "provider_5xx"),
        ("timeout_error", "timeout"),
        ("invalid_request_error", "invalid_request"),
        ("new_error", "unknown"),
    ],
)
async def test_stream_error_classification(error_type, kind):
    transport = Transport(
        Body(
            wire(
                [
                    start(),
                    *text_block("partial"),
                    {
                        "type": "error",
                        "error": {
                            "type": error_type,
                            "message": "test-only-key private",
                        },
                    },
                ]
            )
        )
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport)
    assert classify_gateway_error(error.value).error_class == kind
    assert error.value.client_request_id is None
    assert "test-only-key" not in str(error.value)
    assert len(transport.calls) == 1 and transport.closed and transport.body.closed


async def test_context_overflow():
    transport = Transport(
        Body(
            b'{"error":{"type":"invalid_request_error","message":"prompt is too long: private"}}'
        ),
        status=400,
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport)
    assert error.value.classification.error_class == "context_overflow"


@pytest.mark.parametrize(
    "exception,kind",
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectTimeout, "timeout"),
        (httpx.ConnectError, "network"),
        (httpx.ReadError, "network"),
        (httpx.RemoteProtocolError, "network"),
    ],
)
@pytest.mark.parametrize("phase", ["connect", "stream"])
async def test_httpx_failures_normalized_at_provider_boundary(exception, kind, phase):
    transport = Transport(Body(wire([start()]), error=exception("test-only-key")))
    if phase == "connect":

        def handle(request):
            transport.calls.append(request)
            raise exception("test-only-key", request=request)

        transport.handler = handle
    provider = AnthropicProvider(api_key="test-only-key", transport=transport)
    with pytest.raises(LLMProviderFailure) as error:
        _ = [event async for event in provider.stream_turn_once(turn(), model=MODEL)]
    assert classify_gateway_error(error.value).error_class == kind
    assert error.value.client_request_id is None
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "test-only-key" not in str(error.value)
    assert len(transport.calls) == 1 and transport.closed
    if phase == "stream":
        assert transport.body.closed


@pytest.fixture
def provider_deadlines(monkeypatch):
    original = asyncio.timeout
    deadlines = []

    def capture(seconds):
        # Both consumers below request ten seconds; do not let rescheduling hide
        # an adapter that ignores the caller's configured deadline.
        assert seconds == 10
        deadline = original(seconds)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(adapter.asyncio, "timeout", capture)
    return deadlines


@pytest.mark.parametrize("mode", ["task", "deadline", "early_close"])
@pytest.mark.parametrize("encoding", ["identity", "gzip"])
async def test_cancellation_closes_pending_read_and_client(
    mode, encoding, provider_deadlines
):
    data = wire([start(), *text_block("first")])
    body = Body(gzip.compress(data) if encoding == "gzip" else data, block=True)
    transport = Transport(body, headers={"content-encoding": encoding})
    signal = asyncio.Event()
    request = turn(timeout_seconds=10)
    tasks_before = set(asyncio.all_tasks())

    async def consume():
        # One consumer owns the iterator and its timeout for the entire turn.
        async with aclosing(
            runtime(transport).stream_turn_once(
                request, model=MODEL, cancel_event=signal
            )
        ) as stream:
            async for event in stream:
                assert event.delta == "first"
                if mode == "early_close":
                    break

    pending = asyncio.create_task(consume())
    try:
        if mode == "early_close":
            await asyncio.wait_for(pending, 1)
        else:
            await asyncio.wait_for(body.waiting.wait(), 1)
            if mode == "task":
                pending.cancel()
            else:
                # Test cleanup at the blocked read, not HTTP client startup speed.
                assert len(provider_deadlines) == 1
                provider_deadlines[0].reschedule(
                    asyncio.get_running_loop().time() + 0.01
                )
            with pytest.raises(
                LLMProviderFailure if mode == "deadline" else asyncio.CancelledError
            ) as error:
                await asyncio.wait_for(pending, 1)
            if mode == "deadline":
                assert error.value.classification.error_class == "timeout"
                assert error.value.client_request_id is None
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert transport.closed and body.closed and len(transport.calls) == 1
    # HTTPX/httpx-sse nested async generators finalize on event-loop turns;
    # their owning response/client have already closed synchronously above.
    for _ in range(10):
        await asyncio.sleep(0)
        if set(asyncio.all_tasks()) == tasks_before:
            break
    assert set(asyncio.all_tasks()) == tasks_before


@pytest.mark.parametrize("limit", ["output", "wire"])
async def test_stream_size_bounds(limit, monkeypatch):
    monkeypatch.setattr(
        f"llm_gateway.providers.anthropic_provider._{limit.upper()}_BYTES", 100
    )
    data = (
        wire([start(), *text_block("x" * 101), *stop()])
        if limit == "output"
        else b"data: " + b"x" * 101
    )
    transport = Transport(Body(data))
    with pytest.raises(LLMProviderFailure):
        await collect(transport)
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize(
    "events",
    [
        [start()],
        [start(), *text_block("ok")],
        [start(), {"type": "message_stop"}],
        [start(), tool_block()[1]],
        [start(), *tool_block(), *stop()],
        [
            start(),
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "not enabled"},
            },
        ],
    ],
)
async def test_malformed_or_unsupported_stream_cannot_succeed(events):
    transport = Transport(Body(wire(events)))
    with pytest.raises(LLMProviderFailure):
        await collect(transport, turn(tools=[TOOL]))
    assert len(transport.calls) == 1 and transport.closed and transport.body.closed


async def test_gzip_and_unknown_metadata_do_not_corrupt_sse():
    data = wire(
        [
            start(),
            {"type": "ping"},
            {"type": "new_metadata", "value": "ignored"},
            *text_block("杭州"),
            *stop(),
        ]
    )
    transport = Transport(
        Body(gzip.compress(data)), headers={"content-encoding": "gzip"}
    )
    assert (await collect(transport))[0].delta == "杭州"


async def test_redirect_is_not_followed_with_api_key():
    transport = Transport(
        Body(b""), status=307, headers={"location": "https://untrusted.example"}
    )
    with pytest.raises(LLMProviderFailure):
        await collect(transport)
    assert len(transport.calls) == 1 and transport.closed


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("line_ending", [b"\r", b"\n", b"\r\n"])
@pytest.mark.parametrize("chunk_size", [1, 7, 4096])
async def test_sse_unicode_text_and_tool_arguments(separator, line_ending, chunk_size):
    text = f"杭州{separator}after"
    arguments = json.dumps({"city": text}, ensure_ascii=False)
    data = wire(
        [
            start(),
            *text_block(text),
            *tool_block(arguments=arguments),
            *stop("tool_use"),
        ]
    ).replace(b"\r\n", line_ending)
    assert separator.encode() in data  # Exercise literal Unicode, not JSON escapes.
    transport = Transport(Body(data, chunk_size=chunk_size))
    events = await collect(transport, turn(tools=[TOOL]))
    assert events[0].delta == text
    assert (
        "".join(
            event.delta for event in events if event.kind == "tool_call_arguments_delta"
        )
        == arguments
    )
    assert events[-1].finish_reason == "tool_calls"
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize(
    "updates,expected",
    [
        ({"input_tokens": None}, (15, 3, 2)),
        ({"cache_read_input_tokens": None}, (15, 3, 2)),
        ({"cache_creation_input_tokens": None}, (15, 3, 2)),
        (
            {
                "input_tokens": None,
                "cache_read_input_tokens": None,
                "cache_creation_input_tokens": None,
            },
            (15, 3, 2),
        ),
        (
            {
                "input_tokens": 20,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 6,
            },
            (30, 4, 6),
        ),
        (
            {
                "input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            (0, 0, 0),
        ),
    ],
)
async def test_usage_merges_non_null_cumulative_counters(updates, expected):
    delta = {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 4}}
    final = stop()
    final[0]["usage"].update(updates)
    transport = Transport(Body(wire([start(), *text_block("ok"), delta, *final])))
    usage = (await collect(transport))[-2].usage
    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
        expected
    )
    assert usage.output_tokens == 7  # Cumulative, not 1 + 4 + 7.


@pytest.mark.parametrize("phase", ["start", "delta"])
@pytest.mark.parametrize(
    "counter",
    [
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ],
)
@pytest.mark.parametrize("value", [-1, 1.5, "test-only-key", True, {}, []])
async def test_malformed_usage_fails_at_provider_boundary(phase, counter, value):
    events = [start(), *text_block("ok"), *stop()]
    usage = events[0]["message"]["usage"] if phase == "start" else events[-2]["usage"]
    usage[counter] = value
    transport = Transport(Body(wire(events)))
    provider = AnthropicProvider(api_key="test-only-key", transport=transport)
    with pytest.raises(LLMProviderFailure) as error:
        _ = [event async for event in provider.stream_turn_once(turn(), model=MODEL)]
    assert error.value.classification.error_class == "invalid_request"
    assert error.value.client_request_id is None
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert "test-only-key" not in str(error.value)
    assert transport.closed and transport.body.closed


class InflaterProbe:
    """Track actual zlib allocations, not HTTPX's already-decoded chunks."""

    def __init__(self, inflater):
        self.inflater = inflater
        self.calls = []

    def decompress(self, data, max_length=0):
        assert 0 < max_length <= adapter._DECODE_CHUNK_BYTES
        assert len(data) <= adapter._DECODE_CHUNK_BYTES
        result = self.inflater.decompress(data, max_length)
        assert len(result) <= max_length
        self.calls.append((len(data), max_length, len(result)))
        return result

    def flush(self, *args):
        pytest.fail("flush length is not an allocation cap")

    @property
    def eof(self):
        return self.inflater.eof

    @property
    def unused_data(self):
        return self.inflater.unused_data

    @property
    def unconsumed_tail(self):
        return self.inflater.unconsumed_tail


@pytest.fixture
def inflater_probes(monkeypatch):
    create = zlib.decompressobj
    probes = []

    def tracked(*args, **kwargs):
        probe = InflaterProbe(create(*args, **kwargs))
        probes.append(probe)
        return probe

    monkeypatch.setattr(adapter.zlib, "decompressobj", tracked)
    return probes


@pytest.mark.parametrize("status", [200, 400, 429])
@pytest.mark.parametrize("chunk_size", [7, 65536])
async def test_gzip_expansion_is_bounded_before_buffering(
    status, chunk_size, inflater_probes
):
    limit = adapter._WIRE_BYTES if status == 200 else adapter._ERROR_BYTES
    data = gzip.compress(b"test-only-key " + b"x" * (20 * 1024 * 1024))
    assert len(data) < limit
    transport = Transport(
        Body(data, chunk_size=chunk_size),
        status=status,
        headers={"content-encoding": "gzip"},
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport, turn(timeout_seconds=10))
    probe = inflater_probes[0]
    assert len(inflater_probes) == 1
    assert sum(output for _, _, output in probe.calls) == limit + 1
    assert max(output for _, _, output in probe.calls) <= 4096
    assert error.value.classification.error_class == (
        "rate_limit" if status == 429 else "invalid_request"
    )
    assert str(error.value) == "Provider text operation failed"
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert error.value.client_request_id is None
    assert len(transport.calls) == 1 and transport.closed and transport.body.closed


@pytest.mark.parametrize("status", [200, 429])
@pytest.mark.parametrize("chunk_size", [7, 65536])
async def test_gzip_raw_total_has_independent_limit(
    status, chunk_size, monkeypatch, inflater_probes
):
    monkeypatch.setattr(
        adapter, "_WIRE_BYTES" if status == 200 else "_ERROR_BYTES", 2048
    )
    decoded = wire([start(), *text_block("ok"), *stop()])
    assert len(decoded) < 2048
    compressed = gzip.compress(decoded)
    # A long legal gzip filename consumes raw budget without decoded output.
    data = compressed[:3] + bytes([compressed[3] | 8]) + compressed[4:10]
    data += b"x" * 2048 + b"\0" + compressed[10:]
    transport = Transport(
        Body(data, chunk_size=chunk_size),
        status=status,
        headers={"content-encoding": "gzip"},
    )
    with pytest.raises(LLMProviderFailure):
        await collect(transport)
    assert sum(output for _, _, output in inflater_probes[0].calls) == 0
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize("size", [4095, 4096, 4097, 8192])
async def test_gzip_exact_decoded_limit_drains_tail_without_unbounded_flush(
    size, monkeypatch, inflater_probes
):
    events = wire([start(), *text_block("ok"), *stop()])
    data = b":" + b" " * (size - len(events) - 2) + b"\n" + events
    assert len(data) == size
    monkeypatch.setattr(adapter, "_WIRE_BYTES", size)
    transport = Transport(
        Body(gzip.compress(data), chunk_size=65536),
        headers={"content-encoding": "gzip"},
    )
    events = await collect(transport)
    assert events[0].delta == "ok" and events[-1].finish_reason == "stop"
    assert sum(output for _, _, output in inflater_probes[0].calls) == size
    assert inflater_probes[0].eof
    assert transport.calls[0].headers["accept-encoding"] == "gzip"
    assert transport.closed and transport.body.closed


async def test_gzip_http_error_preserves_classification_and_redaction(inflater_probes):
    data = b'{"error":{"message":"prompt is too long: test-only-key"}}'
    transport = Transport(
        Body(gzip.compress(data)), status=400, headers={"content-encoding": "gzip"}
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport)
    assert error.value.classification.error_class == "context_overflow"
    assert str(error.value) == "Provider text operation failed"
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize("status", [200, 429])
@pytest.mark.parametrize("damage", ["truncated", "corrupt", "trailing", "encoding"])
async def test_invalid_compression_fails_safely(status, damage):
    # No finish can be emitted from the incomplete SSE fixture.
    data = gzip.compress(wire([start()]))
    encoding = "gzip"
    if damage == "truncated":
        data = data[:-1]
    elif damage == "corrupt":
        data = b"test-only-key"
    elif damage == "trailing":
        data += b"test-only-key"
    else:
        encoding = "gzip, deflate"
    transport = Transport(
        Body(data), status=status, headers={"content-encoding": encoding}
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport)
    assert error.value.classification.error_class == (
        "rate_limit" if status == 429 else "invalid_request"
    )
    assert str(error.value) == "Provider text operation failed"
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize("encoding", ["deflate", "br", "zstd", "test-only-key"])
@pytest.mark.parametrize("status", [200, 429])
async def test_unadvertised_content_encodings_fail_safely(encoding, status):
    transport = Transport(
        Body(b"test-only-key"), status=status, headers={"content-encoding": encoding}
    )
    with pytest.raises(LLMProviderFailure) as error:
        await collect(transport)
    assert error.value.classification.error_class == (
        "rate_limit" if status == 429 else "invalid_request"
    )
    assert str(error.value) == "Provider text operation failed"
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert transport.closed and transport.body.closed


@pytest.mark.parametrize("mode", ["task", "deadline"])
async def test_cancellation_during_gzip_http_error_read(mode, provider_deadlines):
    body = Body(gzip.compress(b'{"error":'), block=True)
    transport = Transport(body, status=429, headers={"content-encoding": "gzip"})
    signal = asyncio.Event()
    request = turn(timeout_seconds=10)

    async def consume():
        async with aclosing(
            runtime(transport).stream_turn_once(
                request, model=MODEL, cancel_event=signal
            )
        ) as stream:
            return [event async for event in stream]

    pending = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(body.waiting.wait(), 1)
        if mode == "task":
            pending.cancel()
        else:
            assert len(provider_deadlines) == 1
            provider_deadlines[0].reschedule(asyncio.get_running_loop().time() + 0.01)
        with pytest.raises(
            LLMProviderFailure if mode == "deadline" else asyncio.CancelledError
        ) as error:
            await asyncio.wait_for(pending, 1)
        if mode == "deadline":
            assert error.value.classification.error_class == "timeout"
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert len(transport.calls) == 1 and transport.closed and body.closed
