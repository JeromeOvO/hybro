# LLM Gateway Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all LLM SDK clients, env lookup, model routing, retry, and streaming behind `llm_gateway/`, while preserving existing `app_shell` public methods long enough to migrate callers safely.

**Architecture:** `llm_gateway` becomes the only LLM module that imports provider SDKs and reads LLM settings. Existing prompt-heavy services in `app_shell` first become gateway-backed compatibility adapters. Prompt workflows then move into focused `llm_gateway/services/` classes that accept primitives or DTOs from `common/dto/llm_workflows.py`, never domain models from `models/` or `app_shell/`. `main.py` wires one `LLMGatewayImpl` instance into consumers instead of allowing each legacy service to create provider clients.

**Tech Stack:** Python 3.11+, FastAPI dependency wiring, async provider protocols, Pydantic DTOs in `common/dto/llm.py`, `pytest`/`pytest-asyncio`, Ruff, OpenAI SDK, Google GenAI SDK, Bedrock via `aioboto3`.

---

## Current State

- `llm_gateway/` already exposes `LLMGatewayImpl`, `ModelRegistryImpl`, and provider adapters for OpenAI, Gemini, and Bedrock.
- `main.py` already creates `model_registry = ModelRegistryImpl()` and `llm_provider = LLMGatewayImpl(model_registry=model_registry)`, then uses it for some newer module dependencies.
- Legacy primary LLM paths still live in `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py`.
- `app_shell/openai_service.py` directly imports `AsyncOpenAI`, calls `os.getenv`, creates its own client, owns embedding calls, supervisor JSON/text/stream calls, query expansion, agent selection, summarization, memory prompt generation, and user-message parsing.
- `app_shell/gemini_service.py` directly imports `google.genai`, reads env, creates its own client, and owns Gemini-specific prompt calls.
- `app_shell/bedrock_service.py` directly imports `aioboto3`/`botocore` and owns supervisor JSON/text/stream calls.
- Existing tests cover the LLM gateway protocol shape and import boundary for `llm_gateway`, but they do not yet prevent new direct SDK/env use from leaking into `app_shell`.

## Target Boundaries

- Only `llm_gateway/providers/` may import LLM provider SDKs (`openai`, `google.genai`, Bedrock runtime via `aioboto3`/`botocore`). Other `llm_gateway` modules depend on protocols, DTOs, and provider-agnostic gateway APIs.
- `llm_gateway/` must not import `models`, `app_shell`, `execution`, `room`, `agent`, or other domain packages. Workflow services receive only primitives, `common/dto/llm.py` DTOs, or `common/dto/llm_workflows.py` DTOs.
- `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py` must not import LLM provider SDKs, `dotenv`, or call `os.getenv` for LLM model/API-key configuration.
- Non-LLM infrastructure modules such as `app_shell/s3_service.py` may continue to import AWS SDKs for storage. Boundary tests must distinguish Bedrock LLM provider access from S3/storage access.
- `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py` keep their public method names during the first migration stage, but delegate provider interaction to typed gateway services.
- Logical model selection uses `ModelRegistryImpl` entries such as `lead_ai_model`, `supervisor_model`, `classifier_ai_model`, `embedding_model`, `context_memory_legacy_json_model`, `gemini_model_name`, `gemini_embedding_model_name`, and `bedrock_supervisor_model`.
- Streaming supervisor calls use a gateway streaming API so callers do not know which provider produced chunks.
- Retry and timeout behavior is centralized in `LLMGatewayImpl`; provider adapters remain thin SDK translators.

## File Map

### New Files

- `llm_gateway/config.py`  
  Defines `LLMGatewayConfig` with retry count, retry backoff, request timeout, stream timeout, default logical model ids, and a `from_settings(settings)` factory that derives supervisor defaults from typed settings.

- `llm_gateway/errors.py`  
  Defines gateway-owned exceptions such as `LLMStreamingUnsupportedError`, `LLMModelRoutingError`, and `LLMServiceNotBoundError` for typed runtime failures.

- `llm_gateway/structured_generation.py`  
  Defines provider-neutral structured-generation options and request translation helpers for schema-backed structured output and schema-less JSON-object mode.

- `common/dto/llm_workflows.py`  
  Defines domain-neutral DTOs for prompt workflows, including agent routing candidates, explicit mentions, room-message summaries, memory generation inputs, and parsed user-message requests.

- `llm_gateway/services/__init__.py`  
  Exports focused gateway-backed services used by `main.py` and compatibility adapters.

- `llm_gateway/services/supervisor.py`  
  Provides `SupervisorLLMService` with `call_json(system_prompt, user_prompt, *, model=None, timeout_seconds=None)`, `call_text(system_prompt, user_prompt, *, model=None, timeout_seconds=None)`, and `call_text_stream(system_prompt, user_prompt, *, model=None, timeout_seconds=None)`. It uses the injected default logical model id and lets `LLMGatewayImpl`/`ModelRegistryImpl` choose the provider.

- `llm_gateway/services/embedding.py`  
  Provides `EmbeddingLLMService.get_embedding(text, target_dim=None, *, model="embedding_model")` for compatibility adapters and consumer surfaces that expose embedding helpers.

- `llm_gateway/services/discovery.py`  
  Provides `DiscoveryLLMService.expand_query_for_discovery`.

- `llm_gateway/services/debate.py`  
  Provides `DebateLLMService.short_debate_with_openai` and `DebateLLMService.long_debate_with_openai` using gateway generation while preserving existing prompt behavior.

- `llm_gateway/services/agent_selection.py`  
  Provides `AgentSelectionLLMService.select_best_agent_for_task` using DTO candidates instead of `models.agent.Agent`.

- `llm_gateway/services/summary.py`  
  Provides summarization workflows currently hosted in `OpenAIService`, using DTO summaries of room messages instead of `models.room.RoomAgentMessage`.

- `llm_gateway/services/room_memory.py`  
  Provides chat-context and room-memory generation currently hosted in `OpenAIService`, using DTO memory inputs instead of `models.context_memory.*`.

- `llm_gateway/services/message_parser.py`  
  Provides user-message parsing currently hosted in `OpenAIService`.

- `tests/test_llm_gateway_runtime.py`  
  Covers retry, provider selection, model registry resolution, and streaming behavior with fake providers.

- `tests/test_llm_gateway_provider_streaming.py`  
  Covers OpenAI, Bedrock, and Gemini provider streaming adapters with fake SDK clients and no network calls.

- `tests/test_llm_gateway_service_adapters.py`  
  Covers gateway-backed service behavior for supervisor, discovery expansion, agent selection, parsing, summarization, and memory prompts.

- `tests/test_service_gemini.py`  
  Covers retained `GeminiService` compatibility methods and return shapes with fake gateway services.

- `tests/test_llm_app_shell_boundaries.py`  
  Enforces that `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py` do not import provider SDKs or read LLM env vars directly.

### Modified Files

- `common/protocols/llm_protocols.py`  
  Add a streaming protocol and focused service protocols used by `app_shell`, `execution`, and `platform_module`.

- `common/protocols/__init__.py`  
  Export the new cross-module LLM protocols from `common.protocols` for consumers that import protocols from the package root.

- `common/dto/__init__.py`  
  Export workflow DTOs that are intended for cross-module LLM service inputs.

- `pyproject.toml`  
  Add `llm_gateway.services` to the explicit setuptools package list.

- `common/config/settings.py`  
  Add `supervisor_model` so the current `SUPERVISOR_MODEL` override becomes typed config instead of `os.getenv` usage in `OpenAIService`. Preserve Gemini API-key compatibility by making `GOOGLE_API_KEY` canonical and `GEMINI_API_KEY` a fallback alias when the canonical value is empty.

- `.env.example`  
  Document `SUPERVISOR_MODEL` as an optional OpenAI supervisor override. Document `GOOGLE_API_KEY` as canonical for Gemini and `GEMINI_API_KEY` as a backward-compatible fallback alias.

- `llm_gateway/gateway.py`  
  Add gateway config, retry wrapper, timeout handling, schema-less JSON-object mode for `generate_structured`, and `generate_stream`.

- `llm_gateway/model_registry.py`  
  Register logical model `supervisor_model` from `settings.supervisor_model` and preserve existing `bedrock_supervisor_model` for Bedrock routing.

- `llm_gateway/providers/openai_provider.py`  
  Add `generate_stream`; ensure all chat/embedding calls receive concrete model ids from `LLMGatewayImpl` and do not read default model settings.

- `llm_gateway/providers/gemini_provider.py`  
  Add gateway-compatible streaming behavior by defaulting to a single text chunk from `generate`. Enable native Gemini chunk streaming only if the installed SDK call is covered by fake-client tests. Ensure concrete model ids come from `LLMGatewayImpl`, not provider-level model settings.

