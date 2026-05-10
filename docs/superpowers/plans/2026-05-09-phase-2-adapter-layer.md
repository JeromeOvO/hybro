# Phase 2 Adapter Layer Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add concrete A2A protocol and LLM provider adapter implementations that satisfy the Phase 0 Common protocols while keeping external SDK types confined to the adapter layer.

**Architecture:** Create new `a2a_adapter/` and `llm_gateway/` packages that translate between Common DTOs and third-party SDK calling conventions. The adapter layer imports only Common, DAL, and its own third-party SDKs; business prompt composition, orchestration decisions, and task lifecycle behavior stay outside this phase.

**Tech Stack:** Python 3.11+, `a2a-sdk`, `httpx`, `httpx-sse`, OpenAI `AsyncOpenAI`, `google-genai`, `aioboto3`, pytest, pytest-asyncio, stdlib `typing.Protocol`.

---

## Scope

Include:
- New A2A anti-corruption package under `a2a_adapter/`.
- New LLM gateway package under `llm_gateway/`.
- Protocol conformance, import-boundary, translator, routing, and mocked provider tests in `tests/test_adapter_protocols.py` and `tests/test_adapter_unit.py`.
- `pyproject.toml` package list updates for new adapter packages.

Exclude:
- No business module rewiring to use the adapters yet, except tests importing the new concrete classes.
- No prompt migration from `services/openai_service.py`, `services/gemini_service.py`, or `services/bedrock_service.py`.
- No task lifecycle, continuation, room, run, hub, or agent registration logic in adapters.
- No singleton/container wiring in `main.py`.
- No new dependencies; use dependencies already present in `pyproject.toml`.

Reference-only source files:
- A2A transport patterns: `services/a2a_service.py`
- A2A card resolution: `common/client/card_resolver.py`, `common/client/client.py`
- A2A constants: `services/a2a_constants.py`
- OpenAI calling patterns: `services/openai_service.py`
- Gemini calling patterns: `services/gemini_service.py`
- Bedrock calling patterns: `services/bedrock_service.py`
- Settings source: `common/config/settings.py`
- Protocols: `common/protocols/a2a_protocols.py`, `common/protocols/llm_protocols.py`
- DTOs: `common/dto/a2a.py`, `common/dto/agent.py`, `common/dto/llm.py`

Assumptions:
- Phase 0 Common Foundation and Phase 1 DAL are merged to `main`.
- The implementation branch is `refactor/phase-2-adapters` from `main`.
- The installed `a2a-sdk` version exposes `SendMessageRequest` and `SendStreamingMessageRequest`; if the SDK also exposes older task request names, prefer the currently installed message request classes and keep the public adapter API unchanged.
- Legacy reference files may continue to import SDKs until later module migration phases. Phase 2 import-boundary tests must enforce SDK confinement for new adapter packages and current business modules, and must prevent adapter imports from `services/`, `modules/`, `database/`, `infrastructure/`, `models/`, and legacy `config/`.

## File Map

Create:
- `a2a_adapter/__init__.py`: re-export `AgentTransportImpl` and `AgentCardResolverImpl`.
- `a2a_adapter/transport.py`: `AgentTransportImpl` implementing `AgentTransport`.
- `a2a_adapter/card_resolver.py`: `AgentCardResolverImpl` implementing `AgentCardResolver` with TTL cache.
- `a2a_adapter/translators.py`: pure DTO-to-SDK-payload and SDK-response-to-DTO conversion helpers.
- `llm_gateway/__init__.py`: re-export `LLMGatewayImpl` and `ModelRegistryImpl`.
- `llm_gateway/gateway.py`: `LLMGatewayImpl` implementing `LLMProvider` and routing calls to provider instances.
- `llm_gateway/model_registry.py`: `ModelRegistryImpl` with static model metadata and capability lookup.
- `llm_gateway/providers/__init__.py`: re-export provider classes.
- `llm_gateway/providers/openai_provider.py`: OpenAI provider for chat, structured chat, single embeddings, and batch embeddings.
- `llm_gateway/providers/gemini_provider.py`: Gemini provider for text, structured JSON, single embeddings, and batch embeddings.
- `llm_gateway/providers/bedrock_provider.py`: Bedrock provider for text and structured JSON via JSON parsing fallback.
- `llm_gateway/retry.py`: optional shared retry/timeout helper only if provider retry code exceeds 20 lines.
- `tests/test_adapter_protocols.py`: runtime protocol conformance, package exports, packaging, and AST import-boundary tests.
- `tests/test_adapter_unit.py`: mocked translator, card resolver, transport, registry, provider, and gateway routing tests.

