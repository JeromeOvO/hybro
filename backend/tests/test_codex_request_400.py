"""Codex request-shape and bounded HTTP error diagnostics; no live credentials."""

import json
import traceback

import httpx
import pytest

from llm_gateway._diagnostics import _request_diagnostic
from llm_gateway.errors import LLMProviderFailure
from llm_gateway.providers.openai_codex import OpenAICodexProvider, _ordinary_messages
from tests.fakes.llm_runtime import oauth_credential
from tests.test_openai_codex_provider import text_events, turn, wire
from tests.test_openai_oauth import Body


@pytest.mark.parametrize("operation", ["generate", "structured", "stream", "turn"])
async def test_user_input_uses_reference_codex_content_parts(operation):
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        # pi-ai 0.73.1 convertResponsesMessages user-string branch, not an
        # assertion against the public OpenAI SDK's broader shorthand types.
        assert payload["input"] == [
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
        ]
        assert "max_output_tokens" not in payload
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Body(wire(text_events('{"ok":true}'))),
        )

    adapter = OpenAICodexProvider(
        oauth_credential(), transport=httpx.MockTransport(handle)
    )
    messages = [{"role": "user", "content": "hello"}]
    if operation == "turn":
        assert [e async for e in adapter.stream_turn_once(turn())][
            -1
        ].finish_reason == "stop"
    elif operation == "stream":
        assert [s async for s in adapter.generate_stream(messages, "gpt-5.4")] == [
            '{"ok":true}'
        ]
    elif operation == "structured":
        assert (
            await adapter.generate_structured(messages, "gpt-5.4", json_mode=True)
        ).data == {"ok": True}
    else:
        assert (await adapter.generate(messages, "gpt-5.4")).content == '{"ok":true}'
    assert len(calls) == 1
    assert messages == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize(
    "envelope,code,parameter",
    [
        (
            {
                "error": {
                    "code": "invalid_type",
                    "param": "input[0].content",
                    "message": "private-secret",
                }
            },
            "invalid_type",
            "input.content",
        ),
        (
            {"detail": "Unsupported parameter: temperature"},
            "unsupported_parameter",
            "temperature",
        ),
        ({"detail": "Input must be a list"}, "invalid_type", "input"),
        (
            {"detail": "Instructions are required"},
            "missing_required_parameter",
            "instructions",
        ),
        (
            {
                "detail": [
                    {
                        "type": "list_type",
                        "loc": ["body", "input", 0, "content"],
                        "input": "private-secret",
                    }
                ]
            },
            "invalid_type",
            "input.content",
        ),
        (
            {
                "error": {
                    "message": "Invalid type for 'input[0].content': expected an array, but got a string instead."
                }
            },
            "invalid_type",
            "input.content",
        ),
        ({"detail": "private-secret"}, None, None),
        ({"error": {"param": "private-secret", "code": "private-secret"}}, None, None),
        ({"detail": ["private-secret"]}, None, None),
        ({"detail": {"type": [], "param": {}}}, None, None),
    ],
)
async def test_http_400_retains_only_known_facts_and_closes(envelope, code, parameter):
    body = Body(json.dumps(envelope).encode())
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            400, headers={"content-type": "application/json"}, stream=body
        )

    adapter = OpenAICodexProvider(
        oauth_credential(), transport=httpx.MockTransport(handle)
    )
    with pytest.raises(LLMProviderFailure) as caught:
        await adapter.generate([{"role": "user", "content": "hello"}], "gpt-5.4")
    diagnostic = caught.value._diagnostic
    assert diagnostic.status == 400 and diagnostic.category == "invalid_request"
    assert (diagnostic.code, diagnostic.parameter) == (code, parameter)
    assert "private-secret" not in repr(
        vars(caught.value)
    ) + diagnostic.describe() + "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert body.closed and len(calls) == 1


@pytest.mark.parametrize("raw", [b"not-json private-secret", b"x" * 65537])
async def test_unparseable_or_oversized_http_body_still_reports_status(raw):
    body = Body(raw)
    adapter = OpenAICodexProvider(
        oauth_credential(),
        transport=httpx.MockTransport(lambda _: httpx.Response(400, stream=body)),
    )
    with pytest.raises(LLMProviderFailure) as caught:
        await adapter.generate([], "gpt-5.4")
    assert caught.value._diagnostic.status == 400
    assert caught.value._diagnostic.code is None
    assert body.closed


def test_unknown_detail_never_echoes_provider_text():
    for text in [
        "sk-private",
        "https://callback.invalid?code=secret",
        "\x1b[31msecret",
        "secret" * 1000,
    ]:
        diagnostic = _request_diagnostic(400, {"detail": text})
        assert text not in diagnostic.describe()
        assert diagnostic.code is None and diagnostic.parameter is None


def test_user_message_conversion_preserves_unicode_and_order():
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "user", "content": "second"},
    ]
    assert _ordinary_messages(messages)[1] == [
        {"role": "user", "content": [{"type": "input_text", "text": "你好"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "second"}]},
    ]