- `llm_gateway/providers/bedrock_provider.py`  
  Add `generate_stream` backed by Bedrock response streaming. Keep credentials and region setup here, but require concrete model ids from `LLMGatewayImpl`.

- `llm_gateway/__init__.py`  
  Keep root exports limited to `LLMGatewayImpl` and `ModelRegistryImpl`; import services and errors from their submodules.

- `app_shell/openai_service.py`  
  Convert into a compatibility adapter that delegates provider calls to `llm_gateway` services. Remove direct SDK imports, `dotenv`, and LLM env lookups.

- `app_shell/gemini_service.py`  
  Convert into a side-effect-free compatibility adapter over `llm_gateway` Gemini-capable services. Keep the singleton during this migration stage.

- `app_shell/bedrock_service.py`  
  Convert into a compatibility adapter over provider-neutral supervisor helpers. Default and registered-model paths delegate to `SupervisorLLMService`; unregistered concrete Bedrock override paths use internal Bedrock-hinted gateway helpers so the gateway does not guess. Remove direct `aioboto3`/`botocore` imports from this module.

- `execution/orchestration/room_supervisor_service.py`  
  Inject `SupervisorLLMService` or its protocol instead of choosing between `OpenAIService` and `BedrockService` directly.

- `main.py`  
  Wire `LLMGatewayImpl` and focused gateway services once, then pass them into legacy adapters and module dependency binders.

- `container.py`  
  Replace public `LLMProvider` annotations with `LLMGateway` or narrower gateway-facing protocols.

- `agent/facade.py`  
  Replace `LLMProvider` dependency typing with `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway` for vector-indexing embedding use.

- `context_memory/search.py`  
  Replace `LLMProvider` dependency typing with `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway` for vector search embedding use.

- `context_memory/projection.py`  
  Replace `LLMProvider` dependency typing with `LLMStructuredGateway` or the narrow gateway protocol needed by projection calls.

- `context_memory/summary.py`  
  Replace `LLMProvider` dependency typing with `LLMStructuredGateway`.

- `context_memory/facade.py`  
  Replace stored `LLMProvider` dependency typing with gateway-facing protocols that satisfy delegated search, projection, and summary paths.

- `api_gateway/viewsets/agent.py`  
  Replace `app_shell.bound.EmbeddingProvider` with `EmbeddingServiceProtocol`.

- `app_shell/bound.py`  
  Remove or deprecate the local `EmbeddingProvider` protocol after API gateway viewsets use the common protocol.

- `tests/test_service_openai.py`  
  Replace direct fake `client` assertions with fake gateway/service assertions while preserving behavior expectations.

- `tests/test_supervisor_bedrock_routing.py`  
  Assert supervisor routing chooses the configured logical model/provider through `SupervisorLLMService`.

- `tests/test_service_bedrock.py`  
  Move direct Bedrock SDK expectations into provider-level tests and keep only thin compatibility-adapter assertions for `BedrockService`.

- `tests/test_platform_module_protocols.py`  
  Replace the current source assertion that expects `discovery_query_expander=openai_service` with one that expects a gateway-backed discovery service.

- `tests/test_common_foundation.py` and `tests/test_phase0_common.py`  
  Update protocol export and runtime protocol checks for `LLMProviderAdapter`, `LLMGateway`, and narrow LLM gateway protocols.

- `tests/test_api_thin_adapters.py` and `tests/fixtures/phase9_api_routes.json`  
  Update API route inventory expectations after replacing `app_shell.bound.EmbeddingProvider` with the common embedding protocol.

- `tests/test_adapter_protocols.py`  
  Extend `test_adapter_subpackages_are_packaged` so the explicit package list includes `llm_gateway.services`.

- `System-Architecture.md`  
  Update the system dependency diagram and LLM ownership notes after implementation.

- `docs/MODULAR_DECOUPLING_DESIGN.md`  
  Change LLM Gateway from target-only language to implemented migration status, including remaining compatibility adapters if any remain.

## Implementation Steps

### 1. Add Failing Boundary and Runtime Tests

- [ ] Add `tests/test_llm_app_shell_boundaries.py`.
- [ ] Parse `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py` with `ast.parse` and assert that imports do not include:
  - `openai`
  - `google.genai`
  - `from google import genai`
  - `from google.genai import ...`
  - `aioboto3`
  - `botocore`
  - `dotenv`
- [ ] Implement the import assertion against AST aliases, not substring matching, so both `import google.genai` and `from google import genai` are rejected for LLM compatibility services.
- [ ] In the same test file, assert that these three files do not call `os.getenv` for LLM config.
- [ ] Do not scan all of `app_shell/` for `aioboto3`/`botocore`; `app_shell/s3_service.py` owns non-LLM storage access and is outside this LLM migration.
- [ ] Add or extend an allowlist-based import-boundary test for `llm_gateway/services/`: allowed roots are standard-library modules, `common`, and `llm_gateway`; every other production package root is rejected.
- [ ] In the same services boundary test, explicitly reject imports from `llm_gateway.providers` and constructor annotations using `OpenAIProvider`, `GeminiProvider`, `BedrockProvider`, or raw provider maps. Focused services must be typed to gateway-facing protocols.
- [ ] Add a protocol/source boundary test that rejects `provider_hint` parameters on public gateway protocols, focused-service protocols, and `SupervisorLLMService` public methods. Allow `provider_hint` only in `llm_gateway/gateway.py` internal helper methods, compatibility adapters, and tests that verify legacy concrete-model overrides.
- [ ] Add an AST/source boundary test that rejects direct LLM settings-field access outside approved owners:
  - model/routing fields are allowed only in `common/config/`, `llm_gateway/config.py`, and `llm_gateway/model_registry.py`;
  - LLM provider credential/region fields are allowed in `common/config/` and `llm_gateway/providers/`;
  - shared AWS storage credentials are allowed in approved storage adapters such as `app_shell/s3_service.py` and `dal/s3/client.py`;
  - no `app_shell`, `execution`, `api`, `agent`, `room`, `context_memory`, or service module may read LLM settings fields directly.
- [ ] Cover at least these model/routing settings fields:
  - `lead_ai_model`
  - `classifier_ai_model`
  - `embedding_model`
  - `gemini_model_name`
  - `gemini_embedding_model_name`
  - `supervisor_model`
  - `use_bedrock_supervisor`
  - `bedrock_supervisor_model`
- [ ] Cover at least these LLM provider credential/region settings fields:
  - `openai_api_key`
  - `google_api_key`
  - `bedrock_region`
- [ ] Do not treat shared AWS credential fields `aws_access_key_id` and `aws_secret_access_key` as LLM-only settings. Boundary tests may allow them in storage adapters while still forbidding Bedrock model/region settings outside LLM owners.
- [ ] Add a separate workflow-config boundary test for `debate_rounds`: `settings.debate_rounds` may be read only in `common/config/` and composition-root wiring (`main.py`/`container.py`). `app_shell/openai_service.py`, `execution/orchestration/supervisor_executor.py`, and other feature services must receive debate rounds as a primitive constructor/bind argument.
- [ ] Add `tests/test_llm_gateway_runtime.py` with fake providers:
  - `FakeProvider.generate` records messages, model, and kwargs.
  - `FlakyProvider.generate` raises once then succeeds.
  - `StreamingProvider.generate_stream` yields `["a", "b", "c"]`.
- [ ] Cover these gateway behaviors:
  - logical model id resolves to the expected provider and concrete model.
  - explicit model override still routes through registry metadata when the model is registered.
  - unregistered concrete model override works only when a compatibility adapter supplies an explicit provider hint.
  - unregistered concrete model override without a provider hint raises a typed routing error.
  - transient provider failure is retried exactly once when config says `max_attempts=2`.
  - non-streaming providers fail with a typed gateway error when `generate_stream` is requested.
  - streaming providers yield chunks in order.
  - a streaming provider that fails before yielding the first chunk is retried and then succeeds.
  - a streaming provider that fails after yielding the first chunk is not retried, because retry would duplicate already-sent output.
  - non-streaming operations are wrapped in `asyncio.timeout` using the selected workflow timeout.
  - streaming operations are timed across async-generator iteration, not only stream creation.
- [ ] Run:
  ```bash
  uv run pytest tests/test_llm_gateway_runtime.py tests/test_llm_app_shell_boundaries.py
  ```
- [ ] Expected result for this test-writing step: tests are committed in failing form first, then the later implementation steps in this plan must make them pass before the final verification gate.

### 2. Introduce Gateway Config and Streaming Protocols