Modify:
- `pyproject.toml`: add `a2a_adapter`, `llm_gateway`, and `llm_gateway.providers` to `[tool.setuptools].packages`.

## Task 0: Prepare Branch

**Files:** none

- [ ] **Step 1: Start from `main`**

```bash
git switch main
git pull --ff-only origin main
git switch -c refactor/phase-2-adapters
```

Expected: branch is `refactor/phase-2-adapters`.

- [ ] **Step 2: Confirm Phase 0 and Phase 1 tests pass before Phase 2 changes**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py -v
```

Expected: PASS before adapter changes.

## Task 1: Add Failing Adapter Tests

**Files:**
- Create: `tests/test_adapter_protocols.py`
- Create: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add protocol conformance tests**

Assert these concrete objects satisfy runtime protocols:
- `AgentTransportImpl(timeout=1, client=fake_client)` is an `AgentTransport`.
- `AgentCardResolverImpl(client=fake_client)` is an `AgentCardResolver`.
- `LLMGatewayImpl(model_registry=registry, providers=fake_providers)` is an `LLMProvider`.
- `ModelRegistryImpl()` is a `ModelRegistry`.

- [ ] **Step 2: Add export and package-list tests**

Assert:
- `from a2a_adapter import AgentTransportImpl, AgentCardResolverImpl` works.
- `from llm_gateway import LLMGatewayImpl, ModelRegistryImpl` works.
- `pyproject.toml` includes `a2a_adapter`, `llm_gateway`, and `llm_gateway.providers`.

- [ ] **Step 3: Add import-boundary AST tests**

Scan new adapter packages and assert:
- `a2a_adapter/**` imports only stdlib, `common.*`, `dal.*`, `a2a.*`, `httpx`, and `httpx_sse`.
- `llm_gateway/**` imports only stdlib, `common.*`, `dal.*`, `openai`, `google.genai`, `aioboto3`, and `botocore`.
- Neither `a2a_adapter/` nor `llm_gateway/` imports from `services`, `modules`, `database`, `infrastructure`, `models`, legacy `config/`, `main`, or `container`. SDK confinement for existing business modules is enforced in later migration phases, not Phase 2.

- [ ] **Step 4: Add translator unit tests**

Cover:
- `internal_message_to_a2a()` preserves `role`, `parts`, `metadata`, and `agent_id`.
- `a2a_task_to_result()` normalizes task ids, status, result payload, and error text.
- `a2a_event_to_stream_event()` normalizes streaming events and marks terminal events as `final=True`.
- `a2a_card_to_snapshot()` supports dicts and SDK-like objects with attributes.

- [ ] **Step 5: Add mocked adapter and gateway tests**

Cover:
- `AgentCardResolverImpl.resolve_card()` fetches `/.well-known/agent.json`, translates to `AgentCardSnapshot`, and caches per URL until TTL expires.
- `supports_push_notifications()` and `supports_streaming()` read normalized card capabilities.
- `AgentTransportImpl.send_message()` posts a translated message and returns `AgentTaskResult`.
- `AgentTransportImpl.send_message()` catches HTTP/SDK errors and returns `AgentTaskResult(error=...)`.
- `AgentTransportImpl.stream_message()` yields one `AgentStreamEvent` per SSE frame.
- `ModelRegistryImpl.get_model()`, `supports_capability()`, and `list_models()` behave deterministically.
- `LLMGatewayImpl.generate()` routes by registry provider.
- `LLMGatewayImpl.generate_structured()` routes by registry provider and parses JSON for fallback providers.
- `LLMGatewayImpl.embed()` and `embed_batch()` route to an embedding-capable provider.

- [ ] **Step 6: Verify tests fail for missing adapter packages**

```bash
uv run python -m pytest tests/test_adapter_protocols.py tests/test_adapter_unit.py -v
```

Expected: FAIL with import errors for `a2a_adapter` and `llm_gateway`.

## Task 2: Implement A2A Translators

**Files:**
- Create: `a2a_adapter/translators.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add `internal_message_to_a2a()`**

Implement a pure function:

```python
def internal_message_to_a2a(msg: InternalAgentMessage) -> dict[str, Any]:
    return {
        "role": msg.role,
        "parts": msg.parts,
        "metadata": {"agent_id": msg.agent_id, **msg.metadata},
    }
```

Keep the function SDK-type-free at the signature boundary. SDK request construction belongs in `transport.py`.

- [ ] **Step 2: Add task result normalization**

Implement `a2a_task_to_result(task_data: dict, agent_id: str) -> AgentTaskResult`.

Field normalization:
- `task_id`: first non-empty value from `id`, `taskId`, `task_id`, or `""`.
- `status`: string value from `status.state`, `status`, or `"unknown"`.
- `result`: include `artifacts`, `message`, and raw `task_data` keys that are present.
- `error`: string value from `error.message`, `error`, or status message when status is failed/canceled.

- [ ] **Step 3: Add stream event normalization**

Implement `a2a_event_to_stream_event(event_data: dict, agent_id: str) -> AgentStreamEvent`.

Rules:
- `task_id`: first non-empty value from `task_id`, `taskId`, `id`, nested `task.id`, or `""`.
- `event_type`: first non-empty value from `event_type`, `type`, `kind`, or `"message"`.
- `payload`: raw event dict plus normalized nested `status`, `artifact`, or `message` fields when present.
- `final`: true when event has `final=True` or terminal status in `{"completed", "failed", "canceled", "cancelled", "rejected"}`.

- [ ] **Step 4: Add agent card normalization**

Implement `a2a_card_to_snapshot(card: Any, agent_url: str) -> AgentCardSnapshot`.

Rules:
- Accept dicts and SDK objects.
- Read `agent_id` from `id`, `agent_id`, `name`, or `agent_url`.
- Read `name`, `description`, `url`, `input_modes`, and `output_modes` defensively.
- Normalize capabilities to string names, including `"streaming"` and `"push_notifications"` when SDK capability flags are present.

- [ ] **Step 5: Run translator tests**

```bash
uv run python -m pytest tests/test_adapter_unit.py -k translator -v
```

Expected: PASS for translator tests.

## Task 3: Implement A2A Card Resolver

**Files:**
- Create: `a2a_adapter/card_resolver.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add `AgentCardResolverImpl` constructor**

Constructor:

```python
def __init__(
    self,
    client: httpx.AsyncClient | None = None,
    cache_ttl: int = 300,
) -> None:
```

Track injected client vs owned client so tests can inject a fake client and production can close owned clients later if a close method is added.

- [ ] **Step 2: Implement `resolve_card()`**

Behavior:
- Build card URL as `{agent_url.rstrip("/")}/.well-known/agent.json`.
- `GET` with the configured async client.
- Parse JSON into `a2a.types.AgentCard` inside this file only.
- Translate with `a2a_card_to_snapshot()`.
- Cache successful snapshots in memory as `dict[str, tuple[float, AgentCardSnapshot]]`.
- Return cached value until TTL expires.
- Return `None` on HTTP, JSON, validation, or SDK parsing errors.

- [ ] **Step 3: Implement support checks**

`supports_push_notifications(agent_url)` and `supports_streaming(agent_url)` call `resolve_card()` and check normalized capability names.

Use these accepted capability spellings:
- Push: `"push_notifications"`, `"push-notifications"`, `"pushNotifications"`.
- Streaming: `"streaming"`, `"stream"`, `"message/stream"`.

- [ ] **Step 4: Run card resolver tests**

```bash
uv run python -m pytest tests/test_adapter_unit.py -k card_resolver -v
```

Expected: PASS for card resolver tests.

## Task 4: Implement A2A Transport

**Files:**
- Create: `a2a_adapter/transport.py`
- Create: `a2a_adapter/__init__.py`
- Test: `tests/test_adapter_protocols.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add `AgentTransportImpl` constructor**

Constructor:

```python
def __init__(
    self,
    timeout: int = 30,
    client: httpx.AsyncClient | None = None,
) -> None:
```

Use injected `httpx.AsyncClient` for tests. Production construction creates an async client with the configured timeout.

- [ ] **Step 2: Implement synchronous message send**

`send_message(agent_url, message, **kwargs)` must:
- Convert `InternalAgentMessage` with `internal_message_to_a2a()`.
- Construct `a2a.types.Message`, `MessageSendParams`, and `SendMessageRequest` inside `transport.py`.
- POST JSON to `agent_url.rstrip("/")`.
- Translate successful response payload to `AgentTaskResult` with `a2a_task_to_result()`.
- On `httpx`, SDK validation, or JSON errors, return `AgentTaskResult(task_id="", agent_id=message.agent_id, status="error", result={}, error=str(exc))`.

Keep request ids generated inside the adapter with `uuid.uuid4()`. Do not add run, room, task lifecycle, webhook, or continuation behavior.

- [ ] **Step 3: Implement streaming message send**

`stream_message(agent_url, message, **kwargs)` must:
- Convert the message the same way as `send_message()`.
- Construct `a2a.types.SendStreamingMessageRequest` inside `transport.py`.
- Use `httpx_sse.aconnect_sse()` with the async client.
- For each SSE frame, `json.loads(sse.data)` and yield `a2a_event_to_stream_event(event_data, message.agent_id)`.
- On streaming error before any event is yielded, yield one `AgentStreamEvent(task_id="", agent_id=message.agent_id, event_type="error", payload={"error": str(exc)}, final=True)`.

- [ ] **Step 4: Export A2A adapter classes**

`a2a_adapter/__init__.py` must have explicit `__all__`:

```python
__all__ = ["AgentTransportImpl", "AgentCardResolverImpl"]
```

- [ ] **Step 5: Run A2A adapter tests**

```bash
uv run python -m pytest tests/test_adapter_protocols.py tests/test_adapter_unit.py -k "a2a or transport or card_resolver or translator" -v
```

Expected: PASS for A2A adapter tests.

## Task 5: Implement Model Registry

**Files:**
- Create: `llm_gateway/model_registry.py`
- Test: `tests/test_adapter_protocols.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add static model configuration**

`ModelRegistryImpl` reads settings from `common.config.settings` only and registers:
- Logical name `lead_ai_model` -> `settings.lead_ai_model`, provider `openai`.
- Logical name `classifier_ai_model` -> `settings.classifier_ai_model`, provider `openai`.
- Logical name `embedding_model` -> `settings.embedding_model`, provider `openai`.
- Logical name `gemini_model_name` -> `settings.gemini_model_name`, provider `gemini`.
- Logical name `gemini_embedding_model_name` -> `settings.gemini_embedding_model_name`, provider `gemini`.
- Logical name `bedrock_supervisor_model` -> `settings.bedrock_supervisor_model`, provider `bedrock`.

Also index each model by concrete `model_id` when it differs from the logical name so gateway callers may pass either form.

- [ ] **Step 2: Assign model capabilities**

Use conservative static capabilities:
- OpenAI lead/classifier models: `["json_schema", "tool_use", "vision"]`.
- OpenAI embedding model: `["embedding"]`.
- Gemini text model: `["json_schema", "vision"]`.
- Gemini embedding model: `["embedding"]`.
- Bedrock supervisor model: `["tool_use"]`.

Do not use capabilities to block `generate_structured()` fallback. Use capabilities to route embeddings and to answer `supports_capability()`.

- [ ] **Step 3: Implement registry methods**

Methods:
- `get_model(logical_name: str) -> ModelInfo`: return registered `ModelInfo`; raise `KeyError` if missing.
- `supports_capability(model: str, capability: str) -> bool`: false if model is missing.
- `list_models(capability: str | None = None) -> list[ModelInfo]`: return unique models sorted by logical name; filter when capability is provided.

- [ ] **Step 4: Run model registry tests**

```bash
uv run python -m pytest tests/test_adapter_unit.py -k model_registry -v
```

Expected: PASS for model registry tests.

## Task 6: Implement LLM Providers

**Files:**
- Create: `llm_gateway/providers/__init__.py`
- Create: `llm_gateway/providers/openai_provider.py`
- Create: `llm_gateway/providers/gemini_provider.py`
- Create: `llm_gateway/providers/bedrock_provider.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add OpenAI provider**

`OpenAIProvider` constructor accepts optional `client: AsyncOpenAI`, `api_key: str | None`, and default model strings for tests.

Implement:
- `generate()`: call `client.chat.completions.create(model=model, messages=messages, **kwargs)` and return `LLMResponse`.
- `generate_structured()`: call chat completions with native `response_format={"type": "json_schema", "json_schema": {"name": "structured_response", "schema": schema, "strict": True}}`; parse returned content with `json.loads()` for `LLMStructuredResponse.data`.
- `embed()`: call `client.embeddings.create(model=model, input=text)` and return first embedding.
- `embed_batch()`: call `client.embeddings.create(model=model, input=texts)` and return embeddings in response order.

Normalize usage into `LLMUsage(prompt_tokens, completion_tokens, total_tokens)` when SDK usage fields exist. Store only JSON-serializable raw response data in `raw_response`.

- [ ] **Step 2: Add Gemini provider**

`GeminiProvider` constructor accepts optional injected client and API key.

Implement:
- `generate()`: call the async `google.genai` client with model, message text/content, and kwargs; return `LLMResponse`.
- `generate_structured()`: set `response_mime_type="application/json"` when supported, append a generic JSON-schema instruction to the first system message or prepend one if no system message exists, then parse content with `json.loads()`.
- `embed()` and `embed_batch()`: use the Gemini embedding model and return normalized vectors.

Keep prompt-template decisions out of the provider. The only text this provider may add is the generic structured-output instruction derived from the schema.

- [ ] **Step 3: Add Bedrock provider**

`BedrockProvider` constructor accepts optional `session: aioboto3.Session`, region, and default model.

Implement:
- `generate()`: use `bedrock-runtime.invoke_model` with Anthropic Claude-compatible body from the existing reference pattern and return `LLMResponse`.
- `generate_structured()`: call `generate()` with a generic schema instruction added to the system message, then `json.loads()` response content.
- `embed()` and `embed_batch()`: raise `NotImplementedError("BedrockProvider does not support embeddings")`.

Do not add Bedrock prompt templates or supervisor-specific behavior.

- [ ] **Step 4: Export providers**

`llm_gateway/providers/__init__.py` must have explicit `__all__`:

```python
__all__ = ["OpenAIProvider", "GeminiProvider", "BedrockProvider"]
```

- [ ] **Step 5: Run provider tests**

```bash
uv run python -m pytest tests/test_adapter_unit.py -k provider -v
```

Expected: PASS with mocked SDK clients; no real network calls.

## Task 7: Implement LLM Gateway and Optional Retry Helper

**Files:**
- Create: `llm_gateway/gateway.py`
- Create: `llm_gateway/retry.py` if shared retry code is needed.
- Create: `llm_gateway/__init__.py`
- Test: `tests/test_adapter_protocols.py`
- Test: `tests/test_adapter_unit.py`

- [ ] **Step 1: Add gateway constructor**

Constructor:

```python
def __init__(
    self,
    model_registry: ModelRegistryImpl | None = None,
    providers: dict[str, LLMProvider] | None = None,
) -> None:
```

Default providers:
- `"openai"` -> `OpenAIProvider()`
- `"gemini"` -> `GeminiProvider()`
- `"bedrock"` -> `BedrockProvider()`

- [ ] **Step 2: Add model resolution and provider routing**

Private helper behavior:
- `model=None` resolves to `settings.lead_ai_model` for `generate()` and `generate_structured()`.
- `model=None` resolves to `settings.embedding_model` for `embed()` and `embed_batch()`.
- Resolve model through `ModelRegistryImpl.get_model()`.
- Route to `providers[model_info.provider]`.
- Pass concrete `model_info.model_id` to provider methods.
- Raise `KeyError` for unknown models and `RuntimeError` for missing providers.

- [ ] **Step 3: Implement gateway methods**

Methods:
- `generate(messages, model=None, **kwargs)` routes to provider `generate()`.
- `generate_structured(messages, schema, model=None, **kwargs)` routes to provider `generate_structured()`.
- `embed(text, model=None)` routes only if resolved model supports `"embedding"`, otherwise raise `ValueError`.
- `embed_batch(texts, model=None)` mirrors `embed()` routing and preserves input order.

- [ ] **Step 4: Add optional retry helper only if needed**

If provider timeout/retry code becomes duplicated and exceeds 20 lines, add `llm_gateway/retry.py` with one small async helper. Keep retries generic and provider-agnostic. Do not add fallback model-selection strategy in Phase 2.

- [ ] **Step 5: Export gateway classes**

`llm_gateway/__init__.py` must have explicit `__all__`:

```python
__all__ = ["LLMGatewayImpl", "ModelRegistryImpl"]
```

- [ ] **Step 6: Run gateway tests**

```bash
uv run python -m pytest tests/test_adapter_protocols.py tests/test_adapter_unit.py -k "gateway or model_registry or provider" -v
```

Expected: PASS for LLM gateway tests.

## Task 8: Add Packaging Metadata

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_adapter_protocols.py`

- [ ] **Step 1: Add packages to setuptools list**

Add:
- `a2a_adapter`
- `llm_gateway`
- `llm_gateway.providers`

- [ ] **Step 2: Run package-list test**

```bash
uv run python -m pytest tests/test_adapter_protocols.py -k package -v
```

Expected: PASS.

## Task 9: Verification

**Files:**
- Test: `tests/test_adapter_protocols.py`
- Test: `tests/test_adapter_unit.py`
- Test: `tests/test_common_foundation.py`
- Test: `tests/test_dal_protocols.py`
- Test: `tests/test_dal_unit.py`

- [ ] **Step 1: Run adapter tests**

```bash
uv run python -m pytest tests/test_adapter_protocols.py tests/test_adapter_unit.py -v
```

Expected: PASS.

- [ ] **Step 2: Run Phase 0 and Phase 1 regressions**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py -v
```

Expected: PASS.

- [ ] **Step 3: Run compile checks**

```bash
uv run python -m compileall a2a_adapter llm_gateway
```

Expected: no syntax or import failures.

- [ ] **Step 4: Run import-boundary tests explicitly**

```bash
uv run python -m pytest tests/test_adapter_protocols.py -k import_boundary -v
```

Expected: PASS; SDK and internal dependency boundaries are enforced.

- [ ] **Step 5: Check scoped diff**

```bash
git status --short
git diff --stat
```

Expected changes are limited to `a2a_adapter/**`, `llm_gateway/**`, adapter tests, `pyproject.toml`, and this plan file.

## Task 10: Commit

**Files:**
- All files changed by this plan.

- [ ] **Step 1: Stage scoped files**

```bash
git add a2a_adapter llm_gateway tests/test_adapter_protocols.py tests/test_adapter_unit.py pyproject.toml docs/superpowers/plans/2026-05-09-phase-2-adapter-layer.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: add adapter layer implementations"
```

Expected: one commit on `refactor/phase-2-adapters`.

## Guardrails

- Adapter layer imports only `common.*`, `dal.*`, and its own third-party SDKs.
- A2A adapter may import only `a2a`, `httpx`, and `httpx_sse` as third-party SDK/client dependencies.
- LLM gateway may import only `openai`, `google.genai`, `aioboto3`, and `botocore` as provider SDK dependencies.
- Do not import from `services/`, `modules/`, `database/`, `infrastructure/`, `models/`, legacy `config/`, `main.py`, or `container.py`.
- `a2a-sdk` types must not appear in public signatures or outside `a2a_adapter/`.
- LLM provider SDK types must not appear in public signatures or outside `llm_gateway/`.
- Implement `AgentTransport`, `AgentCardResolver`, `LLMProvider`, and `ModelRegistry` exactly as defined in `common/protocols/`.
- Translate only between Common DTOs and SDK payloads. Do not add business-specific room, run, agent registration, task lifecycle, or orchestration logic.
- The LLM gateway wraps calling conventions, provider routing, and generic structured-output handling only. Supervisor, debate, classifier, and memory prompts stay in business modules.
- Use `common.config.settings` for settings access. Do not call `os.getenv()` in adapter code.
- Unit tests must use mocked HTTP/SSE/provider clients and must not make real network calls.
