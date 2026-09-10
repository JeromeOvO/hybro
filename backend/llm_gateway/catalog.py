"""Small capability catalog shared by setup and the canonical runtime.

Limits are backend operating ceilings, never larger than the Provider limits.
Only implemented adapters are eligible. Subscription IDs come from pi-ai 0.73.1;
account entitlement is checked by setup's single text verification, not assumed.
"""

from dataclasses import dataclass
from typing import Literal

from common.config.runtime_config import (
    RuntimeConfig,
    RuntimeConfigurationError,
    RuntimeProvider,
)
from llm_gateway.setup_service import ModelChoices


@dataclass(frozen=True, slots=True)
class TextModel:
    provider: Literal["openai", "deepseek", "anthropic"]
    model_id: str
    api: Literal["chat_completions", "responses", "messages"]
    context_window: int
    max_output_tokens: int
    thinking_levels: tuple[str, ...] = ()
    auth: Literal["api_key", "oauth"] = "api_key"
    operations: frozenset[str] = frozenset({"text_stream", "tools", "structured_json"})


# OpenAI model documentation: /docs/models/gpt-5-mini and /gpt-4o-mini.
# DeepSeek: api-docs.deepseek.com (V4 Flash, Chat Completions, JSON Output).
# Anthropic: platform.claude.com/docs/en/about-claude/models/overview.
# Haiku 4.5: 200K context / 64K output; gateway ceiling is 32K, thinking off.
TEXT_MODELS = (
    # Prefer gpt-5.5 for new setups; retain older IDs for explicit selections.
    # Catalog membership alone does not guarantee current account availability.
    TextModel(
        "openai",
        "gpt-5.5",
        "responses",
        272_000,
        32_768,
        ("minimal", "low", "medium", "high", "xhigh"),
        auth="oauth",
    ),
    *(
        TextModel(
            "openai",
            model,
            "responses",
            128_000 if model == "gpt-5.3-codex-spark" else 272_000,
            32_768,
            ("minimal", "low", "medium", "high")
            if model.startswith("gpt-5.1")
            else ("minimal", "low", "medium", "high", "xhigh"),
            auth="oauth",
        )
        for model in (
            "gpt-5.1",
            "gpt-5.1-codex-max",
            "gpt-5.1-codex-mini",
            "gpt-5.2",
            "gpt-5.2-codex",
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
            "gpt-5.4-mini",
            "gpt-5.4",
        )
    ),
    TextModel(
        "openai", "gpt-5-mini", "responses", 400_000, 32_768, ("low", "medium", "high")
    ),
    TextModel("openai", "gpt-4o-mini", "chat_completions", 128_000, 16_384),
    TextModel("deepseek", "deepseek-v4-flash", "chat_completions", 128_000, 8192),
    TextModel("anthropic", "claude-haiku-4-5-20251001", "messages", 200_000, 32_768),
)


def model_choices(provider: RuntimeProvider) -> ModelChoices:
    return ModelChoices(
        text=tuple(
            entry.model_id
            for entry in TEXT_MODELS
            if entry.provider == provider.id and provider.auth == entry.auth
        ),
        image=("gpt-image-1",)
        if provider.id == "openai" and provider.auth == "api_key"
        else (),
    )


def validate_models(config: RuntimeConfig) -> TextModel:
    choices = model_choices(config.provider)
    if config.models.text not in choices.text or (
        config.models.image is not None and config.models.image not in choices.image
    ):
        raise RuntimeConfigurationError(
            "Selected Provider/auth or models are not implemented/eligible; rerun setup."
        )
    return next(
        entry
        for entry in TEXT_MODELS
        if entry.provider == config.provider.id
        and entry.model_id == config.models.text
        and entry.auth == config.provider.auth
    )