- [ ] Add `LLMGatewayConfig` in `llm_gateway/config.py`:
  ```python
  from dataclasses import dataclass

  @dataclass(frozen=True)
  class LLMGatewayConfig:
      max_attempts: int = 2
      retry_backoff_seconds: float = 0.2
      request_timeout_seconds: float = 60.0
      stream_timeout_seconds: float = 120.0
      supervisor_json_timeout_seconds: float = 30.0
      supervisor_text_timeout_seconds: float = 90.0
      supervisor_stream_timeout_seconds: float = 90.0
      bedrock_request_timeout_seconds: float = 45.0
      default_generation_model: str = "lead_ai_model"
      default_embedding_model: str = "embedding_model"
      default_supervisor_model: str = "supervisor_model"

      @classmethod
      def from_settings(cls, settings_obj: Any) -> "LLMGatewayConfig":
          return cls(default_supervisor_model="supervisor_model")
  ```
- [ ] Update `LLMGatewayImpl.generate` and `generate_structured` to use `self.config.default_generation_model` when `model` is not provided.
- [ ] Update `LLMGatewayImpl.embed` and `embed_batch` to use `self.config.default_embedding_model` when `model` is not provided.
- [ ] Remove direct `settings.lead_ai_model`, `settings.embedding_model`, or other LLM settings reads from `llm_gateway/gateway.py`.
- [ ] Update provider constructors so providers may read credentials and region only; remove reads of `settings.lead_ai_model`, `settings.embedding_model`, `settings.gemini_model_name`, `settings.gemini_embedding_model_name`, and `settings.bedrock_supervisor_model` from provider default model fields.
- [ ] Add `LLMStreamingProvider` to `common/protocols/llm_protocols.py`:
  ```python
  from collections.abc import AsyncIterator

  class LLMStreamingProvider(Protocol):
      def generate_stream(
          self,
          messages: list[dict[str, Any]],
          model: str,
          **kwargs: Any,
      ) -> AsyncIterator[str]:
          ...
  ```
- [ ] Keep optional model ids only on gateway/service-facing APIs. Provider-facing protocols and provider adapter methods receive concrete `model: str`.
- [ ] Split protocol names so raw provider adapters and gateway consumers cannot be confused:
  - `LLMProviderAdapter` for `OpenAIProvider`, `GeminiProvider`, and `BedrockProvider`, with concrete `model: str` on generation/embedding methods;
  - `LLMGateway` and the narrow gateway-facing protocols for consumer modules and focused services, with optional logical model ids.
- [ ] Update `LLMGatewayImpl.__init__` type hints to `providers: dict[str, LLMProviderAdapter] | None`.
- [ ] Update `tests/test_adapter_protocols.py::test_llm_gateway_provider_mapping_is_typed_to_provider_protocol` to assert the provider map uses `LLMProviderAdapter`, and add a separate assertion that `LLMGatewayImpl` satisfies the public `LLMGateway` protocol.
- [ ] Add a combined `LLMGateway` protocol in `common/protocols/llm_protocols.py` that includes `generate`, `generate_structured`, `embed`, `embed_batch`, and `generate_stream`.
- [ ] Change structured-generation protocols consistently:
  - gateway-facing `generate_structured` accepts `schema: dict[str, Any] | None = None`, optional logical `model`, `json_mode: bool = False`, and `timeout_seconds: float | None = None`;
  - provider-facing `LLMProviderAdapter.generate_structured` accepts concrete `model: str`, `schema: dict[str, Any] | None = None`, and `json_mode: bool = False`;
  - `json_mode=True` with `schema=None` means "return one JSON object" and is the contract used by supervisor JSON compatibility calls;
  - `schema is None and json_mode is False` is invalid and raises `LLMModelRoutingError` or a dedicated structured-generation validation error before calling a provider.
- [ ] Add gateway-facing narrow protocols that represent gateway behavior rather than raw provider adapters:
  - `LLMTextGateway` for `generate`;
  - `LLMStructuredGateway` for `generate_structured`;
  - `LLMEmbeddingGateway` for `embed` and `embed_batch`;
  - `LLMStreamGateway` for `generate_stream`.
- [ ] Add `EmbeddingServiceProtocol` with `get_embedding(text: str, target_dim: int | None = None) -> list[float] | None` for compatibility/API surfaces that currently expose `get_embedding`.
- [ ] Add `RequiredEmbeddingServiceProtocol` with `get_embedding(text: str, target_dim: int | None = None) -> list[float]` for vector-indexing/search paths that cannot safely accept `None`.
- [ ] Add an internal `ModelSelectableEmbeddingServiceProtocol` with `get_embedding(text: str, target_dim: int | None = None, *, model: str = "embedding_model") -> list[float] | None` for `OpenAIService`/`GeminiService` compatibility adapters that must choose OpenAI versus Gemini embedding logical models. Public consumers continue to use `EmbeddingServiceProtocol` without `model=`.
- [ ] Document that focused services must not receive `OpenAIProvider`, `GeminiProvider`, `BedrockProvider`, or the raw provider mapping directly. They receive `LLMGatewayImpl` through one of the gateway-facing protocols so registry resolution, retry, timeout, and routing are always used.
- [ ] Add focused service protocols in `common/protocols/llm_protocols.py` for embedding, supervisor calls, discovery expansion, debate calls, agent selection, summarization, room memory, Gemini text generation, and message parsing. Match existing public method names and return types so consumers can migrate without changing behavior.
- [ ] Add `common/dto/llm_workflows.py` before moving prompt workflows. Include DTOs for:
  - agent selection candidates with id, name, description, capabilities, skills, and availability fields;
  - explicit mention metadata with agent id, agent name, and mention text;
  - room message summary inputs with role, text, agent id, and timestamp fields;
  - memory generation inputs with room id, user id, message snippets, and existing-memory snippets;
  - user-message parsing inputs with raw message text, selected agent map, debate mode flag, auto-assign flag, available agent candidates, conversation context, explicit mention DTOs, and debate rounds as a primitive integer.
- [ ] Add tests for `UserMessageParsingInput`-backed parsing that preserve debate mode, curated selected-agent mode, auto-assign mode, conversation context inclusion, explicit mention handling, and configured debate rounds.
- [ ] Update `common/dto/__init__.py` only with DTO exports that are used across modules.
- [ ] Export only protocol names that are intended for cross-module injection.
- [ ] Update `common/protocols/__init__.py` to re-export those cross-module LLM protocols from `common.protocols`.
- [ ] Update protocol export/design tests in `tests/test_common_foundation.py` and `tests/test_phase0_common.py` so every new protocol in `common.protocols.__all__` is runtime-checkable and has the expected method set.
- [ ] Run:
  ```bash
  uv run pytest tests/test_adapter_protocols.py tests/test_common_foundation.py tests/test_phase0_common.py tests/test_llm_gateway_runtime.py
  ```
- [ ] Expected result: protocol import tests pass; runtime tests that depend on gateway behavior are implemented in this step and made green in Step 3.

### 2b. Preserve Supervisor Model Configuration

- [ ] Add `supervisor_model: str | None = None` to `common/config/settings.py` with env binding for `SUPERVISOR_MODEL`.
- [ ] Add a `supervisor_model` logical model entry to `ModelRegistryImpl`:
  - when `settings.use_bedrock_supervisor` is false, route to OpenAI and use `settings.supervisor_model or settings.lead_ai_model`;
  - when `settings.use_bedrock_supervisor` is true, route to Bedrock and use `settings.bedrock_supervisor_model`;
  - keep the public logical id as `supervisor_model` in both cases so service code does not branch on providers.
- [ ] Update `.env.example` with `SUPERVISOR_MODEL=` and describe it as an optional OpenAI supervisor override. Do not use it for Bedrock routing; Bedrock routing remains controlled by typed settings and model registry defaults.
- [ ] Update `tests/test_adapter_unit.py` or add a focused registry test so `supervisor_model` is registered and `SUPERVISOR_MODEL` can override the OpenAI supervisor model without `OpenAIService` calling `os.getenv`.
- [ ] Preserve the `context_memory_legacy_json_model` registry entry and ensure focused tests cover it. `context_memory` passes this logical id to the gateway and must not own concrete provider model ids.
- [ ] Run:
  ```bash
  uv run pytest tests/test_adapter_unit.py tests/test_adapter_protocols.py
  ```
- [ ] Expected result: settings and registry tests pass, and supervisor model override behavior is covered outside `app_shell`.

### 2c. Preserve Gemini API-Key Configuration

