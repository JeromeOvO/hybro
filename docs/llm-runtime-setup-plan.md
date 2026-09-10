# Gateway Runtime and CLI Setup

## Approved scope

Deliver gateway-local Provider/authentication/text/image selection and `hybro setup`.
Interactive `hybro` opens a TUI command menu; explicit commands remain scriptable.
OpenAI ChatGPT/Codex OAuth is required alongside OpenAI, DeepSeek, and Anthropic
API-key authentication. API-key implementation alone does not complete delivery.

```text
hybro setup / config -> config.json + auth.json
                          |
existing backend callers ---------> llm_gateway -> Provider APIs / Codex Responses
bundled Agent SDKs -> backend proxy ------^
```

Preserve the original `LLMGatewayImpl` constructor, public methods, DTOs, Protocols,
model registry interfaces, request correlation, and Execution's
response/retry/tool ownership. A configured text selection overrides original
logical labels and frozen provider/model hints privately at every text entrypoint.
Missing setup explicitly requires `hybro setup`; invalid state fails closed rather
than selecting an old text route. Setup validation precedes irrelevant legacy
text-provider/model hints. Embedding implementation is outside this configuration
migration and is not enabled or configured by setup.

Bundled Agents use a thin backend inference proxy while retaining their SDKs,
tools, task execution and A2A ownership. Compose supplies the internal proxy URL
and a separate inference-only token, never Provider credentials or the runtime
mount. `hybro start` generates missing tokens in `auth.json.services`. Backend and
frontend configuration share `config.json`; normal CLI startup ignores dotenv.
This does not introduce Agent availability/identity policy, embedding removal,
`HYBRO_HOME_HOST`, automatic backend recreation on save, or `setup --apply`.

## Configuration and setup

- `HYBRO_HOME` is an absolute host path, default `~/.hybro`. Backend Compose alone
  mounts it at `/var/lib/hybro/runtime`, with that container path as `HYBRO_HOME`.
- `config.json` version 1 contains `provider.id`, `provider.auth`, `models.text`,
  optional `models.image`, application overrides under `backend`/`frontend` and
  `image_size`. `auth.json` holds the Provider credential and service secrets.
  Unknown fields, duplicate JSON keys and invalid values fail closed.
- One private directory (`0700`), regular private files (`0600`), directory flock,
  atomic replacement and caught-error pair rollback protect setup. This is not a
  crash-atomic two-file transaction; missing/mismatched state requires setup again.
- Revision hashes only canonical configuration, never secrets or token refresh.
  Setup compares the credential snapshot after verification and rejects concurrent
  credential changes before save or no-op. Unchanged selection/credentials are a
  semantic no-op.
- Interactive `hybro` (or `hybro tui`) uses a fixed-screen settings panel with
  Models and Services tabs. A centered, width-limited layout uses reverse-video
  selection and a separate notice/key-hint footer, inheriting terminal colors.
  Tab switches pages; Enter edits a row, with the current model focused in pickers.
  Valid stored authentication is reused
  for model-only changes. First/changed connections use the guided setup flow.
  Edits remain a local draft until explicit verification/save; exiting via Esc
  confirms discarding pending changes, while Ctrl-C exits without saving.
- Esc closes pickers and returns from Services rather than reporting failure.
  Service commands delegate to the existing script; Enter/Esc returns after output.
  Only container removal and forced recreation require Cancel-default confirmation.
  Terminal screen/input mode is restored on exit. Both panel and setup use the same
  JSON store and explicit setup-input boundary. Without a terminal, no arguments show help.
- Interactive setup first shows saved Provider/authentication/text/image selections
  or a missing/invalid configuration notice. This config-only display never reads
  credentials or verifies backend activation; it does not create a runtime directory.
- Setup order is Provider -> eligible authentication -> credential acquisition
  (complete browser OAuth or hidden API-key entry) -> text model -> optional image
  model -> one text verification -> save. OAuth tokens remain in memory until save.
  Only OpenAI offers OAuth. Image selection includes None when images are eligible;
  otherwise no image menu is shown. No typed option IDs are required. Menus scroll
  to fit small terminals; Escape/Ctrl-C cancels with terminal/cursor restoration.
  Explicit flags remain available; non-interactive keys are one-time setup inputs
  and are persisted after verification, not read from environment at runtime.
- Before selection/login/verification, setup checks an existing runtime directory.
  It reports and tightens an owned real directory to `0700` using a no-follow
  directory FD, original-versus-opened device/inode comparison, `fstat`, and
  `fchmod`; it never changes contents or ownership.
  Symlinks, non-directories and other owners receive actionable errors. An absent
  directory is not created by preflight, help, invalid selection or menu cancellation.
  Runtime access remains strict and never repairs permissions automatically.
