"""Setup verification uses the same adapters, with all provider traffic mocked."""

import io
import json

import httpx
import pytest
from openai import AsyncOpenAI

from llm_gateway.providers import AnthropicProvider
from llm_gateway.setup_cli import main
from tests.test_anthropic_provider import Body, start, stop, text_block, wire


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-4o-mini"),
        ("openai", "gpt-5-mini"),
        ("deepseek", "deepseek-v4-flash"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
@pytest.mark.parametrize("status", [200, 401, 429, 500])
def test_setup_verifies_selected_adapter_once_then_saves(
    tmp_path, monkeypatch, provider, model, status, image_model="none"
):
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == model
        if status != 200:
            return httpx.Response(
                status,
                stream=Body(b'{"error":{"message":"private-key"}}'),
                headers={"content-type": "application/json"},
            )
        if provider == "anthropic":
            assert "JSON object must conform" not in payload["system"]
            return httpx.Response(
                200,
                stream=Body(wire([start(), *text_block('{"ok":true}'), *stop()])),
                headers={"content-type": "text/event-stream"},
            )
        assert "response_format" not in payload
        if model == "gpt-5-mini":
            assert "max_tokens" not in payload
            assert payload["max_completion_tokens"] == 4096
        return httpx.Response(
            200,
            json={
                "id": "wire-id",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr("llm_gateway.providers.openai_provider.AsyncOpenAI", factory)
    monkeypatch.setattr("llm_gateway.providers.deepseek_provider.AsyncOpenAI", factory)
    monkeypatch.setattr(
        "llm_gateway.setup_bindings.AnthropicProvider",
        lambda **kwargs: AnthropicProvider(
            **kwargs, transport=httpx.MockTransport(handle)
        ),
    )
    error = io.StringIO()
    result = main(
        [
            "--non-interactive",
            "--provider",
            provider,
            "--auth",
            "api_key",
            "--text-model",
            model,
            "--image-model",
            image_model,
        ],
        environment={
            "HOME": str(tmp_path),
            f"{provider.upper()}_API_KEY": "fixture-only",
        },
        output=io.StringIO(),
        error_output=error,
    )
    assert result == (0 if status == 200 else 1)
    assert len(calls) == 1
    assert (tmp_path / ".hybro/config.yaml").exists() is (status == 200)
    assert not (tmp_path / ".hybro/auth.json").exists()
    assert "private-key" not in error.getvalue()


def test_image_setup_performs_only_text_verification(tmp_path, monkeypatch):
    test_setup_verifies_selected_adapter_once_then_saves(
        tmp_path, monkeypatch, "openai", "gpt-4o-mini", 200, "gpt-image-1"
    )
    from llm_gateway.runtime_store import RuntimeConfigStore

    state = RuntimeConfigStore(tmp_path / ".hybro").load(
        {"OPENAI_API_KEY": "fixture-only"}
    )
    assert state.config.models.image == "gpt-image-1"


def test_production_catalog_offers_optional_images_only_for_eligible_provider():
    from llm_gateway.catalog import model_choices
    from llm_gateway.runtime_config import RuntimeProvider

    for provider in ("openai", "deepseek", "anthropic"):
        expected = ("gpt-image-1",) if provider == "openai" else ()
        assert (
            model_choices(RuntimeProvider(id=provider, auth="api_key")).image
            == expected
        )