- [ ] Keep `settings.google_api_key` as the canonical Gemini credential field and allow `GEMINI_API_KEY` as a fallback alias only when `GOOGLE_API_KEY` is empty.
- [ ] Add a settings test that covers `GOOGLE_API_KEY` taking precedence over `GEMINI_API_KEY`, fallback to `GEMINI_API_KEY` when canonical config is empty, and the empty/no-key case.
- [ ] Update `llm_gateway/providers/gemini_provider.py` tests so provider construction uses `settings.google_api_key`, including the fallback alias path, never `os.getenv` in `app_shell/gemini_service.py`.
- [ ] Run:
  ```bash
  uv run pytest tests/test_adapter_unit.py
  ```
- [ ] Expected result: Gemini API-key compatibility is typed and covered in settings/provider tests.

### 2d. Package New Gateway Subpackages

- [ ] Add `llm_gateway.services` to `[tool.setuptools].packages` in `pyproject.toml`.
- [ ] Extend `tests/test_adapter_protocols.py::test_adapter_subpackages_are_packaged` to assert that `llm_gateway.services` is included.
- [ ] Update `tests/test_adapter_protocols.py::test_llm_gateway_import_boundary` to split `llm_gateway/providers/` from the rest of `llm_gateway`:
  - provider modules may import provider SDK roots;
  - `llm_gateway/gateway.py`, `llm_gateway/config.py`, `llm_gateway/model_registry.py`, and `llm_gateway/services/` must not import provider SDK roots;
  - `llm_gateway/services/` uses an allowlist of standard-library roots, `common`, and `llm_gateway`;
  - all `llm_gateway` modules remain forbidden from importing domain packages such as `models`, `app_shell`, `execution`, `room`, `agent`, `platform_module`, `api`, and `api_gateway`.
- [ ] Run:
  ```bash
  uv run pytest tests/test_adapter_protocols.py
  ```
- [ ] Expected result: adapter protocol and packaging tests pass.

### 3. Implement Retry, Timeout, and Streaming in `LLMGatewayImpl`

- [ ] Modify `LLMGatewayImpl.__init__` to accept `config: LLMGatewayConfig | None = None`.
- [ ] Store `self.config = config or LLMGatewayConfig()`.
- [ ] Add `LLMStreamingUnsupportedError` in `llm_gateway/errors.py`.
- [ ] Add a typed routing error such as `LLMModelRoutingError` for unregistered concrete model overrides without provider context.
- [ ] Add `LLMServiceNotBoundError` in `llm_gateway/errors.py` for side-effect-free legacy compatibility singletons called before dependency binding.
- [ ] Import `LLMStreamingUnsupportedError` from `llm_gateway.errors` in runtime tests. Keep `llm_gateway.__all__` unchanged.
- [ ] Add `llm_gateway/structured_generation.py` helpers that translate provider-neutral structured-generation options into provider request metadata:
  - schema-backed structured output when `schema` is provided;
  - schema-less JSON-object mode when `json_mode=True`;
  - Bedrock JSON-only request instruction for JSON-object mode;
  - no business prompt construction and no domain imports.
- [ ] Add a private `_with_retry` helper that:
  - attempts a provider coroutine up to `max_attempts`;
  - sleeps `retry_backoff_seconds * attempt_number` between attempts;
  - does not retry validation errors from model registry resolution;
  - re-raises the final provider exception with the original traceback.
- [ ] Wrap `generate`, `generate_structured`, `embed`, and `embed_batch` through `_with_retry`.
- [ ] Update `generate_structured` to enforce the provider-neutral structured contract before routing:
  - `schema` provided: pass schema metadata through to the provider adapter;
  - `schema=None` and `json_mode=True`: request schema-less JSON-object mode;
  - `schema=None` and `json_mode=False`: fail before provider dispatch.
- [ ] Add tests proving `generate_structured(..., schema=None, json_mode=True)` routes through registry/retry/timeout and returns `LLMStructuredResponse.data` from a fake provider.
- [ ] Add `generate_stream` that:
  - resolves the provider through `_resolve_provider`;
  - checks whether the provider has `generate_stream`;
  - raises `LLMStreamingUnsupportedError` when the resolved provider does not expose streaming;
  - wraps provider async-generator iteration, not just provider lookup;
  - yields text chunks without buffering the full response;
  - retries failures that happen before the first chunk is yielded;
  - does not retry failures that happen after a chunk is yielded because retry would duplicate already-sent output.
- [ ] Add timeout handling using `asyncio.timeout` around each provider operation.
- [ ] Preserve current workflow timeout behavior:
  - OpenAI supervisor JSON: 30 seconds;
  - OpenAI supervisor text: 90 seconds;
  - OpenAI supervisor stream: 90 seconds;
  - Bedrock supervisor calls: `llm_gateway_config.bedrock_request_timeout_seconds`, defaulting to 45 seconds.
- [ ] Add provider-hint support for compatibility adapters:
  - public gateway and focused-service protocols do not expose `provider_hint`;
  - compatibility adapters use explicit internal gateway methods such as `_generate_with_provider_hint`, `_generate_structured_with_provider_hint`, and `_generate_stream_with_provider_hint`;
  - these internal methods accept `provider_hint: Literal["openai", "gemini", "bedrock"]`;
  - gateway/service-facing methods accept `timeout_seconds: float | None` that is consumed by gateway timeout handling and never forwarded to provider SDK kwargs;
  - if `model` is registered, registry metadata wins and `provider_hint` must match when provided;
  - if `model` is unregistered and `provider_hint` is provided, gateway treats `model` as a concrete provider model id and routes to the hinted provider;
  - if `model` is unregistered and no provider hint is provided, gateway raises `LLMModelRoutingError`.
- [ ] Run:
  ```bash
  uv run pytest tests/test_llm_gateway_runtime.py tests/test_adapter_protocols.py
  ```
- [ ] Expected result: gateway runtime tests and adapter protocol tests pass.

### 4. Add Provider Streaming Implementations

- [ ] Add `generate_stream` to `OpenAIProvider` using `AsyncOpenAI.chat.completions.create(..., stream=True)`.
- [ ] Yield only non-empty text deltas.
- [ ] Add `generate_stream` to `BedrockProvider` using the existing Bedrock streaming response path from `app_shell/bedrock_service.py`.
- [ ] Add `generate_stream` to `GeminiProvider` with a deterministic fallback that calls `generate` once and yields the full text as one chunk. Enable native Google GenAI chunk streaming only in the same step as fake-client tests for the exact installed SDK call shape.
- [ ] Keep provider adapters free of business prompt construction.
- [ ] Add provider-level tests with fake SDK clients:
  - OpenAI stream extracts text deltas.
  - Bedrock stream extracts text chunks from Bedrock events.
  - Gemini fallback yields a single chunk when native streaming is unavailable.
  - OpenAI schema-less `json_mode=True` maps to JSON-object enforcement without requiring a schema.
  - Bedrock schema-less `json_mode=True` receives the structured-generation translator's JSON-only request instruction and does not get that instruction from `BedrockService` or `SupervisorLLMService`.
  - provider adapters use the concrete `model` argument supplied by the gateway and do not fall back to LLM model settings.
- [ ] Run:
  ```bash
  uv run pytest tests/test_llm_gateway_provider_streaming.py
  ```
- [ ] Expected result: provider streaming tests pass without real network calls.

### 5. Create Gateway-Backed Focused Services

- [ ] Add `SupervisorLLMService` in `llm_gateway/services/supervisor.py`.
- [ ] Keep orchestration prompt construction in `execution/orchestration/room_supervisor_service.py`. `SupervisorLLMService` only accepts already-built `system_prompt` and `user_prompt` strings, sends them to the gateway with a provider-neutral logical model id, performs JSON/text parsing, and exposes streaming.
- [ ] Define the new service API with these exact methods:
  - `call_json(system_prompt: str, user_prompt: str, *, model: str | None = None, timeout_seconds: float | None = None) -> dict`
  - `call_text(system_prompt: str, user_prompt: str, *, model: str | None = None, timeout_seconds: float | None = None) -> str`
  - `call_text_stream(system_prompt: str, user_prompt: str, *, model: str | None = None, timeout_seconds: float | None = None) -> AsyncIterator[str]`
- [ ] Define the provider-neutral supervisor JSON contract:
  - `SupervisorLLMService.call_json` returns `dict` and raises `ValueError` for empty or invalid JSON, matching current legacy behavior;
  - `SupervisorLLMService.call_json` calls `llm_provider.generate_structured(messages, schema=None, json_mode=True, model=model or self.default_model, timeout_seconds=timeout_seconds)`;
  - OpenAI-backed calls use JSON enforcement through `response_format={"type": "json_object"}` or an equivalent gateway structured-generation option;
  - Bedrock-specific JSON-only instruction is added by the `llm_gateway` structured-generation request translator, not by `SupervisorLLMService`, `BedrockService`, or generic provider prompt logic;
  - provider adapters remain prompt-free except for translating structured-generation metadata into provider request shape.
  - service-adapter/provider tests assert OpenAI JSON enforcement kwargs and Bedrock JSON instruction behavior using fake gateway/provider calls, while `SupervisorLLMService` itself remains provider-neutral.