- API keys come from hidden input, a stored credential, or an explicit automation
  environment input. A differing stored/automation key requires resolution; the
  same key can be verified again without a false conflict. Runtime and refresh
  read stored credentials only. Never pass secrets in command arguments.
- `scripts/hybro` delegates to `common.config.cli` through frozen uv with
  `--no-env-file`. The CLI loads JSON and projects scoped values to Compose,
  frontend and Agents; it never sources dotenv during normal startup. Legacy
  files are inputs only to explicit `config migrate --from-env` conversion.
  Gateway exports and configuration schemas remain import-safe.
- Verification makes one real text request when a human runs setup (API billing or
  subscription quota); tests mock it. A completed nonempty text reply verifies
  access; exact JSON formatting is not an authentication requirement. Failures show
  bounded stage/category and available numeric HTTP status/allowlisted error code
  and parameter. Codex parses both `error` and `detail` envelopes within 64 KiB;
  known message templates become fixed codes, never raw text or exception chains. Images are selected by eligibility
  only, never probed by a billable image request.
- Saving never starts/restarts Docker. Print manual backend restart instructions.
  `hybro start` validates setup before starting Docker. No automatic apply protocol.

Examples:

```bash
./scripts/hybro setup --provider openai --auth oauth --text-model gpt-5.5
./scripts/hybro setup --non-interactive --provider openai --auth api_key \
  --text-model gpt-5-mini --image-model none
```

OAuth requires interactive setup on the browser's host. It does not read an API
key or use `OPENAI_BASE_URL`. An unexpired stored session may be reused by setup;
an expired/near-expiry setup session requires browser authorization again.

## Mandatory OAuth implementation

Port the pi-ai browser PKCE and raw Responses contracts with MIT attribution.
Source reference: pi-mono `17de82d7bea18a6589677a9761baabc2060c9efb`, plus the
pi-ai 0.73.1 packaged subscription model catalog. These are wire-contract/catalog
references, not proof of live account entitlement.

- Public client `app_EMoamEEZ73f0CkXaXp7hrann`, random PKCE verifier/challenge and
  one-time random state. Authorize at `https://auth.openai.com/oauth/authorize`.
- Bind only `127.0.0.1:1455`, callback `http://localhost:1455/auth/callback`.
  Validate path, method, state and code; reject malformed/duplicate parameters.
  Login expires after five minutes; callback connections have five-second/8-KiB
  bounds. Cancel/timeout closes the listener and pending connections.
- Launch the host URL opener with a bounded subprocess (macOS `open`, Linux
  `xdg-open`). If unavailable/failing, print the URL for manual opening on the
  same computer. No state-less pasted code, externally bound listener, backend
  browser route, or invented redirect URI. Device/WS flows are not required.
- Exchange/refresh only at `https://auth.openai.com/oauth/token`, no redirects,
  no ambient proxy, 15-second timeout, bounded identity-encoded token response.
  Persist access token, rotating refresh token, expiry and account ID. Extract
  `https://api.openai.com/auth.chatgpt_account_id` from the access JWT as routing
  metadata only; it is **not** Hybro authentication or signature verification.
- At actual text entrypoints re-read config/auth/account under the same directory
  lock. Refresh at five minutes remaining, double-checking under the lock so
  concurrent workers share a rotation. Config/auth/account changes fail closed;
  never overwrite a changed snapshot. Refresh changes auth only, not revision.
- Async callers perform filesystem/flock work in workers with cancelable
  nonblocking lock acquisition, not blocking flock on the event loop. Once a
  refresh starts, its bounded refresh-and-save critical section owns persistence:
  caller cancellation waits for that section before releasing the lock, preventing
  rotation loss during HTTP cleanup. No background refresh task outlives its lock.

## Text and image adapters

API-key routes remain OpenAI, OpenAI-compatible DeepSeek, and Anthropic Messages.
OAuth uses a separate fixed-endpoint single-attempt REST/SSE adapter:

```text
POST https://chatgpt.com/backend-api/codex/responses
Authorization: Bearer <access token>
chatgpt-account-id: <account metadata>
originator: hybro
User-Agent: hybro
```

Send the documented Responses beta header, `store:false`, `stream:true`, ordered
text/function calls/results, flat function definitions and choice. Preserve exact
original call IDs, request correlation and frozen caller transcript. No Codex CLI
agent loop, tool execution, retries inside the adapter, session cache, WebSockets,
or provider plugin framework. Original gateway retry policy remains unchanged.