- [ ] Add shared supervisor response helpers in `llm_gateway/services/supervisor.py` for message construction, `LLMStructuredResponse.data` unwrapping, JSON parsing, and `LLMResponse.content` unwrapping. Compatibility adapters must reuse these helpers when they call gateway internal provider-hint methods directly.
- [ ] Preserve the legacy adapter APIs on `OpenAIService`:
  - `call_supervisor_llm_json(system_prompt: str, user_prompt: str, model: str | None = None) -> dict`
  - `call_supervisor_llm_text(system_prompt: str, user_prompt: str, model: str | None = None) -> str`
  - `call_supervisor_llm_text_stream(system_prompt: str, user_prompt: str, model: str | None = None) -> AsyncIterator[str]`
- [ ] Preserve the legacy adapter APIs on `BedrockService`:
  - `call_claude_json(system_prompt: str, user_prompt: str, model: str | None = None) -> dict`
  - `call_claude_text(system_prompt: str, user_prompt: str, model: str | None = None) -> str`
  - `call_claude_text_stream(system_prompt: str, user_prompt: str, model: str | None = None) -> AsyncIterator[str]`
- [ ] Preserve legacy concrete model override semantics:
  - `OpenAIService.call_supervisor_llm_*` treats `model` as an OpenAI logical id or concrete OpenAI model override and routes through the OpenAI provider context;
  - `BedrockService.call_claude_*` treats `model` as a Bedrock logical id or concrete Bedrock model override and routes through the Bedrock provider context;
  - if a concrete override is not registered in `ModelRegistryImpl`, the compatibility adapter must still supply the correct provider context so the gateway does not guess.
- [ ] Implement the previous compatibility rule without adding `provider_hint` to `SupervisorLLMService` public methods:
  - `OpenAIService.call_supervisor_llm_*` and `BedrockService.call_claude_*` call the public `SupervisorLLMService` only for provider-neutral/default paths that do not need provider-specific concrete-model context;
  - when preserving legacy unregistered concrete `model=` overrides, `OpenAIService` calls `_generate_*_with_provider_hint(provider_hint="openai", ...)` and `BedrockService` calls `_generate_*_with_provider_hint(provider_hint="bedrock", ...)` on the injected gateway;
  - those direct hinted calls reuse the supervisor response helpers for JSON parsing and text/content extraction so return shapes stay identical.
- [ ] Add tests for provider-hinted supervisor compatibility paths:
  - registered model with mismatched provider hint fails with `LLMModelRoutingError`;
  - unregistered `model="custom-openai-model"` works only through the OpenAI compatibility path;
  - unregistered `model="custom-bedrock-model"` works only through the Bedrock compatibility path;
  - no public protocol or `SupervisorLLMService.call_*` signature accepts `provider_hint`.
- [ ] Compatibility adapters pass workflow timeouts to `SupervisorLLMService` or the internal provider-hinted gateway helper:
  - OpenAI supervisor JSON uses `timeout_seconds=llm_gateway_config.supervisor_json_timeout_seconds`;
  - OpenAI supervisor text uses `timeout_seconds=llm_gateway_config.supervisor_text_timeout_seconds`;
  - OpenAI supervisor stream uses `timeout_seconds=llm_gateway_config.supervisor_stream_timeout_seconds`;
  - Bedrock compatibility calls use `timeout_seconds=llm_gateway_config.bedrock_request_timeout_seconds`.
- [ ] Ensure `BedrockService.call_claude_*` defaults to logical model `bedrock_supervisor_model`, not `supervisor_model`, so it always calls Bedrock regardless of `settings.use_bedrock_supervisor`.
- [ ] Add tests proving `BedrockService.call_claude_json`, `call_claude_text`, and `call_claude_text_stream` use `bedrock_supervisor_model` by default and preserve custom `model="custom-bedrock-model"` override semantics.
- [ ] Add tests proving `OpenAIService.call_supervisor_llm_json`, `call_supervisor_llm_text`, and `call_supervisor_llm_text_stream` preserve custom `model="custom-openai-model"` override semantics.
- [ ] Route supervisor calls through gateway/model-registry configuration, not provider branching inside `SupervisorLLMService`:
  - derive `default_supervisor_model` through `LLMGatewayConfig.from_settings(settings)`;
  - pass `default_model=llm_gateway_config.default_supervisor_model` into `SupervisorLLMService`;
  - make `SupervisorLLMService` call the gateway with `model=model or self.default_model`;
  - keep provider choice inside the `supervisor_model` registry entry and `LLMGatewayImpl._resolve_provider`.
- [ ] Add `DiscoveryLLMService` and move the prompt/generation part of `expand_query_for_discovery` into it.
- [ ] Keep `settings.discovery_query_expansion_threshold` access outside `llm_gateway/services/`: pass `max_expansion_words` as a primitive constructor argument from the composition root or keep threshold gating in the caller before invoking `DiscoveryLLMService`.
- [ ] Add `EmbeddingLLMService` and move `get_embedding(text, target_dim=None)` provider calls behind it for OpenAI and Gemini compatibility adapters.
- [ ] Add `DebateLLMService` and move `short_debate_with_openai` and `long_debate_with_openai` provider calls behind it while preserving existing method names on `OpenAIService`.
- [ ] Add `AgentSelectionLLMService` and move `select_best_agent_for_task` prompt/call logic into it.
- [ ] Add `MessageParserLLMService` and move `parse_user_message_by_llm` prompt/call logic into it.
- [ ] Inject `debate_rounds` as a primitive into `MessageParserLLMService` or into the `OpenAIService` adapter binding from `main.py`/room runtime. `app_shell/openai_service.py` must not read `settings.debate_rounds` directly after migration.
- [ ] Add `SummaryLLMService` and move debate/non-debate summarization logic into it.
- [ ] Preserve exact legacy compatibility adapter signatures on `OpenAIService`:
  - `expand_query_for_discovery(query: str) -> str`;
  - `select_best_agent_for_task(meta_task_description: str, agents: list[Agent]) -> str`;
  - `summarize_agent_responses_stream(agent_responses: list[dict[str, str]], mode: str = "non_debate", user_question: str | None = None) -> AsyncIterator[str]`;
  - `summarize_agent_responses(agent_responses: list[dict[str, str]], mode: str = "non_debate", user_question: str | None = None) -> str`;
  - `summarize_debate_answer(agent_responses: list[dict[str, str]]) -> str`;
  - `summarize_non_debate_answer(agent_responses: list[dict[str, str]]) -> str`.
  - `generate_chat_context(user_input: str, agent_response: str, context_data: ContextData) -> str`;
  - `generate_room_memory_content(messages: list[RoomAgentMessage], room_memory_content: MemoryContent) -> str`;
  - `parse_user_message_by_llm(message_text: str, selected_agent_set: dict = None, is_debate_mode: bool = False, auto_assign_agents: bool = False, agents: list[Agent] = None, conversation_context: str | None = None, explicit_mentions: list[dict] | None = None) -> dict`.
- [ ] Define `SummaryLLMService` with a DTO-based internal API that includes agent response text, agent id/name when available, mode, user question, optional room config primitives, and optional conversation context.
- [ ] Add tests for both existing summary calling forms: positional `agent_responses` with keyword `mode`/`user_question`, and alias methods for debate/non-debate summaries.
- [ ] Add service-adapter tests that assert gateway DTO services unwrap `LLMResponse.content` and `LLMStructuredResponse.data` before returning legacy `str`/`dict` results.
- [ ] Add service-adapter tests for empty response errors, JSON parse failures, non-JSON fallback behavior in `GeminiService.lead_ai_completion`, and existing parse-user-message fallback behavior.
- [ ] Add `RoomMemoryLLMService` and move `generate_chat_context` and `generate_room_memory_content` into it.
- [ ] Each focused service receives the narrowest protocol that covers its methods:
  - embedding-only gateway services receive `LLMEmbeddingGateway`;
  - vector-indexing/search consumers receive `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway`, never the optional-return `EmbeddingServiceProtocol`;
  - compatibility/API embedding surfaces receive `EmbeddingServiceProtocol` only when the caller explicitly handles `None`;
  - non-streaming text services receive `LLMTextGateway` or `LLMStructuredGateway`;
  - supervisor and summary services that stream receive `LLMGateway` or a service-specific protocol combining text, structured, and stream methods.
- [ ] Each focused service accepts primitives or `common/dto/llm_workflows.py` DTOs, not `models.*`, `a2a.types`, or `app_shell` classes.
- [ ] Add translator helpers inside the compatibility adapters to convert existing domain objects to DTO inputs before calling focused services.
- [ ] Add tests in `tests/test_llm_gateway_service_adapters.py` using fake gateway responses. Assert exact logical model ids, key kwargs, and returned values.
- [ ] Move Bedrock JSON extraction and request-shape assertions from `tests/test_service_bedrock.py` into provider/service tests that target `llm_gateway/providers/bedrock_provider.py`, the structured-generation translator, and `SupervisorLLMService`.
- [ ] Keep `tests/test_service_bedrock.py` focused on `BedrockService` compatibility methods delegating to `SupervisorLLMService` for default/registered calls and to Bedrock-hinted gateway helpers for unregistered concrete override calls.
- [ ] Run:
  ```bash
  uv run pytest tests/test_llm_gateway_service_adapters.py tests/test_supervisor_bedrock_routing.py
  ```
- [ ] Expected result: new service tests pass. `RoomSupervisorService` routing tests are rewritten and run in Step 7 after dependency rewiring.

### 6. Convert Legacy `app_shell` Services into Compatibility Adapters

- [ ] Update `OpenAIService.__init__` to accept the focused services, `embedding_service: ModelSelectableEmbeddingServiceProtocol | None = None`, and `llm_gateway_config: LLMGatewayConfig | None = None` as optional constructor arguments for tests/direct construction.
- [ ] Add a `bind_llm_services(..., llm_gateway_config: LLMGatewayConfig)` method or explicit constructor injection so `main.py` can supply gateway-backed services and the OpenAI supervisor timeout primitives copied from config.
- [ ] Make `openai_service = OpenAIService()` side-effect-free at import time. It must not construct SDK clients, read env vars, or read LLM settings.
- [ ] Define unbound compatibility behavior: methods that require focused services raise a clear binding error such as `LLMServiceNotBoundError` if called before injection.
- [ ] Add tests proving legacy singleton construction has no SDK/env side effects and unbound methods fail with the clear binding error.
- [ ] Replace `self.client.chat.completions.create` calls with calls to the focused services.
- [ ] Store OpenAI compatibility supervisor timeout primitives from `llm_gateway_config` and use them in `call_supervisor_llm_json`, `call_supervisor_llm_text`, and `call_supervisor_llm_text_stream`, including the provider-hinted concrete override paths.
- [ ] Preserve `OpenAIService.get_embedding(text, target_dim=None)` and delegate it to the model-selectable embedding dependency with `embedding_service.get_embedding(text, target_dim=target_dim, model="embedding_model")`.
- [ ] Preserve `OpenAIService.short_debate_with_openai(...)` and `OpenAIService.long_debate_with_openai(...)`, and delegate both to `DebateLLMService`.
- [ ] Remove these imports from `app_shell/openai_service.py`:
  - `AsyncOpenAI`
  - OpenAI chat parameter types
  - `dotenv.load_dotenv`
  - direct LLM env access through `os.getenv`
- [ ] Keep `openai_service = OpenAIService()` so modules that still import the singleton do not break during this stage.
- [ ] Update `GeminiService.get_embedding(text)` to delegate to the model-selectable embedding dependency with `get_embedding(text, model="gemini_embedding_model_name")` and preserve the existing `list[float] | None` return shape.
- [ ] Update `GeminiService.generate_text(prompt, context=None)` to delegate generation to `LLMGatewayImpl` using logical model `gemini_model_name`.
- [ ] Update `GeminiService.lead_ai_completion(query, context=None)` to delegate generation to `LLMGatewayImpl` using logical model `gemini_model_name`, parse JSON, and preserve the existing fallback `{"steps": [{"description": content, "step_id": "single_step"}]}` for non-JSON responses.
- [ ] Update `GeminiService.process_task(task)` to keep task/history mutation in `app_shell/gemini_service.py`, but delegate the Gemini text generation call to `LLMGatewayImpl` using logical model `gemini_model_name`.
- [ ] Update `GeminiService.summarize_output(content)` to delegate text generation to `LLMGatewayImpl` using logical model `gemini_model_name`.
- [ ] Keep `GeminiService` as a thin compatibility wrapper in this migration stage even if production imports are reduced, so external/test imports do not break prematurely.
- [ ] Add `tests/test_service_gemini.py` covering retained `GeminiService.get_embedding`, `generate_text`, `lead_ai_completion`, `process_task`, and `summarize_output` behavior with fake gateway-backed services, plus import/side-effect compatibility tests for the singleton.
- [ ] Update `BedrockService` so default/registered supervisor JSON/text/stream calls delegate to `SupervisorLLMService`, while unregistered concrete Bedrock `model=` overrides call the Bedrock-hinted internal gateway helper and reuse supervisor response helpers.
- [ ] Make `gemini_service = GeminiService()` and `bedrock_service = BedrockService()` side-effect-free at import time. They must not construct SDK clients, read env vars, or read LLM settings before their gateway-backed dependencies are injected.
- [ ] Add unbound-method tests for `GeminiService` and `BedrockService` compatibility methods mirroring the `OpenAIService` binding behavior.
- [ ] Keep public methods and return shapes stable for callers already tested in `tests/test_service_openai.py` and `tests/test_supervisor_bedrock_routing.py`.
- [ ] Run:
  ```bash
  uv run pytest tests/test_service_openai.py tests/test_service_bedrock.py tests/test_supervisor_bedrock_routing.py tests/test_llm_app_shell_boundaries.py
  ```
- [ ] Expected result: legacy service behavior tests pass, and boundary tests pass because legacy service files no longer import provider SDKs or read LLM env vars.

### 7. Rewire Runtime Dependencies

- [ ] Before changing `main.py`, refactor `execution/orchestration/room_supervisor_service.py` so module import does not construct legacy LLM providers:
  - replace `room_supervisor_service = RoomSupervisorService()` with a bindable proxy, factory, or no-arg instance that has no provider side effects;
  - add a `bind_supervisor_llm_service(supervisor_llm_service)` path or constructor argument;
  - keep `RoomSupervisorService` responsible for orchestration prompts and room-specific decision logic;
  - add tests proving importing `execution.orchestration.room_supervisor_service` does not instantiate `OpenAIService`, `BedrockService`, or provider SDK clients.
- [ ] In `main.py`, create focused services after `llm_provider`:
  ```python
  llm_gateway_config = LLMGatewayConfig.from_settings(settings)
  llm_provider = LLMGatewayImpl(
      model_registry=model_registry,
      config=llm_gateway_config,
  )
  supervisor_llm_service = SupervisorLLMService(
      llm_provider=llm_provider,
      default_model=llm_gateway_config.default_supervisor_model,
  )
  embedding_llm_service = EmbeddingLLMService(llm_provider=llm_provider)
  discovery_llm_service = DiscoveryLLMService(llm_provider=llm_provider)
  debate_llm_service = DebateLLMService(llm_provider=llm_provider)
  agent_selection_llm_service = AgentSelectionLLMService(llm_provider=llm_provider)
  summary_llm_service = SummaryLLMService(llm_provider=llm_provider)
  room_memory_llm_service = RoomMemoryLLMService(llm_provider=llm_provider)
  message_parser_llm_service = MessageParserLLMService(llm_provider=llm_provider)
  ```
- [ ] Bind those services plus `llm_gateway_config` into `openai_service` only for callers that still depend on the compatibility singleton. The binding must set JSON/text/stream timeout primitives used by retained `call_supervisor_llm_*` methods.
- [ ] Bind gateway-backed dependencies into retained Gemini and Bedrock compatibility singletons in the same composition root:
  - `gemini_service` receives the model-selectable embedding dependency and Gemini text-generation gateway/service dependency;
  - `bedrock_service` receives `SupervisorLLMService`, the provider-hinted gateway helper dependency needed for concrete Bedrock overrides, and `llm_gateway_config`;
  - all three retained LLM singletons (`openai_service`, `gemini_service`, `bedrock_service`) expose an `is_bound` property used by startup wiring tests.
- [ ] Bind the gateway-backed embedding dependency into `agent_viewset.bind_agent_viewset_dependencies(...)` instead of `embedding_source=openai_service`; the bound object must satisfy `EmbeddingServiceProtocol` and must not be the OpenAI compatibility singleton.
- [ ] Update `api_gateway/viewsets/agent.py` to use `EmbeddingServiceProtocol` instead of `app_shell.bound.EmbeddingProvider`.
- [ ] Update `app_shell/bound.py` exports and remove the local `EmbeddingProvider` dependency from API gateway viewsets after consumers use the common protocol.
- [ ] Update `tests/test_api_thin_adapters.py` and `tests/fixtures/phase9_api_routes.json` so route inventory expectations reference the common embedding protocol instead of `app_shell.bound.EmbeddingProvider`.
- [ ] Update existing non-legacy `LLMProvider` consumers after splitting raw provider adapters from public gateway protocols:
  - `agent/facade.py`: use `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway` because vector-indexing paths require a non-optional embedding;
  - `context_memory/search.py`: use `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway` because vector search paths require a non-optional embedding;
  - `context_memory/projection.py`: use `LLMStructuredGateway` or the narrow protocol required by projection;
  - `context_memory/summary.py`: use `LLMStructuredGateway`;
  - `context_memory/facade.py`: store and forward gateway-facing protocols to delegated search/projection/summary paths;
  - `container.py`: use `LLMGateway` or narrow protocols in factory signatures.