A missing `Content-Type` does not reject an otherwise valid SSE response;
JSON events and a terminal response remain mandatory.
Translate UTF-8 LF/CRLF/CR SSE, including EOF residual frames, text/refusal/reasoning
summaries, output-indexed tool starts/deltas/argument completion/item completion,
terminal usage and finish. Validate argument backfills and terminal lifecycle;
malformed events, missing terminal output and provider errors fail with sanitized
typed errors. Known streamed error codes preserve caller-owned retry classification
without retaining raw error messages. Completed/done events may omit status, as in
pi-ai; explicit failed/unknown statuses and contradictory incomplete events do not
become success. Stop and close at the terminal event rather
than awaiting HTTP EOF. Truncation preserves partial output, actual usage and
`length`, without declaring partial functions executable. Ordinary generation
returns truncated text/usage rather than retrying a completed inference; structured
output still undergoes local validation. User content uses explicit `input_text`
arrays, matching pi-ai's Codex serializer. Assistant history uses complete
`output_text` items with annotations, completed status and request-local message
IDs; function call IDs remain original. Gateway usage retains inclusive input-token
semantics, with cached counters reported separately. No DTO expansion for signed
reasoning replay or reasoning-token accounting; do not fabricate encrypted items.

Structured generation uses existing JSON instruction helpers plus local JSON/schema
validation and fails without hidden repair calls. The pi-ai 0.73.1 subscription
catalog includes `gpt-5.1`, `gpt-5.1-codex-max`, `gpt-5.1-codex-mini`, `gpt-5.2`,
`gpt-5.2-codex`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`, `gpt-5.4`,
`gpt-5.4-mini`, and `gpt-5.5`; `gpt-5.5` is the default recommendation. These are
source-confirmed IDs, not an account-entitlement inventory. Reasoning respects
per-model mappings (5.1 mini low/minimal -> medium; later minimal -> low; no
xhigh for 5.1 models). Local output ceilings remain 32768. Arbitrary public-API
model IDs are ineligible.
OAuth image catalog is empty. Never use an OAuth token for public API/image or
embedding endpoints, regardless of `OPENAI_BASE_URL`.

Codex does not accept `max_output_tokens` in this verified request body. Validate
local requested bounds (1..32768), cap emitted UTF-8 bytes at four times that
value, and report `length` with actual consumed usage when terminal output tokens
exceed the request's bound. This is **not** exact incremental token enforcement and
does not cap server-side token consumption. Requests are capped at 2 MiB, SSE
wire/decoded data at 16 MiB, and text attempts at 600 seconds. Forward effective
gateway timeouts privately across all ordinary entrypoints and hints (defaults:
60 seconds for generation, 120 for streaming).
Unsupported options fail before network calls rather than silently passing through.

Only OpenAI API-key setup offers optional `gpt-image-1` generation/reference editing.
Bundled Agents call `/api/v1/internal/llm/chat/completions`, `/images/generations`
and `/images/edits` using their existing SDKs. The proxy reuses the same gateway
instance, requires a saved setup, overrides SDK model labels, and preserves tool
calls/results and streaming. Image editing accepts one multipart reference image.
No tools execute in the proxy. Independent Bearer authentication remains required
in mock mode, and no Provider credential or OAuth token is forwarded to Agents.

## Acceptance and verification

Required before handoff:

1. Mock browser callback/listener/launcher and HTTP token exchange; cover PKCE/state,
   malformed input, denial, expiry, cancellation, safe persistence and redaction.
2. Cover refresh serialization, changed config/auth/account, credential rotations,
   cancellation through HTTP cleanup/persistence and preserved configuration revision.
3. Mock raw SSE to cover ordered history, exact tool call IDs, parallel argument
   lifecycles, usage, structured output, limits, errors, EOF, cancellation and close.
4. Exercise original gateway generate/structured/stream/provider-hint/frozen-turn
   entrypoints with OAuth selected; prove embedding and API-key paths unaffected.
5. Run named offline tests through the audited temp-fixture guard, both full backend
   Ruff gates, offline CLI script tests, and `git diff --check`. Never unfiltered
   pytest or functional collection. Automated tests use isolated HOME/config and
   mocked Provider/network calls; they must not read real credentials or launch
   the application lifespan. Deployment/live checks are separate, explicitly
   scoped operations, not side effects of test collection.
6. Report exact guard/list/log artifact paths, passed/skipped tests and live-service
   validation limitations for independent parent review. Preserve all unrelated
   work; stage or commit only when explicitly requested.

Code and docs must implement the complete OAuth behavior above; source research,
storage-only OAuth DTOs, disabled CLI options or API-key success do not satisfy it.