- [ ] Replace `discovery_query_expander=openai_service` with `discovery_query_expander=discovery_llm_service`.
- [ ] Replace supervisor injection in `RoomSupervisorService` with `supervisor_llm_service`.
- [ ] Replace or bind each current production `openai_service` consumer explicitly:
  - `app_shell/agent_resolver_service.py`: inject `AgentSelectionLLMService` for `select_best_agent_for_task`.
  - `app_shell/room_coordinator_service.py`: inject `SummaryLLMService` for `summarize_agent_responses`.
  - `app_shell/room_runtime.py`: inject `MessageParserLLMService` for `parse_user_message_by_llm` and keep room-runtime orchestration outside `llm_gateway`.
  - `app_shell/memory_service.py`: inject `RoomMemoryLLMService` for `generate_chat_context` and `generate_room_memory_content`; inject the compaction provider through a gateway-backed protocol instead of passing `openai_service`.
  - `common/utils/context_utils.py`: migrate turn-notes enrichment away from the legacy `call_supervisor_llm_json(system_prompt=..., user_prompt=..., model=...)` provider shape to a gateway `generate_structured` protocol using logical model `context_memory_legacy_json_model`.
  - `app_shell/memory_search_service.py`: inject a non-optional embedding dependency for vector search.
  - `app_shell/debate_service.py`: inject `DebateLLMService` for short and long debate calls.
  - `app_shell/database_service.py`: add constructor or bind injection for a non-optional embedding dependency, set `db_service` after binding in `main.py`, and replace all `self.ai_service.get_embedding(...)` calls with the injected embedding dependency.
  - `execution/orchestration/factory.py`: update default dependency wiring so `openai_service` is not the default LLM dependency for new execution components.
  - `execution/orchestration/room_message_center.py`: change `RoomMessageCenter.__init__` and `create_room_message_center` from `openai_service` to `summary_llm_service` or a summary protocol, then inject `SummaryLLMService` for streaming and non-streaming summarization.
- [ ] Update `execution/orchestration/supervisor_executor.py` so it no longer reads `settings.debate_rounds`; it receives `debate_rounds: int` from `execution/orchestration/factory.py` or the composition root, and tests cover the constructor/factory default path.
- [ ] Update `tests/test_execution_protocols.py` so `create_room_message_center` propagates `summary_llm_service` and no longer asserts `runtime.openai_service is deps["openai_service"]`.
- [ ] Update `tests/test_unified_summary.py` fakes from `rmc.openai_service.summarize_*` to the new summary dependency.
- [ ] Run `rg -n "openai_service|RoomSupervisorService\\(|call_supervisor_llm|summarize_agent_responses|get_embedding" tests` and update every affected focused test file that exercises migrated dependencies before relying on the full suite.
- [ ] After these replacements, run `rg -n "openai_service|gemini_service|bedrock_service|OpenAIService|GeminiService|BedrockService" common app_shell execution agent room context_memory platform_module api api_gateway delivery hub_runtime_bridge jobs dal database llm_gateway main.py container.py` and classify every remaining production match into exactly one bucket: compatibility wrapper definition, temporary adapter binding, or call site that still needs migration.
- [ ] Update `tests/test_platform_module_protocols.py` so it asserts the gateway-backed discovery service is wired into platform dependencies.
- [ ] Add or update a main wiring test so `agent_viewset.bind_agent_viewset_dependencies` receives the embedding service or embedding adapter, not `openai_service`.
- [ ] Add a startup wiring test that imports/builds the app composition root and asserts every retained legacy LLM singleton is bound before request handling. The test should fail if `openai_service`, `gemini_service`, or `bedrock_service` still has unbound methods after startup wiring.
- [ ] Run:
  ```bash
  uv run pytest tests/test_platform_module_protocols.py tests/test_execution_protocols.py tests/test_api_gateway_module_boundaries.py tests/test_api_thin_adapters.py tests/test_service_agent_resolver.py tests/test_memory_search_service.py tests/test_room_coordinator_service.py tests/test_unified_summary.py tests/test_service_database.py tests/test_service_room.py tests/test_scope_validation.py tests/test_api_relay.py tests/test_heartbeat_fixes.py tests/test_phase5_supervisor_integration.py tests/test_guard_consecutive_redelegation.py tests/test_context_memory_bugfixes.py tests/test_supervisor_improvements.py
  ```
- [ ] Expected result: module-boundary tests pass with `llm_gateway` as the LLM dependency owner.

### 8. Remove Remaining Direct LLM Calls and Tighten Boundaries

- [ ] Run:
  ```bash
  rg -n "AsyncOpenAI|import openai|from openai|google\\.genai|from google import genai|from google\\.genai|OPENAI_API_KEY|LEAD_AI_MODEL|CLASSIFIER_AI_MODEL|EMBEDDING_MODEL|SUPERVISOR_MODEL|GOOGLE_API_KEY|GEMINI_API_KEY|GEMINI_MODEL_NAME|GEMINI_EMBEDDING_MODEL_NAME|gpt-4o|gpt-5|text-embedding|gemini-|claude" common app_shell execution platform_module agent room context_memory api api_gateway delivery hub_runtime_bridge jobs dal database llm_gateway main.py container.py
  rg -n "aioboto3|botocore|BEDROCK_[A-Z_]+|bedrock_supervisor_model|use_bedrock_supervisor|bedrock_region" common app_shell execution platform_module agent room context_memory api api_gateway delivery hub_runtime_bridge jobs dal database llm_gateway main.py container.py
  ```
- [ ] Expected allowed matches:
  - LLM provider SDK imports only under `llm_gateway/providers/`;
  - non-LLM AWS storage imports in `app_shell/s3_service.py`;
  - compatibility wrapper names such as `app_shell/bedrock_service.py` and `BedrockService` when they do not import SDKs, read env, or access Bedrock settings directly;
  - settings fields under `common/config/settings.py`;
  - concrete provider model names only in settings, model registry, tests, and docs; providers receive concrete model ids from gateway calls but do not own model settings or literals.
- [ ] Add a boundary test for concrete provider model literals outside settings, model registry, provider tests, and docs. Replace hard-coded runtime literals such as `model="gpt-4o-mini"` in `common/utils/context_utils.py` with logical ids such as `context_memory_legacy_json_model`.
- [ ] Move any remaining provider prompt construction from `app_shell/openai_service.py` into the focused `llm_gateway/services/` class that owns that behavior, after translating domain objects to common DTOs in the adapter.
- [ ] If a compatibility method has no remaining imports outside tests, replace the call site with the focused protocol and keep the method as a thin delegation wrapper.
- [ ] Add an AST-based boundary test that fails if direct LLM SDK imports appear outside `llm_gateway/providers/`, while explicitly allowing non-LLM AWS SDK imports in storage modules. The test must inspect import module roots and aliases, covering `import openai`, `from openai import OpenAI`, `from openai import AsyncOpenAI`, `import google.genai`, `from google import genai`, `from google.genai import types`, `import aioboto3`, and `from botocore...`.
- [ ] Add a boundary test that fails if direct LLM settings-field access appears outside the allowed config/gateway files listed in Step 1.
- [ ] Add a boundary test that fails if LLM env var literals or env access forms appear outside approved config/provider credential files. Cover `os.getenv`, `os.environ[...]`, `os.environ.get`, imported `getenv`, and `dotenv`, for LLM env names including `OPENAI_API_KEY`, `LEAD_AI_MODEL`, `CLASSIFIER_AI_MODEL`, `EMBEDDING_MODEL`, `SUPERVISOR_MODEL`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL_NAME`, `GEMINI_EMBEDDING_MODEL_NAME`, and Bedrock LLM env names.
- [ ] Run:
  ```bash
  uv run pytest tests/test_llm_app_shell_boundaries.py tests/test_api_gateway_module_boundaries.py tests/test_adapter_protocols.py
  ```
- [ ] Expected result: direct LLM provider access is limited to `llm_gateway/providers/`.

### 9. Update Architecture Documentation

- [ ] Update `System-Architecture.md` to show:
  - `main.py` creates one gateway instance;
  - feature modules depend on LLM protocols or focused gateway services;
  - provider SDKs are isolated under `llm_gateway/providers/`.
- [ ] Update `docs/MODULAR_DECOUPLING_DESIGN.md` at the LLM Gateway section to record:
  - previous current state: direct calls in `app_shell`;
  - new state: registry + routing + retry + streaming in gateway;
  - compatibility adapters that remain and the exact caller groups still using them.
- [ ] Run:
  ```bash
  rg -n "direct calls|current state|LLM Gateway|openai_service|bedrock_service|gemini_service" docs System-Architecture.md
  ```
- [ ] Expected result: documentation no longer presents direct provider calls as the active architecture for migrated paths.

### 10. Final Verification

- [ ] Run focused tests:
  ```bash
  uv run pytest tests/test_llm_gateway_runtime.py tests/test_llm_gateway_provider_streaming.py tests/test_llm_gateway_service_adapters.py tests/test_llm_app_shell_boundaries.py tests/test_service_openai.py tests/test_service_bedrock.py tests/test_supervisor_bedrock_routing.py
  ```
- [ ] Expected result: all focused LLM migration tests pass.
- [ ] Run broader module tests:
  ```bash
  uv run pytest tests/test_adapter_protocols.py tests/test_adapter_unit.py tests/test_common_foundation.py tests/test_phase0_common.py tests/test_platform_module_protocols.py tests/test_execution_protocols.py tests/test_context_memory_protocols.py tests/test_agent_facade.py tests/test_context_memory_search.py tests/test_context_memory_projection.py tests/test_context_memory_facade.py tests/test_context_memory_adapters.py tests/test_api_gateway_module_boundaries.py tests/test_api_thin_adapters.py tests/test_service_agent_resolver.py tests/test_memory_search_service.py tests/test_room_coordinator_service.py tests/test_unified_summary.py tests/test_service_database.py tests/test_service_room.py tests/test_scope_validation.py tests/test_phase5_supervisor_integration.py tests/test_guard_consecutive_redelegation.py tests/test_context_memory_bugfixes.py tests/test_supervisor_improvements.py
  ```
- [ ] Expected result: all selected module-boundary tests pass.
- [ ] Run lint:
  ```bash
  uv run ruff check .
  ```
- [ ] Expected result: Ruff exits with code 0.
- [ ] Run the full suite when focused tests and lint pass:
  ```bash
  uv run pytest
  ```
- [ ] Expected result: full suite exits with code 0.

## Migration Order and Risk Controls

- Keep compatibility adapters until all callers have been moved to focused protocols. This limits blast radius because `OpenAIService` method names remain available while provider access moves behind the gateway.
- Start with supervisor, discovery expansion, and embeddings because they have smaller call surfaces and existing focused tests.
- Move summarization and room-memory workflows after the gateway service pattern is proven because those methods contain more prompt assembly and streaming edge cases.
- Do not change prompt wording and provider routing in the same test step. First preserve existing outputs using fake gateway responses, then improve routing or retry behavior behind tests.
- Do not remove `openai_service = OpenAIService()` until `rg -n "openai_service" app_shell execution platform_module agent room context_memory api api_gateway main.py tests` shows no production caller that requires the singleton.
- Do not remove `gemini_service = GeminiService()` or `bedrock_service = BedrockService()` until equivalent `rg` checks prove no production caller requires those singletons.

## Completion Criteria

- `app_shell/openai_service.py`, `app_shell/gemini_service.py`, and `app_shell/bedrock_service.py` contain no LLM provider SDK imports and no LLM env lookup.
- `llm_gateway/providers/` is the only production package that imports LLM provider SDKs. Non-LLM AWS storage access remains allowed in storage modules such as `app_shell/s3_service.py`.
- `llm_gateway/services/` imports only `common`, `llm_gateway`, standard-library modules, and typed DTO dependencies; it does not import `models`, `app_shell`, `execution`, `room`, `agent`, `platform_module`, `api`, or `api_gateway`.
- `common/dto/llm_workflows.py` contains the domain-neutral workflow DTOs required by migrated prompt services.
- `pyproject.toml` includes `llm_gateway.services`, and `tests/test_adapter_protocols.py::test_adapter_subpackages_are_packaged` covers it.
- `llm_gateway.__all__` remains `{"LLMGatewayImpl", "ModelRegistryImpl"}`, and new services/errors are imported from their submodules rather than the package root.
- `settings.supervisor_model` and `ModelRegistryImpl` preserve the current `SUPERVISOR_MODEL` override path without direct env lookup in `app_shell`.
- `context_memory_legacy_json_model` remains registered and covered by tests; context-memory code passes logical model ids to the gateway instead of concrete provider model names.
- Direct LLM settings-field access outside `common/config/` and the allowed `llm_gateway` config/registry/provider files fails boundary tests.
- Gemini API-key handling is explicitly tested so `GOOGLE_API_KEY` takes precedence and `GEMINI_API_KEY` is only a fallback when the canonical value is empty.
- `LLMGatewayImpl` owns logical model resolution, retry, timeout, and streaming dispatch.
- `OpenAIService.call_supervisor_llm_json`, `call_supervisor_llm_text`, and `call_supervisor_llm_text_stream` receive configured timeout primitives through binding and pass them to `SupervisorLLMService` or provider-hinted gateway helpers.
- `LLMProviderAdapter` is used only for raw provider adapters and provider maps; consumer modules and focused services depend on `LLMGateway` or narrower gateway-facing protocols.
- Core vector-indexing/search consumers use `RequiredEmbeddingServiceProtocol` or `LLMEmbeddingGateway`; optional `EmbeddingServiceProtocol` is limited to compatibility/API surfaces that explicitly handle `None`.
- Existing non-legacy consumers in `agent/`, `context_memory/`, `container.py`, and `api_gateway/viewsets/agent.py` use public gateway-facing protocols rather than the raw provider-adapter protocol.
- API route inventory fixtures and tests reference the common embedding protocol after `app_shell.bound.EmbeddingProvider` is removed or deprecated from the API viewset surface.
- Provider adapter methods receive concrete `model: str` values from `LLMGatewayImpl`; optional logical model ids are accepted only by gateway/service-facing APIs.
- `LLMGatewayImpl.generate_stream` raises a typed `LLMStreamingUnsupportedError` when the resolved provider cannot stream.
- `main.py` wires focused LLM services, including supervisor, embedding, discovery, debate, selection, summary, room memory, and message parsing services, from one gateway instance.
- `agent_viewset.bind_agent_viewset_dependencies` receives an embedding service or embedding adapter instead of the OpenAI compatibility singleton.
- `RoomSupervisorService` keeps orchestration prompt construction and delegates only provider-neutral `system_prompt`/`user_prompt` calls to `SupervisorLLMService`.
- `SupervisorLLMService` does not branch on OpenAI versus Bedrock; it passes a logical model id to `LLMGatewayImpl`, and provider selection remains in model registry/gateway resolution.
- `BedrockService.call_claude_*` always uses Bedrock context by defaulting to `bedrock_supervisor_model`; `OpenAIService.call_supervisor_llm_*` and `BedrockService.call_claude_*` preserve legacy concrete `model=` override behavior.
- Importing `execution.orchestration.room_supervisor_service` does not instantiate legacy OpenAI, Bedrock, Gemini, or provider SDK clients.
- User-message parsing tests cover debate mode, curated selected-agent mode, auto-assign mode, conversation context, explicit mentions, and configured debate rounds.
- `parse_user_message_by_llm` no longer reads `settings.debate_rounds`; debate rounds are supplied as a primitive through binding or service construction.
- `execution/orchestration/supervisor_executor.py` no longer reads `settings.debate_rounds`; execution factories or composition-root wiring supply debate rounds as a primitive workflow config value.
- Production `openai_service`, `gemini_service`, `bedrock_service`, `OpenAIService`, `GeminiService`, and `BedrockService` matches are reduced to compatibility wrapper definitions or explicitly documented temporary adapter bindings; no feature module depends on a legacy LLM singleton when a focused LLM service can satisfy the dependency.
- Legacy LLM singleton construction is side-effect-free, and unbound compatibility methods fail with a clear binding error.
- `DatabaseService` and its module singleton receive a non-optional embedding dependency through constructor or bind injection; they do not import or store `openai_service` for embedding calls.
- Existing behavior tests for OpenAI service methods and supervisor routing pass after being rewritten around fake gateway services.
- New boundary tests prevent regression to direct SDK access outside the gateway.
- `System-Architecture.md` and `docs/MODULAR_DECOUPLING_DESIGN.md` describe the migrated LLM ownership model.
