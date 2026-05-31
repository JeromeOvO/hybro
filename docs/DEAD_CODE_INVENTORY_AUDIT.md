# Dead Code Inventory Audit

## Scope

Included:

- `src/` application code, hooks, components, stores, selectors, API helpers, room sync, and timeline logic.
- `tests/` unit tests, e2e tests, fixtures, setup files, and test utilities.
- Root config and entry files: `package.json`, `vitest.config.ts`, `playwright.config.ts`, `tsconfig.json`, and `eslint.config.mjs`.

Excluded:

- npm dependency and devDependency usage.
- Backend and sibling repositories.
- Implementation files outside `src/` and `tests/` that are merely referenced by package scripts.
- Unused `package.json` script findings.
- Config entries requiring CI, docs, hooks, deployment settings, editor integrations, or other out-of-scope activation sources.
- Low-confidence suspicious code.
- Deletion steps, cleanup batches, source edits, test edits, and config edits.

## Method

This audit used a static reference pass. It built an entrypoint manifest, resolved local import edges, computed a live closure from accepted entrypoints, and accepted findings only when file-level evidence had no live manifest path, no import-graph edge, no exact symbol consumer, and no dynamic/public/config-driven probe.

### Pre-Audit Worktree Baseline

Command: `git status --short`

```text
 M docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md
 M package-lock.json
?? docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md
```

Tracked dirty diff fingerprints:

```text
14dc87047a6c66bc632b1426d03e3eabc64713baee7758314c2f2592ed5b6ddf  -
2a0f01ab0e4fa1ee322cff0be691da07c7b8c1ffa222b25e6a6d826a0717a086  -
```

Pre-existing untracked fingerprint:

```text
7a72a6913a98465e3e0c60b5ec08de5ae5839a81cca45d6dafd064d29c53fba5  docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md
```

### Package Scripts

Command:

```bash
node -e "const p=require('./package.json'); for (const [name, cmd] of Object.entries(p.scripts || {})) console.log(name + ': ' + cmd)"
```

Output:

```text
dev: next dev --turbopack
build: next build
start: next start
lint: next lint
test: vitest run
test:watch: vitest
test:coverage: vitest run --coverage
test:ui: vitest --ui
test:e2e: playwright test
test:e2e:ui: playwright test --ui
test:e2e:headed: playwright test --headed
test:all: npm run test && npm run test:e2e
```

`package.json` scripts are standard tool commands: `next dev`, `next build`, `next start`, `next lint`, `vitest`, and `playwright test`.

Package-script target extraction found no concrete repository file targets. `test:all` contains shell chaining (`npm run test && npm run test:e2e`) and was treated as a reachability input only, not as evidence that any script entry is dead.

### Package Script Target Extraction

Command:

```bash
node - <<'NODE'
const p = require('./package.json')
const scripts = p.scripts || {}
const fileToken = /(?:^|\s)(?:node|tsx|ts-node|vite-node|playwright|vitest|next|eslint|sh|bash)?\s*([A-Za-z0-9_./-]+\.(?:js|cjs|mjs|ts|tsx|json|config\.ts|config\.mjs))/g
for (const [name, cmd] of Object.entries(scripts)) {
  const targets = []
  let match
  while ((match = fileToken.exec(cmd))) targets.push(match[1])
  const unresolvedShell = /&&|\|\||;|\||\$\(|`|npx\s|npm\s|pnpm\s|yarn\s|env\s/.test(cmd)
  console.log(JSON.stringify({ name, cmd, targets, unresolvedShell }))
}
NODE
```

Output:

```text
{"name":"dev","cmd":"next dev --turbopack","targets":[],"unresolvedShell":false}
{"name":"build","cmd":"next build","targets":[],"unresolvedShell":false}
{"name":"start","cmd":"next start","targets":[],"unresolvedShell":false}
{"name":"lint","cmd":"next lint","targets":[],"unresolvedShell":false}
{"name":"test","cmd":"vitest run","targets":[],"unresolvedShell":false}
{"name":"test:watch","cmd":"vitest","targets":[],"unresolvedShell":false}
{"name":"test:coverage","cmd":"vitest run --coverage","targets":[],"unresolvedShell":false}
{"name":"test:ui","cmd":"vitest --ui","targets":[],"unresolvedShell":false}
{"name":"test:e2e","cmd":"playwright test","targets":[],"unresolvedShell":false}
{"name":"test:e2e:ui","cmd":"playwright test --ui","targets":[],"unresolvedShell":false}
{"name":"test:e2e:headed","cmd":"playwright test --headed","targets":[],"unresolvedShell":false}
{"name":"test:all","cmd":"npm run test && npm run test:e2e","targets":[],"unresolvedShell":true}
```

Resolved target table command:

```bash
node - <<'NODE' > /tmp/hybro-script-targets.txt
const fs = require('fs')
const path = require('path')
const p = require('./package.json')
const scripts = p.scripts || {}
const tokens = new Set()
const tokenRe = /([A-Za-z0-9_./-]+\.(?:js|cjs|mjs|ts|tsx))/g
for (const cmd of Object.values(scripts)) {
  let match
  while ((match = tokenRe.exec(cmd))) tokens.add(match[1])
}
for (const token of tokens) {
  const normalized = path.normalize(token)
  console.log(`${token}\t${fs.existsSync(normalized) ? normalized : 'unresolved'}`)
}
NODE
sed -n '1,200p' /tmp/hybro-script-targets.txt
```

Output: no rows. There are no concrete repository file targets to traverse from package scripts. `test:all` stops at npm/shell chaining and only delegates to already recorded `test` and `test:e2e` scripts.

### TypeScript Config

Command:

```bash
node -e "const ts=require('./tsconfig.json'); console.log(JSON.stringify({baseUrl:ts.compilerOptions?.baseUrl, paths:ts.compilerOptions?.paths, include:ts.include, exclude:ts.exclude}, null, 2))"
```

Output:

```json
{
  "baseUrl": ".",
  "paths": {
    "@/*": [
      "./src/*"
    ]
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx",
    ".next/types/**/*.ts",
    ".next/dev/types/**/*.ts"
  ],
  "exclude": [
    "node_modules"
  ]
}
```

`tsconfig.json` has `baseUrl: "."` and `@/* -> ./src/*`. The import graph resolver used this configured path alias, extensionless file resolution, and directory `index` resolution before treating a missing reference as evidence.

### Next.js Entrypoints

Configured dependency: `next: ^16.2.6`. Installed package version: `16.2.6`.

Verification command:

```bash
node -p "require('./node_modules/next/package.json').version"
```

Output:

```text
16.2.6
```

Convention source checked:

- Installed `next` package version and package files under `node_modules/next`.
- Official Next.js App Router file-convention documentation:
  - `https://nextjs.org/docs/app`
  - `https://nextjs.org/docs/app/api-reference/file-conventions/route`
  - `https://nextjs.org/docs/app/api-reference/file-conventions/route-segment-config`
  - `https://nextjs.org/docs/app/api-reference/file-conventions/metadata`
  - `https://nextjs.org/docs/app/api-reference/file-conventions/middleware`

Official documentation check evidence:

```text
Opened https://nextjs.org/docs/app/api-reference/file-conventions.
Observed current docs navigation listing file-system conventions including default.js, error.js, forbidden.js, instrumentation.js, instrumentation-client.js, layout.js, loading.js, not-found.js, page.js, proxy.js, route.js, template.js, unauthorized.js, metadata files, and route segment config. The exact convention checklist used here comes from official Next.js file-convention docs. The installed package version (`16.2.6`) was verified locally and package files were used only as sanity checks for instrumentation, metadata, app routes, middleware/proxy, and route manifest handling.
Opened https://nextjs.org/docs/app/api-reference/file-conventions/metadata for metadata file conventions.
Opened https://nextjs.org/docs/app/api-reference/file-conventions/middleware, redirected to /file-conventions/proxy, for the Next 16 proxy convention.
```

Installed package sanity check:

```bash
rg -n "instrumentation|middleware|metadata|APP_ROUTE|MIDDLEWARE" node_modules/next/dist -g '*.js' -g '*.d.ts' | head -20
```

Output excerpt:

```text
node_modules/next/dist/build/create-compiler-aliases.js:164:            'private-next-instrumentation-client': [
node_modules/next/dist/shared/lib/turbopack/entry-key.js:4: * `root` -> middleware / instrumentation
node_modules/next/dist/shared/lib/turbopack/manifest-loader.js:34:            // Improve implementation of metadata routes...
node_modules/next/dist/shared/lib/turbopack/manifest-loader.js:350:        this.middlewareManifests.set((0, _entrykey.getEntryKey)(type === 'middleware' || type === 'instrumentation' ? 'root' : type, 'server', pageName), readPartialManifestContent(...)
node_modules/next/dist/shared/lib/constants.d.ts:29:     * `APP_ROUTE` represents all the API routes and metadata routes that are under `app/`
node_modules/next/dist/shared/lib/constants.d.ts:43:     * `MIDDLEWARE` represents the middleware output if present
```

Manifest source-of-truth table:

| Source area | Convention or config source | Checked patterns | Repository result |
|-------------|-----------------------------|------------------|-------------------|
| App Router segments | Next.js App Router file conventions | `layout`, `template`, `page`, `loading`, `not-found`, `forbidden`, `unauthorized`, `error`, `global-error`, `route`, `default` | 4 layouts and 19 pages present; other segment files absent |
| App Router metadata | Next.js metadata file conventions | `robots`, `sitemap`, `manifest`, `icon`, `apple-icon`, `opengraph-image`, `twitter-image` | `robots.ts` and `sitemap.ts` present; other metadata files absent |
| Root request hooks | Next.js middleware/proxy conventions for installed Next 16 line | `src/proxy.*`, `src/middleware.*` | `src/proxy.ts` present; middleware absent |
| Instrumentation | Next.js instrumentation conventions | `instrumentation.*`, `instrumentation-client.*` under root or `src` | absent |
| Pages Router | Next.js Pages Router directory if present | `src/pages/**`, `pages/**` | `src/pages: absent`; no Pages Router roots |

Manifest scan command:

```bash
find src/app -type f \( -name 'layout.tsx' -o -name 'template.tsx' -o -name 'page.tsx' -o -name 'loading.tsx' -o -name 'not-found.tsx' -o -name 'forbidden.tsx' -o -name 'unauthorized.tsx' -o -name 'error.tsx' -o -name 'global-error.tsx' -o -name 'route.ts' -o -name 'default.tsx' -o -name 'robots.ts' -o -name 'sitemap.ts' -o -name 'manifest.ts' -o -name 'icon.*' -o -name 'apple-icon.*' -o -name 'opengraph-image.*' -o -name 'twitter-image.*' \) | sort
find src -maxdepth 1 -type f \( -name 'middleware.ts' -o -name 'proxy.ts' -o -name 'instrumentation.ts' -o -name 'instrumentation-client.ts' \) | sort
if [ -d src/pages ]; then find src/pages -type f | sort; else echo 'src/pages: absent'; fi
```

Output:

```text
src/app/(auth)/layout.tsx
src/app/(auth)/sign-in/[[...sign-in]]/page.tsx
src/app/(auth)/sign-up/[[...sign-up]]/page.tsx
src/app/c/about/page.tsx
src/app/c/agents/[id]/page.tsx
src/app/c/agents/page.tsx
src/app/c/chat/page.tsx
src/app/c/hub/page.tsx
src/app/c/layout.tsx
src/app/c/page.tsx
src/app/c/pricing/page.tsx
src/app/c/room/[id]/page.tsx
src/app/d/agents/[id]/page.tsx
src/app/d/agents/page.tsx
src/app/d/discovery-api-keys/page.tsx
src/app/d/docs/page.tsx
src/app/d/hub/page.tsx
src/app/d/inspector/page.tsx
src/app/d/layout.tsx
src/app/d/page.tsx
src/app/d/register/page.tsx
src/app/layout.tsx
src/app/privacy/page.tsx
src/app/robots.ts
src/app/sitemap.ts
src/proxy.ts
src/pages: absent
```

Manifest scan found 26 framework entrypoints.

Convention checklist command:

```bash
node - <<'NODE'
const fs = require('fs')
const path = require('path')
const appNames = ['layout','template','page','loading','not-found','forbidden','unauthorized','error','global-error','route','default','robots','sitemap','manifest','icon','apple-icon','opengraph-image','twitter-image']
const rootFiles = ['proxy','middleware','instrumentation','instrumentation-client']
const exts = ['.ts','.tsx','.js','.jsx','.mjs','.cjs','.ico','.jpg','.jpeg','.png','.svg']
for (const name of appNames) {
  const found = []
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, ent.name)
      if (ent.isDirectory()) walk(full)
      else if (exts.some(ext => ent.name === name + ext || ent.name.startsWith(name + '.'))) found.push(full)
    }
  }
  if (fs.existsSync('src/app')) walk('src/app')
  console.log(`${name}\t${found.length ? found.sort().join(', ') : 'absent'}`)
}
for (const name of rootFiles) {
  const found = []
  for (const base of ['src', '.']) for (const ext of exts.slice(0,6)) {
    const file = path.join(base, name + ext)
    if (fs.existsSync(file)) found.push(file)
  }
  console.log(`${name}\t${found.length ? found.sort().join(', ') : 'absent'}`)
}
NODE
```

Checklist summary:

```text
layout: 4 present
template: absent
page: 19 present
loading: absent
not-found: absent
forbidden: absent
unauthorized: absent
error: absent
global-error: absent
route: absent
default: absent
robots: src/app/robots.ts
sitemap: src/app/sitemap.ts
manifest/icon/apple-icon/opengraph-image/twitter-image: absent
proxy: src/proxy.ts
middleware: absent
instrumentation: absent
instrumentation-client: absent
```

### Test Entrypoints

Vitest config source:

```text
projects:
  stores: include src/**/*.test.ts, tests/unit/stores/**/*.test.ts; exclude tests/e2e/**
  api: include tests/unit/lib/**/*.test.ts; setup ./tests/setup/vitest.setup.ts
  components: include tests/unit/components/**/*.test.tsx, tests/unit/hooks/**/*.test.ts; setup ./tests/setup/vitest.setup.ts
```

Vitest config expands to 95 active unit/component/hook/store test files and 4 setup files:

- `src/**/*.test.ts`
- `tests/unit/stores/**/*.test.ts`
- `tests/unit/lib/**/*.test.ts`
- `tests/unit/components/**/*.test.tsx`
- `tests/unit/hooks/**/*.test.ts`
- setup files under `tests/setup/`

Expansion evidence:

```bash
wc -l /tmp/hybro-vitest-included-tests.txt /tmp/hybro-vitest-setup-files.txt
sed -n '1,12p' /tmp/hybro-vitest-included-tests.txt
cat /tmp/hybro-vitest-setup-files.txt
```

Output excerpt:

```text
95 /tmp/hybro-vitest-included-tests.txt
4  /tmp/hybro-vitest-setup-files.txt
src/hooks/room/sse-handlers/__tests__/pending-turn-buffer.test.ts
src/lib/room-timeline/message-groups.test.ts
src/stores/message-store/__tests__/convert-api-message.test.ts
src/stores/message-store/__tests__/hitl-upsert.test.ts
src/stores/message-store/__tests__/hydration-filter.test.ts
src/stores/message-store/__tests__/infer-turn-terminal-status.test.ts
src/stores/message-store/__tests__/resolve-display-type.test.ts
src/stores/message-store/__tests__/stale-detection.test.ts
src/stores/message-store/__tests__/store.test.ts
src/stores/message-store/__tests__/upsert.test.ts
src/stores/streaming-store/__tests__/streaming-store.test.ts
tests/unit/components/artifact-renderer.test.tsx
tests/setup/mock-fetch-sse.ts
tests/setup/msw-handlers.ts
tests/setup/msw-server.ts
tests/setup/vitest.setup.ts
```

Playwright config expands to 6 E2E specs, `tests/e2e/global-setup.ts`, and `tests/e2e/fixtures/auth.ts`.

Playwright config source:

```text
testDir: ./tests/e2e
globalSetup: ./tests/e2e/global-setup.ts
webServer.command: npm run dev
```

Expansion evidence:

```bash
wc -l /tmp/hybro-playwright-included-specs.txt /tmp/hybro-playwright-setup-files.txt /tmp/hybro-playwright-fixtures.txt
cat /tmp/hybro-playwright-included-specs.txt /tmp/hybro-playwright-setup-files.txt /tmp/hybro-playwright-fixtures.txt
```

Output:

```text
6 /tmp/hybro-playwright-included-specs.txt
1 /tmp/hybro-playwright-setup-files.txt
1 /tmp/hybro-playwright-fixtures.txt
tests/e2e/auth.spec.ts
tests/e2e/authenticated-flows.spec.ts
tests/e2e/chat.spec.ts
tests/e2e/error-handling.spec.ts
tests/e2e/room-timeline.spec.ts
tests/e2e/room.spec.ts
tests/e2e/global-setup.ts
tests/e2e/fixtures/auth.ts
```

Complete active test/support reconciliation:

```text
live	test	src/hooks/room/sse-handlers/__tests__/pending-turn-buffer.test.ts
live	test	src/lib/room-timeline/message-groups.test.ts
live	test	src/stores/message-store/__tests__/convert-api-message.test.ts
live	test	src/stores/message-store/__tests__/hitl-upsert.test.ts
live	test	src/stores/message-store/__tests__/hydration-filter.test.ts
live	test	src/stores/message-store/__tests__/infer-turn-terminal-status.test.ts
live	test	src/stores/message-store/__tests__/resolve-display-type.test.ts
live	test	src/stores/message-store/__tests__/stale-detection.test.ts
live	test	src/stores/message-store/__tests__/store.test.ts
live	test	src/stores/message-store/__tests__/upsert.test.ts
live	test	src/stores/streaming-store/__tests__/streaming-store.test.ts
live	test	tests/e2e/auth.spec.ts
live	test	tests/e2e/authenticated-flows.spec.ts
live	test	tests/e2e/chat.spec.ts
live	test	tests/e2e/error-handling.spec.ts
live	test	tests/e2e/room-timeline.spec.ts
live	test	tests/e2e/room.spec.ts
live	test	tests/unit/components/artifact-renderer.test.tsx
live	test	tests/unit/components/chat-page-cards.test.tsx
live	test	tests/unit/components/composer/ComposerShell.test.tsx
live	test	tests/unit/components/composer/HitlResponseBar.test.tsx
live	test	tests/unit/components/consumer-agent-profile.test.tsx
live	test	tests/unit/components/conversation/agent-card.test.tsx
live	test	tests/unit/components/conversation/agent-content-block.test.tsx
live	test	tests/unit/components/conversation/agent-response-detail-pane.test.tsx
live	test	tests/unit/components/conversation/scroll-state.test.tsx
live	test	tests/unit/components/conversation/user-answer-card.test.tsx
live	test	tests/unit/components/conversation/user-message-block.test.tsx
live	test	tests/unit/components/developer-agent-detail.test.tsx
live	test	tests/unit/components/developer-agents-page.test.tsx
live	test	tests/unit/components/developer-dashboard.test.tsx
live	test	tests/unit/components/developer-sidebar.test.tsx
live	test	tests/unit/components/group-selector.test.tsx
live	test	tests/unit/components/hitl-cards.test.tsx
live	test	tests/unit/components/hitl-inline-reply-form.test.tsx
live	test	tests/unit/components/hub-page-content.test.tsx
live	test	tests/unit/components/hub-section.test.tsx
live	test	tests/unit/components/inline-chips.test.tsx
live	test	tests/unit/components/mode-selector.test.tsx
live	test	tests/unit/components/nav-hub.test.tsx
live	test	tests/unit/components/part-renderer.test.tsx
live	test	tests/unit/components/room-chat-input-empty-room.test.tsx
live	test	tests/unit/components/room-chat-input-mention.test.tsx
live	test	tests/unit/components/room-chat-input.test.tsx
live	test	tests/unit/components/room-page-prefill.test.tsx
live	test	tests/unit/components/room-page-shell-agent-detail.test.tsx
live	test	tests/unit/components/room-setting-form-stale.test.tsx
live	test	tests/unit/components/room-setting-form.test.tsx
live	test	tests/unit/components/settings-dialog.test.tsx
live	test	tests/unit/components/supervisor-toggle.test.tsx
live	test	tests/unit/components/truncated-content-integration.test.tsx
live	test	tests/unit/components/truncated-content.test.tsx
live	test	tests/unit/components/use-case-card.test.tsx
live	test	tests/unit/hooks/double-send-guard.test.ts
live	test	tests/unit/hooks/hitl-sse-handlers.test.ts
live	test	tests/unit/hooks/room-hydration-initial-scroll.test.ts
live	test	tests/unit/hooks/room-lifecycle.test.ts
live	test	tests/unit/hooks/room-name-edit.test.ts
live	test	tests/unit/hooks/room/consumer-contract.test.ts
live	test	tests/unit/hooks/room/pending-turn-buffer.test.ts
live	test	tests/unit/hooks/room/sse-handlers/apply-commands.test.ts
live	test	tests/unit/hooks/selection-plain-text.test.ts
live	test	tests/unit/hooks/supervisor-toggle-creation.test.ts
live	test	tests/unit/hooks/supervisor-toggle-settings.test.ts
live	test	tests/unit/hooks/useChatRoomCreation-template.test.ts
live	test	tests/unit/hooks/useChatRoomCreation.test.ts
live	test	tests/unit/hooks/useGroupManagement-empty-room.test.ts
live	test	tests/unit/hooks/useGroupManagement.test.ts
live	test	tests/unit/hooks/useHubStatus.test.ts
live	test	tests/unit/hooks/useMessageScrollAnchoring.test.ts
live	test	tests/unit/hooks/useRoomSSE.test.ts
live	test	tests/unit/hooks/useRoomWebhook.test.ts
live	test	tests/unit/lib/agent-api.test.ts
live	test	tests/unit/lib/build-turns-incremental.test.ts
live	test	tests/unit/lib/build-turns.test.ts
live	test	tests/unit/lib/chat-mode.test.ts
live	test	tests/unit/lib/derive-final-answer.test.ts
live	test	tests/unit/lib/event-log.test.ts
live	test	tests/unit/lib/hitl-api.test.ts
live	test	tests/unit/lib/middleware.test.ts
live	test	tests/unit/lib/room-api.test.ts
live	test	tests/unit/lib/room-sync/apply-db-messages.test.ts
live	test	tests/unit/lib/selectors/map-agent-display.test.ts
live	test	tests/unit/lib/selectors/route-agent.test.ts
live	test	tests/unit/lib/selectors/select-agent-response-detail.test.ts
live	test	tests/unit/lib/selectors/select-composer-state.test.ts
live	test	tests/unit/lib/selectors/select-hitl.test.ts
live	test	tests/unit/lib/sse-connection.test.ts
live	test	tests/unit/lib/streaming/display.test.ts
live	test	tests/unit/lib/system-agents.test.ts
live	test	tests/unit/lib/task-api.test.ts
live	test	tests/unit/lib/turn-live-shell-progress.test.ts
live	test	tests/unit/lib/turn-live-shell.test.ts
live	test	tests/unit/lib/use-case-templates.test.ts
live	test	tests/unit/stores/fixture-type-safety.test.ts
live	test	tests/unit/stores/message-entity-conformance.test.ts
live	test	tests/unit/stores/message-store-edge-cases.test.ts
live	test	tests/unit/stores/redirect-pending-message.test.ts
live	test	tests/unit/stores/room-ui-store-handoff.test.ts
live	test	tests/unit/stores/room-ui-store.test.ts
live	test	tests/unit/stores/scope-and-stale.test.ts
live	test-support	tests/e2e/fixtures/auth.ts
live	test-support	tests/e2e/global-setup.ts
live	test-support	tests/fixtures/index.ts
live	test-support	tests/setup/mock-fetch-sse.ts
live	test-support	tests/setup/msw-handlers.ts
live	test-support	tests/setup/msw-server.ts
live	test-support	tests/setup/vitest.setup.ts
live	test-support	tests/utils/test-utils.tsx
```

### Import Graph And Live Closure

Commands produced these summary counts:

```text
imports: 1723
unresolved local imports: 3
external/package imports: 576
manifest roots: 133
live closure: 348 files
```

The only unresolved local imports were CSS imports:

```text
src/app/c/layout.tsx    @/app/globals.css    unresolved
src/app/d/layout.tsx    @/app/globals.css    unresolved
src/app/layout.tsx      ./globals.css        unresolved
```

These unresolved imports do not affect any accepted finding.

Analyzer limitations and resulting exclusions:

- Next convention source: the exact checklist is taken from official Next.js file-convention documentation and checked against installed `next@16.2.6` package behavior markers. The installed package does not expose a single complete convention-list command in this audit. Findings that would depend on ambiguous framework convention handling are therefore excluded; no accepted finding is under `src/app`, `src/pages`, root middleware/proxy/instrumentation, or metadata convention paths.
- Test config expansion: Vitest and Playwright entries were read from the checked-in config files and expanded with static glob logic recorded in this report. Tool-resolved config output was not used. Findings that would depend on subtle runner resolution semantics are therefore excluded; no accepted finding is a test, setup file, fixture, helper, or config entry.
- Exclusion probe granularity: accepted findings use per-file live-closure absence plus exact symbol/path probes as primary evidence. Shared dynamic/public/config probes are supporting evidence; any candidate with ambiguous dynamic, registry, generated, public API, or config-driven evidence was excluded rather than promoted.

Import analyzer metadata:

```text
tool: local Node.js static import scanner
inputs: /tmp/hybro-manifest-roots.txt plus all resolved local graph edges
candidate extensions: .ts, .tsx, .js, .jsx, .mjs, .cjs
specifier forms: static import, type import, side-effect import, export-from, require(), string-literal import()
resolver: external/package specifiers stop as external-or-package; @/* maps to src/*; relative specifiers resolve extensionless files and directory index files
skipped files: none among JavaScript/TypeScript files reached from manifest roots
unsupported syntax: computed dynamic import specifiers and non-string require/import calls are not resolved; accepted findings do not depend on those modes
unsupported non-code imports: CSS imports are recorded unresolved and not used for accepted findings
```

Resolver core used for graph edges:

```js
function resolveSpecifier(fromFile, spec) {
  if (spec.startsWith('@/')) spec = path.join('src', spec.slice(2))
  else if (spec.startsWith('.')) spec = path.normalize(path.join(path.dirname(fromFile), spec))
  else return 'external-or-package'
  const candidates = []
  for (const ext of exts) candidates.push(spec + ext)
  for (const ext of exts) candidates.push(path.join(spec, 'index' + ext))
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  return 'unresolved'
}
```

Live-closure algorithm:

```text
1. Seed queue with manifest roots from Next, Vitest, Playwright, package-script targets, and scoped config roots.
2. For each queued file, add it to /tmp/hybro-live-closure.txt.
3. Follow only resolved local graph edges into repository files.
4. Stop at external-or-package, unresolved, non-code, and unsupported computed-dynamic edges.
5. Compare /tmp/hybro-src-files.txt with /tmp/hybro-live-closure.txt for file-level candidates.
```

### Calibration

Calibration examples were selected before accepting findings:

| Mode | Known-live fixture | Expected reference type | Actual output summary | Result |
|------|--------------------|-------------------------|-----------------------|--------|
| Alias import | `src/app/layout.tsx` imports `@/components/theme-provider` | `@/*` resolves to `src/components/theme-provider.tsx` | `/tmp/hybro-import-graph.tsv` has `src/app/layout.tsx	@/components/theme-provider	src/components/theme-provider.tsx` | Passed |
| Extensionless import | `src/lib/selectors/select-agent-response-detail.ts` imports `./conversation-types` | relative extensionless import resolves to `.ts` | graph has two `./conversation-types -> src/lib/selectors/conversation-types.ts` edges | Passed |
| Directory index import | `src/components/room-page-shell.tsx` imports `@/lib/selectors` | alias directory resolves to `src/lib/selectors/index.ts` | graph resolves selector barrel; live closure includes downstream selector files | Passed |
| Barrel re-export | `src/lib/selectors/index.ts` re-exports `./route-agent` | re-export creates graph edge | graph has `src/lib/selectors/index.ts	./route-agent	src/lib/selectors/route-agent.ts` | Passed |
| Type-only import | `tests/unit/lib/event-log.test.ts` imports `RawTimelineEvent` with `import type` | type-only import still creates file reachability | graph includes type-only imports; export-level findings remain unsupported because symbol namespace precision is weaker | Passed for file-level only |
| Side-effect import | `src/app/layout.tsx` imports `./globals.css` | side-effect CSS import is detected but unresolved by TS resolver | graph records `./globals.css	unresolved`; no accepted finding depends on CSS reachability | Failed mode excluded from findings |
| Next convention | `src/app/layout.tsx`, route pages, `src/app/robots.ts`, `src/app/sitemap.ts`, `src/proxy.ts` | convention files are manifest roots | manifest output lists 26 Next entrypoints | Passed |
| Vitest setup/include | `tests/setup/vitest.setup.ts`, unit test globs | setup and 95 tests are roots | `/tmp/hybro-vitest-included-tests.txt` has 95 rows; setup list includes `tests/setup/vitest.setup.ts` | Passed |
| Playwright setup/fixture | `tests/e2e/global-setup.ts`, `tests/e2e/fixtures/auth.ts` | setup/fixture/specs are roots or reachable from specs | 6 specs included; graph has `authenticated-flows.spec.ts	./fixtures/auth	tests/e2e/fixtures/auth.ts` | Passed |

Representative calibration command:

```bash
rg -n "src/app/layout.tsx|src/lib/selectors/index.ts.*./route-agent|tests/e2e/authenticated-flows.spec.ts.*./fixtures/auth|tests/setup/vitest.setup.ts" /tmp/hybro-import-graph.tsv /tmp/hybro-live-closure.txt /tmp/hybro-manifest-roots.txt /tmp/hybro-vitest-included-tests.txt /tmp/hybro-playwright-included-specs.txt /tmp/hybro-vitest-setup-files.txt /tmp/hybro-playwright-setup-files.txt /tmp/hybro-playwright-fixtures.txt
```

Representative output excerpt:

```text
/tmp/hybro-vitest-setup-files.txt:4:tests/setup/vitest.setup.ts
/tmp/hybro-playwright-setup-files.txt:1:tests/e2e/global-setup.ts
/tmp/hybro-manifest-roots.txt:22:src/app/layout.tsx
/tmp/hybro-live-closure.txt:23:src/app/layout.tsx
/tmp/hybro-import-graph.tsv:215:src/app/layout.tsx	@/components/theme-provider	src/components/theme-provider.tsx
/tmp/hybro-import-graph.tsv:1151:src/lib/selectors/index.ts	./route-agent	src/lib/selectors/route-agent.ts
/tmp/hybro-import-graph.tsv:1284:tests/e2e/authenticated-flows.spec.ts	./fixtures/auth	tests/e2e/fixtures/auth.ts
```

## Candidate Universe

| Category | Total scanned | Live | Accepted findings | Excluded | Unsupported |
|----------|---------------|------|-------------------|----------|-------------|
| Source files | 252 | 239 | 5 | 8 | 0 |
| Exports | 918 | 0 | 0 | 0 | 918 |
| Tests | 101 | 101 | 0 | 0 | 0 |
| Test support files | 8 | 8 | 0 | 0 | 0 |
| Eligible config entries | 15 | 15 | 0 | 0 | 0 |

### Reconciliation Log

Source files not in the live closure:

| Item | Category | Outcome | Evidence reference | Notes |
|------|----------|---------|--------------------|-------|
| `src/components/scroll-range-spacer.tsx` | source file | accepted finding | exact symbol/import search empty; absent from live closure | Isolated component |
| `src/components/upgrade-button.tsx` | source file | accepted finding | exact symbol/import search empty; absent from live closure | Isolated component |
| `src/hooks/room/overlay-pending-hitl.ts` | source file | accepted finding | exact re-export path search empty; absent from live closure | Deprecated compatibility re-export |
| `src/hooks/useAutoHideScroll.ts` | source file | accepted finding | exact symbol/import search empty; absent from live closure | Isolated hook |
| `src/lib/agent-colors.ts` | source file | accepted finding | exact exported-symbol search only self; absent from live closure | Isolated utility |
| `src/components/agent-card.tsx` | source file | excluded | basename collides with active conversation/consumer agent card code | Not high-confidence enough |
| `src/components/ui/alert.tsx` | source file | excluded | shadcn UI primitive under `src/components/ui` | Generated/design-system style primitive |
| `src/components/ui/radio-group.tsx` | source file | excluded | shadcn UI primitive under `src/components/ui` | Generated/design-system style primitive |
| `src/components/ui/scroll-area.tsx` | source file | excluded | shadcn UI primitive under `src/components/ui` | Generated/design-system style primitive |
| `src/components/ui/select.tsx` | source file | excluded | shadcn UI primitive under `src/components/ui` | Generated/design-system style primitive |
| `src/hooks/room/index.ts` | source file | excluded | directory index semantics require manual review | Barrel-adjacent |
| `src/hooks/useRoomMessages.ts` | source file | excluded | comment reference in active component; hook migration context unclear | Needs manual review |
| `src/lib/types/memory.ts` | source file | excluded | memory types overlap active request/response memory models | Needs manual review |

All other source files were classified as `live` by the entrypoint-rooted closure.

Export-level reconciliation: 918 exports were enumerated with the TypeScript AST pass. Export findings were not accepted because calibrated TypeScript reference evidence for type/value namespaces was not strong enough under the design standard. Every export is classified as `unsupported by analyzer`.

Test reconciliation: all 101 `*.test.*` / `*.spec.*` files are included by active Vitest or Playwright globs. No dead-test findings were accepted.

Test-support reconciliation: all 8 support files are reachable from active setup, fixture, or helper imports. No dead test-support findings were accepted.

Config reconciliation: no eligible config entries were accepted as candidates. Root package scripts are reachability inputs only and not findings.

Package-script reachability inputs, out of eligible config-entry universe:

```text
scripts.dev, scripts.build, scripts.start, scripts.lint, scripts.test, scripts.test:watch, scripts.test:coverage, scripts.test:ui, scripts.test:e2e, scripts.test:e2e:ui, scripts.test:e2e:headed, scripts.test:all
```

Config-entry reconciliation:

| Item | Category | Outcome | Evidence reference | Notes |
|------|----------|---------|--------------------|-------|
| `tsconfig paths.@/*` | config entry | live | TypeScript config output | Alias used by source/tests and import resolver |
| `tsconfig include` | config entry | live | TypeScript config output | Project source includes active TS/TSX files |
| `tsconfig exclude` | config entry | live | TypeScript config output | Excludes `node_modules`; no dead-config finding |
| `vitest projects.stores.include` | config entry | live | Vitest config source and expansion | Expands `src/**/*.test.ts` and `tests/unit/stores/**/*.test.ts` |
| `vitest projects.stores.exclude` | config entry | live | Vitest config source and expansion | Excludes E2E tests from unit project |
| `vitest projects.api.include` | config entry | live | Vitest config source and expansion | Expands `tests/unit/lib/**/*.test.ts` |
| `vitest projects.api.setupFiles` | config entry | live | Vitest config source and expansion | Includes `tests/setup/vitest.setup.ts` and setup imports |
| `vitest projects.components.include` | config entry | live | Vitest config source and expansion | Expands component and hook tests |
| `vitest projects.components.setupFiles` | config entry | live | Vitest config source and expansion | Includes `tests/setup/vitest.setup.ts` and setup imports |
| `vitest coverage.include` | config entry | live | Vitest config source | Coverage config only; no dead-config finding |
| `vitest coverage.exclude` | config entry | live | Vitest config source | Coverage config only; no dead-config finding |
| `playwright testDir` | config entry | live | Playwright config source and expansion | Expands `tests/e2e` specs |
| `playwright globalSetup` | config entry | live | Playwright config source and expansion | Includes `tests/e2e/global-setup.ts` |
| `playwright projects.chromium` | config entry | live | Playwright config source | Active browser project |
| `playwright webServer.command` | config entry | live | Playwright config source | Runs `npm run dev` as E2E reachability input |

### Reproducibility Artifacts

The audit generated these temporary classification artifacts from the commands in this report and the implementation plan. The artifact paths are not the durable evidence by themselves; the report embeds the counts, fingerprints, and classification outputs needed to verify the reconciliation.

Generation command classes:

```text
src/test/support universes: rg --files with scoped include/exclude globs
Next manifest: find src/app for documented conventions plus root src/proxy.ts or src/middleware.ts
Vitest manifest: expand configured include globs and setupFiles from vitest.config.ts
Playwright manifest: expand tests/e2e specs, configured globalSetup, and fixture import edge
Import graph: Node resolver over static import/export-from/require/import() specifiers using tsconfig @/*, extensionless, and directory-index resolution
Live closure: breadth-first traversal from manifest roots across resolved local graph edges
Export universe: TypeScript AST enumeration of named/default/type/re-export declarations from candidate-scope source files
```

Self-contained rerun script for the main analyzer:

```bash
node - <<'NODE'
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const exts = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']
const importRe = /(?:import\s+(?:type\s+)?[^'"]*from\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|export\s+[^'"]*from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|^import\s*['"]([^'"]+)['"])/gm
const appNames = new Set(['layout','template','page','loading','not-found','forbidden','unauthorized','error','global-error','route','default','robots','sitemap','manifest','icon','apple-icon','opengraph-image','twitter-image'])
const rootNames = new Set(['proxy','middleware','instrumentation','instrumentation-client'])
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name)
    if (ent.isDirectory()) walk(full, out)
    else if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(full)) out.push(full)
  }
  return out
}
function write(file, rows) {
  fs.writeFileSync(file, [...rows].sort().join('\n') + (rows.length ? '\n' : ''))
}
function resolveSpecifier(fromFile, spec) {
  if (spec.startsWith('@/')) spec = path.join('src', spec.slice(2))
  else if (spec.startsWith('.')) spec = path.normalize(path.join(path.dirname(fromFile), spec))
  else return 'external-or-package'
  const candidates = []
  for (const ext of exts) candidates.push(spec + ext)
  for (const ext of exts) candidates.push(path.join(spec, 'index' + ext))
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate
  return 'unresolved'
}
const allFiles = walk('src').concat(walk('tests')).sort()
const srcFiles = walk('src').filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.(ts|tsx)$/.test(f))
const testFiles = allFiles.filter((f) => /\.(test|spec)\.(ts|tsx)$/.test(f))
const vitestTests = testFiles.filter((f) =>
  (f.startsWith('src/') && f.endsWith('.test.ts')) ||
  /^tests\/unit\/stores\/.*\.test\.ts$/.test(f) ||
  /^tests\/unit\/lib\/.*\.test\.ts$/.test(f) ||
  /^tests\/unit\/components\/.*\.test\.tsx$/.test(f) ||
  /^tests\/unit\/hooks\/.*\.test\.ts$/.test(f)
)
const playwrightSpecs = testFiles.filter((f) => /^tests\/e2e\/.*\.spec\.ts$/.test(f))
const setupFiles = [
  'tests/setup/mock-fetch-sse.ts',
  'tests/setup/msw-handlers.ts',
  'tests/setup/msw-server.ts',
  'tests/setup/vitest.setup.ts',
  'tests/e2e/global-setup.ts',
  'tests/e2e/fixtures/auth.ts',
].filter((f) => fs.existsSync(f))
const supportOnly = setupFiles.concat(['tests/fixtures/index.ts', 'tests/utils/test-utils.tsx'].filter((f) => fs.existsSync(f)))
const nextRoots = []
for (const file of walk('src/app')) {
  const base = path.basename(file).replace(/\.(ts|tsx|js|jsx|mjs|cjs|ico|jpg|jpeg|png|svg)$/, '')
  if (appNames.has(base)) nextRoots.push(file)
}
for (const file of walk('src').filter((f) => path.dirname(f) === 'src')) {
  const base = path.basename(file).replace(/\.(ts|tsx|js|jsx|mjs|cjs)$/, '')
  if (rootNames.has(base)) nextRoots.push(file)
}
if (fs.existsSync('src/pages')) nextRoots.push(...walk('src/pages'))
write('/tmp/hybro-src-files.txt', srcFiles)
write('/tmp/hybro-test-files.txt', testFiles)
write('/tmp/hybro-vitest-included-tests.txt', vitestTests)
write('/tmp/hybro-playwright-included-specs.txt', playwrightSpecs)
write('/tmp/hybro-test-support-only-files.txt', supportOnly)
write('/tmp/hybro-next-entrypoints.txt', nextRoots)
const manifestRoots = new Set([...nextRoots, ...vitestTests, ...playwrightSpecs, ...setupFiles])
write('/tmp/hybro-manifest-roots.txt', [...manifestRoots])
const graph = []
const edges = new Map()
for (const file of allFiles) {
  const text = fs.readFileSync(file, 'utf8')
  for (const match of text.matchAll(importRe)) {
    const spec = match[1] || match[2] || match[3] || match[4] || match[5]
    const resolved = resolveSpecifier(file, spec)
    graph.push([file, spec, resolved].join('\t'))
    if (/^(src|tests)\//.test(resolved)) {
      if (!edges.has(file)) edges.set(file, [])
      edges.get(file).push(resolved)
    }
  }
}
write('/tmp/hybro-import-graph.tsv', graph)
const seen = new Set()
const queue = [...manifestRoots]
while (queue.length) {
  const file = queue.shift()
  if (seen.has(file)) continue
  seen.add(file)
  for (const next of edges.get(file) || []) if (!seen.has(next)) queue.push(next)
}
write('/tmp/hybro-live-closure.txt', [...seen])
const exports = []
for (const file of srcFiles) {
  const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true)
  for (const node of sf.statements) {
    const mods = node.modifiers?.map((m) => m.kind) || []
    if (mods.includes(ts.SyntaxKind.ExportKeyword)) {
      if (node.name?.text) exports.push([file, 'named export', node.name.text].join('\t'))
      else if (ts.isExportAssignment(node)) exports.push([file, 'default export', 'export assignment'].join('\t'))
      else if (ts.isVariableStatement(node)) for (const d of node.declarationList.declarations) if (d.name?.text) exports.push([file, 'named export', d.name.text].join('\t'))
      else exports.push([file, 'export declaration', node.getText(sf).slice(0, 80).replace(/\s+/g, ' ')].join('\t'))
    } else if (ts.isExportDeclaration(node)) {
      const moduleText = node.moduleSpecifier?.text || ''
      if (node.exportClause && ts.isNamedExports(node.exportClause)) {
        for (const e of node.exportClause.elements) exports.push([file, 're-export', `${e.name.text} from ${moduleText}`].join('\t'))
      } else exports.push([file, 'namespace/star re-export', moduleText].join('\t'))
    }
  }
}
write('/tmp/hybro-export-universe.tsv', exports)
console.log(`source ${srcFiles.length}`)
console.log(`tests ${testFiles.length}`)
console.log(`test support ${supportOnly.length}`)
console.log(`manifest roots ${manifestRoots.size}`)
console.log(`imports ${graph.length}`)
console.log(`live closure ${seen.size}`)
console.log(`exports ${exports.length}`)
NODE
```

The executed export script used this TypeScript AST style. Its output count was 918 rows; the compact rerun script above is included as a reproduction aid, while the persisted row-level export reconciliation below is the audit evidence used for the final classification.

Artifact row counts:

```text
252 /tmp/hybro-src-files.txt
101 /tmp/hybro-test-files.txt
8   /tmp/hybro-test-support-only-files.txt
918 /tmp/hybro-export-universe.tsv
348 /tmp/hybro-live-closure.txt
1723 /tmp/hybro-import-graph.tsv
133 /tmp/hybro-manifest-roots.txt
```

Artifact fingerprints:

```text
bb7badd1d25a847496e195aef83d32a869d31d0c61dffb655bbc0a1f3eb0b1e9  /tmp/hybro-src-files.txt
64d6e26a715f155b0133bf40f25ce6ce5b6649082dd07438a869c696a0ba196f  /tmp/hybro-test-files.txt
37d747f025f1bb476de38d2e92658645a17457d3ef8b79799b42e330ce191545  /tmp/hybro-test-support-only-files.txt
b930882a4253d76b49ebd21cde43a25176d6b47564cf23632c585fb51f5b3cb8  /tmp/hybro-export-universe.tsv
c94844df2e2b49775b9307a50dc5fca314521a99a5753cb56e538e10578174c5  /tmp/hybro-live-closure.txt
ad0d8a7393d12ec9a048e5ba2c981d804414a91ff3aa765bcb24754d9543b5e6  /tmp/hybro-import-graph.tsv
286dbf280f7f80a539faf6c3f059349a3e6c950940ac300e42f89f3c3ec34c4f  /tmp/hybro-manifest-roots.txt
b7937ac822ded8738b20c9e218b3b15b38f98fa0ef2558c1ee20baaf62f17a90  /tmp/hybro-vitest-included-tests.txt
c00947d3a493920302859fe46c2a129aa8be0b00f8f6bd973f4e9965b5b6d835  /tmp/hybro-playwright-included-specs.txt
```

Full non-live source reconciliation command:

```bash
comm -23 <(sort /tmp/hybro-src-files.txt) <(sort /tmp/hybro-live-closure.txt)
```

Output:

```text
src/components/agent-card.tsx
src/components/scroll-range-spacer.tsx
src/components/ui/alert.tsx
src/components/ui/radio-group.tsx
src/components/ui/scroll-area.tsx
src/components/ui/select.tsx
src/components/upgrade-button.tsx
src/hooks/room/index.ts
src/hooks/room/overlay-pending-hitl.ts
src/hooks/useAutoHideScroll.ts
src/hooks/useRoomMessages.ts
src/lib/agent-colors.ts
src/lib/types/memory.ts
```

Those 13 non-live source rows are reconciled above as 5 accepted findings and 8 exclusions. The remaining 239 source files are live because they appear in both `/tmp/hybro-src-files.txt` and `/tmp/hybro-live-closure.txt`. All 101 tests are roots from active Vitest or Playwright globs. All 8 test support files are roots or reachable from setup/fixture imports. All 918 export rows are classified as unsupported by the export analyzer for accepted findings.

The full row-level reconciliation is embedded below for all 252 source files and all 918 enumerated exports. The test/support section above embeds all 109 active test and support rows.

### Shared Exclusion Probe Checklist

The following probe was run across source, tests, and scoped config before accepting file-level findings:

```bash
rg -n "import\\(|registry|Record<|route\\(|barrel|generated|codegen|scroll-range-spacer|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|ScrollRangeSpacer|UpgradeButton|useAutoHideScroll|AGENT_COLOR_PALETTE|getAgentColorClasses|getAgentInitials" src tests package.json vitest.config.ts playwright.config.ts tsconfig.json eslint.config.mjs
```

Probe interpretation:

| Probe class | Result for accepted findings |
|-------------|------------------------------|
| Dynamic reachability | Dynamic `import()` exists elsewhere, but no dynamic import string references accepted finding paths or symbols. |
| Registry/string lookup | Registry/map-like code exists elsewhere, but no registry key or lookup references accepted finding paths or symbols. |
| Public API/barrel | No accepted finding path is re-exported by an active barrel; `overlay-pending-hitl.ts` is itself a deprecated re-export with no consumers. |
| Framework route handler | No accepted finding is under a Next route/layout/page/metadata/proxy convention path. |
| Generated/external | No accepted finding is marked generated; generated-looking `src/lib/types/memory.ts` is excluded. |
| Config-driven reachability | Package scripts, Vitest, Playwright, TypeScript, and ESLint config probes found no reference to accepted finding paths or symbols. |

Representative output lines for accepted finding names:

```text
src/lib/agent-colors.ts:9:export const AGENT_COLOR_PALETTE = [
src/lib/agent-colors.ts:84:export function getAgentColorClasses(agentId: string) {
src/lib/agent-colors.ts:92:export function getAgentInitials(agentName: string): string {
src/components/upgrade-button.tsx:13:export function UpgradeButton() {
src/hooks/useAutoHideScroll.ts:12:export function useAutoHideScroll(
src/hooks/room/overlay-pending-hitl.ts:2:export { overlayPendingHitlRequests } from '@/lib/room-sync/hitl-overlay'
src/components/scroll-range-spacer.tsx:9:export function ScrollRangeSpacer({ scrollContainerRef }: ScrollRangeSpacerProps) {
```

Per-finding exclusion probe matrix:

| Candidate | Dynamic import probe | Registry/string lookup probe | Public barrel probe | Framework/generated/config probe |
|-----------|----------------------|------------------------------|---------------------|----------------------------------|
| `src/components/scroll-range-spacer.tsx` | shared probe finds no `import()` path/symbol hit outside declaration | no registry/map key hit for `scroll-range-spacer` or `ScrollRangeSpacer` | no export-from hit outside file | not under convention path; no config hit |
| `src/components/upgrade-button.tsx` | shared probe finds no `import()` path/symbol hit outside declaration | no registry/map key hit for `upgrade-button` or `UpgradeButton` | no export-from hit outside file | not under convention path; no config hit |
| `src/hooks/room/overlay-pending-hitl.ts` | no dynamic import of compatibility path | active HITL map/registry hits use `@/lib/room-sync`; none import compatibility path | file is a re-export, but no import/export consumer of that path exists | not convention/generated/config referenced |
| `src/hooks/useAutoHideScroll.ts` | shared probe finds no `import()` path/symbol hit outside declaration | no registry/map key hit for `useAutoHideScroll` | no export-from hit outside file | not under convention path; no config hit |
| `src/lib/agent-colors.ts` | shared probe finds no `import()` path/symbol hit outside declaration | symbol hits are self-contained palette/hash usage only | no export-from hit outside file | not under convention path; no config hit |

Per-finding extended exclusion command:

```bash
for f in src/components/scroll-range-spacer.tsx src/components/upgrade-button.tsx src/hooks/room/overlay-pending-hitl.ts src/hooks/useAutoHideScroll.ts src/lib/agent-colors.ts; do
  echo "== $f"
  bn=$(basename "$f")
  stem=${bn%.*}
  rg -n "from ['\"].*${stem}|export .*from ['\"].*${stem}|import\\(['\"].*${stem}|${f}|${stem}" src tests package.json vitest.config.ts playwright.config.ts tsconfig.json eslint.config.mjs || true
done
```

Output:

```text
== src/components/scroll-range-spacer.tsx
== src/components/upgrade-button.tsx
== src/hooks/room/overlay-pending-hitl.ts
== src/hooks/useAutoHideScroll.ts
src/hooks/useAutoHideScroll.ts:12:export function useAutoHideScroll(
== src/lib/agent-colors.ts
```

The exact symbol probes in the individual findings cover declaration-name variants that this path/stem probe intentionally does not match. Together they show no public barrel, dynamic string import, package-script, tool-config, or framework convention consumer for the accepted finding paths.

### Full Source Reconciliation

| Item | Category | Outcome | Evidence reference | Notes |
|------|----------|---------|--------------------|-------|
| `src/app/(auth)/layout.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/(auth)/sign-in/[[...sign-in]]/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/(auth)/sign-up/[[...sign-up]]/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/about/about-cta-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/about/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/agents/[id]/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/agents/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/chat/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/hub/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/layout.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/pricing/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/c/room/[id]/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/agents/[id]/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/agents/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/discovery-api-keys/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/docs/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/hub/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/inspector/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/layout.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/d/register/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/layout.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/privacy/page.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/robots.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/app/sitemap.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/agent-card.tsx` | source file | excluded | non-live closure + exclusion reason | basename collision with active conversation/consumer agent card code |
| `src/components/agent-selector.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/agent-source-badge.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/artifact-list.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/artifact-renderer.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/attachment-preview.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/composer/ComposerShell.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/composer/HitlResponseBar.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/consumer-agent-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/consumer/consumer-footer.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/consumer/consumer-header.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/consumer/consumer-sidebar.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/AgentCard.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/AgentContentBlock.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/AgentIndex.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/AgentResponseDetailPane.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/AgentResultContent.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/ConversationMessageList.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/FinalAnswerSurface.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/ScrollToBottomButton.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/SynthesisContent.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/TurnBody.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/TurnRenderer.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/UserAnswerCard.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/UserAttachmentCard.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/UserMessageBlock.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/conversation/scroll-state.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/cookie-banner.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/developer-docs-content.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/developer/agent-avatar-upload.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/developer/agent-settings-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/developer/developer-header.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/developer/developer-sidebar.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/fade-in-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/file-attachment-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/framework-badges.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/group-management-modal.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/group-selector.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/hitl-compact-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/hitl-inline-reply-form.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/hitl-question-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/hub-page-content.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/icons.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/image-lightbox.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/inline-chips.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/inline-copy-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/logo.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/markdown-content.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/mode-selector.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-agent.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-discord-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-docs-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-hub.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-main.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/nav-user.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/part-renderer.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/providers/ClerkAuthProvider.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/providers/query-provider.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/require-auth.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/room-chat-input.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/room-default-agents-editor.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/room-page-shell.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/room-setting-form.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/scroll-range-spacer.tsx` | source file | accepted finding | non-live closure + per-finding probes | See Findings section |
| `src/components/settings/danger-zone-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/form-group.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/hub-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/loading-button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/password-input.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/password-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/profile-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/sessions-section.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/settings-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/settings-dialog-provider.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/settings/settings-dialog.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/theme-provider.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/theme-toggle.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/truncated-content.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/alert-dialog.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/alert.tsx` | source file | excluded | non-live closure + exclusion reason | generated/design-system UI primitive |
| `src/components/ui/avatar.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/badge.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/banner.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/breadcrumb.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/button.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/collapsible.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/dialog.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/dropdown-menu.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/form.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/hover-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/input.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/label.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/radio-group.tsx` | source file | excluded | non-live closure + exclusion reason | generated/design-system UI primitive |
| `src/components/ui/resizable.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/scroll-area.tsx` | source file | excluded | non-live closure + exclusion reason | generated/design-system UI primitive |
| `src/components/ui/select.tsx` | source file | excluded | non-live closure + exclusion reason | generated/design-system UI primitive |
| `src/components/ui/separator.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/sheet.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/sidebar.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/skeleton.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/sonner.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/switch.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/textarea.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/ui/tooltip.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/upgrade-button.tsx` | source file | accepted finding | non-live closure + per-finding probes | See Findings section |
| `src/components/use-case-card.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/components/video-embed.tsx` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/index.ts` | source file | excluded | non-live closure + exclusion reason | barrel/directory-index semantics require manual review |
| `src/hooks/room/overlay-pending-hitl.ts` | source file | accepted finding | non-live closure + per-finding probes | See Findings section |
| `src/hooks/room/processing-lifecycle.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/apply-commands.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/artifacts.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/correlation.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/dispatch.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/agent-response.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/artifact-update.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/hitl.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/misc.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/processing-status.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/task-submitted.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/task-update.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/handlers/user-message.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/sse-handlers/types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useAgentCatalog.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useProcessingRestore.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomActions.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomData.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomHydration.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomReset.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomSSEConnection.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useRoomWebhook.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/room/useSendMessage.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/use-mobile.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useAutoHideScroll.ts` | source file | accepted finding | non-live closure + per-finding probes | See Findings section |
| `src/hooks/useChatRoomCreation.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useGroupManagement.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useHubStatus.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useMessageScrollAnchoring.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useMyAgents.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/usePrimaryStreamScroll.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useRoomMessages.ts` | source file | excluded | non-live closure + exclusion reason | active comment/reference and migration context unclear |
| `src/hooks/useRoomSSE.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useRoomWebhook.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useStreamBuffer.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useTextSelectionQuote.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/hooks/useTurnViewModels.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/agent-avatar.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/agent-colors.ts` | source file | accepted finding | non-live closure + per-finding probes | See Findings section |
| `src/lib/agent-icon-utils.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api-client.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/a2a-tasks.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/agent-group.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/agent.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/discovery-api-keys.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/files.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/health.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/hitl.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/hub.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/inspection.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/room.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/sse.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/api/task.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/auth.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/clerk-error.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/consumer-nav.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/developer-nav.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/file-icon-utils.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/nav-items.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/presigned-url.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-sync/apply-db-messages.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-sync/hitl-overlay.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-sync/hydrate-room.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-sync/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-sync/types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/build-turns.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/derive-final-answer.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/event-log.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/map-result-display.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/message-groups.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/turn-agent-terminal.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/turn-live-shell.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/room-timeline/types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selection-plain-text.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/conversation-types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/map-agent-display.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/route-agent.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/select-agent-response-detail.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/select-composer-state.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/selectors/select-hitl.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/sidebar-styles.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/streaming/display.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/system-agents.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/time.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/agent-group.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/agent.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/attachments.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/chat-mode.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/error.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/health.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/memory.ts` | source file | excluded | non-live closure + exclusion reason | generated memory model overlap with active request/response types |
| `src/lib/types/quote.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/request.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/response.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/room.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/types/sse.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/urls.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/use-case-templates.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/lib/utils.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/proxy.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/convert-api-message.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/hydration-filter.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/infer-turn-terminal-status.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/resolve-display-type.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/stale-detection.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/types.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/message-store/upsert.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/room-ui-store.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |
| `src/stores/streaming-store/index.ts` | source file | live | /tmp/hybro-live-closure.txt | Reachable from manifest roots |

### Full Export Reconciliation

| Item | Category | Outcome | Evidence reference | Notes |
|------|----------|---------|--------------------|-------|
| `src/app/(auth)/layout.tsx :: default export :: AuthLayout` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/(auth)/sign-in/[[...sign-in]]/page.tsx :: default export :: Page` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/(auth)/sign-up/[[...sign-up]]/page.tsx :: default export :: Page` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/about/about-cta-button.tsx :: named export :: AboutCtaButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/about/page.tsx :: default export :: AboutPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/agents/[id]/page.tsx :: default export :: ConsumerAgentProfilePage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/agents/page.tsx :: default export :: ConsumerAgentsPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/chat/page.tsx :: default export :: ChatPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/hub/page.tsx :: default export :: ConsumerHubPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/layout.tsx :: default export :: ConsumerLayout` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/page.tsx :: default export :: ConsumerLandingPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/pricing/page.tsx :: default export :: PricingPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/c/room/[id]/page.tsx :: default export :: RoomChatPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/agents/[id]/page.tsx :: default export :: DeveloperAgentManagePage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/agents/page.tsx :: default export :: DeveloperAgentsPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/discovery-api-keys/page.tsx :: default export :: DeveloperApiKeysPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/docs/page.tsx :: default export :: DevelopersPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/hub/page.tsx :: default export :: DeveloperHubPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/inspector/page.tsx :: default export :: InspectorPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/layout.tsx :: default export :: DeveloperLayout` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/page.tsx :: default export :: DeveloperLandingPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/d/register/page.tsx :: default export :: RegisterAgentPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/layout.tsx :: default export :: RootLayout` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/privacy/page.tsx :: default export :: PrivacyPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/robots.ts :: default export :: robots` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/app/sitemap.ts :: default export :: sitemap` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/agent-card.tsx :: named export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/agent-card.tsx :: named export :: StatsCards` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/agent-selector.tsx :: named export :: AgentSelector` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/agent-source-badge.tsx :: named export :: AgentSourceBadge` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/artifact-list.tsx :: named export :: ArtifactList` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/artifact-renderer.tsx :: named export :: ArtifactRenderer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/attachment-preview.tsx :: named export :: AttachmentPreview` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/composer/ComposerShell.tsx :: named export :: ComposerShellAdapter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/composer/ComposerShell.tsx :: named export :: ComposerShell` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/composer/HitlResponseBar.tsx :: named export :: HitlPromptView` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/composer/HitlResponseBar.tsx :: named export :: HitlResponseBar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/consumer/consumer-footer.tsx :: named export :: ConsumerFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/consumer/consumer-sidebar.tsx :: named export :: ConsumerSidebar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/consumer-agent-card.tsx :: named export :: ConsumerAgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/consumer-agent-card.tsx :: named export :: ConsumerAgentCardSkeleton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentCard.tsx :: named export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentContentBlock.tsx :: named export :: AgentContentBlock` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentIndex.tsx :: named export :: AgentIndex` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentIndex.tsx :: named export :: shouldShowAgentIndex` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentResponseDetailPane.tsx :: named export :: AgentResponseDetailPane` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/AgentResultContent.tsx :: named export :: AgentResultContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/ConversationMessageList.tsx :: named export :: ConversationMessageList` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/FinalAnswerSurface.tsx :: named export :: FinalAnswerSurface` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/ScrollToBottomButton.tsx :: named export :: ScrollToBottomButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/SynthesisContent.tsx :: named export :: SynthesisContentBodyProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/SynthesisContent.tsx :: named export :: SynthesisContentBody` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/SynthesisContent.tsx :: named export :: SynthesisContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/SynthesisContent.tsx :: named export :: SynthesisContentFromStream` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/TurnBody.tsx :: named export :: TurnBody` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/TurnRenderer.tsx :: named export :: TurnRenderer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/UserAnswerCard.tsx :: named export :: UserAnswerCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/UserAttachmentCard.tsx :: named export :: UserAttachmentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/UserMessageBlock.tsx :: named export :: UserMessageBlock` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/scroll-state.ts :: named export :: ResolveScrollStateInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/scroll-state.ts :: named export :: ResolveScrollStateOutput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/conversation/scroll-state.ts :: named export :: resolveScrollStateAfterEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/cookie-banner.tsx :: named export :: CookieBanner` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/agent-avatar-upload.tsx :: named export :: AgentAvatarUpload` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/agent-settings-card.tsx :: named export :: AgentSettingsValues` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/agent-settings-card.tsx :: named export :: AgentSettingsCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/agent-settings-card.tsx :: named export :: validateAgentSettings` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/agent-settings-card.tsx :: named export :: settingsToUpdatePayload` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer/developer-sidebar.tsx :: named export :: DeveloperSidebar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/developer-docs-content.tsx :: named export :: DeveloperDocsContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/fade-in-section.tsx :: named export :: FadeInSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/file-attachment-button.tsx :: named re-export :: ACCEPTED_MIME_SET` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/file-attachment-button.tsx :: named re-export :: MAX_FILE_SIZE` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/file-attachment-button.tsx :: named re-export :: MAX_ATTACHMENTS` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/file-attachment-button.tsx :: named export :: FileAttachmentButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/framework-badges.tsx :: named export :: FrameworkBadges` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/group-management-modal.tsx :: named export :: GroupManagementModal` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/group-selector.tsx :: named export :: GroupSelector` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/hitl-compact-card.tsx :: named export :: HitlCompactCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/hitl-inline-reply-form.tsx :: named export :: HitlPanelProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/hitl-inline-reply-form.tsx :: named export :: HitlPanel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/hitl-question-card.tsx :: named export :: HitlQuestionCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/hub-page-content.tsx :: named export :: HubPageContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: GithubIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: DiscordIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: YoutubeIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: CrewAIIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: LangGraphIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: LangChainIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: N8nIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: OllamaIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/icons.tsx :: named export :: OpenClawIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/image-lightbox.tsx :: named export :: ImageLightbox` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/inline-chips.tsx :: named export :: InlineChips` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/inline-copy-button.tsx :: named export :: InlineCopyButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/logo.tsx :: named export :: Logo` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/markdown-content.tsx :: named export :: copySelectionWithMentions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/markdown-content.tsx :: named export :: MarkdownContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/markdown-content.tsx :: named export :: LinkifiedContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/mode-selector.tsx :: named export :: ModeSelector` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-agent.tsx :: named export :: NavAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-discord-button.tsx :: named export :: DiscordButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-docs-button.tsx :: named export :: DocsButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-hub.tsx :: named export :: NavHub` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-main.tsx :: named export :: NavMain` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/nav-user.tsx :: named export :: NavUser` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/part-renderer.tsx :: named export :: CollapsibleJsonBlock` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/part-renderer.tsx :: named export :: PartRenderer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/providers/ClerkAuthProvider.tsx :: named export :: ClerkAuthProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/providers/query-provider.tsx :: named export :: QueryProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/require-auth.tsx :: named export :: RequireAuth` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-chat-input.tsx :: named export :: RoomChatInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-default-agents-editor.tsx :: named export :: RoomDefaultAgentsEditor` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-page-shell.tsx :: named export :: GroupManagementAdapter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-page-shell.tsx :: named export :: QuoteState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-page-shell.tsx :: named export :: TimelineAdapter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-page-shell.tsx :: named export :: RoomPageShell` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-setting-form.tsx :: named export :: RoomModeOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/room-setting-form.tsx :: named export :: RoomSettingFormHandle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/scroll-range-spacer.tsx :: named export :: ScrollRangeSpacer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/danger-zone-section.tsx :: named export :: DangerZoneSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/form-group.tsx :: named export :: FormGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/hub-section.tsx :: named export :: HubSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/loading-button.tsx :: named export :: LoadingButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/password-input.tsx :: named export :: PasswordInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/password-section.tsx :: named export :: PasswordSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/profile-section.tsx :: named export :: ProfileSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/sessions-section.tsx :: named export :: SessionsSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/settings-card.tsx :: named export :: SettingsCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/settings-dialog-provider.tsx :: named export :: useSettingsDialog` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/settings-dialog-provider.tsx :: named export :: SettingsDialogProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/settings/settings-dialog.tsx :: named export :: SettingsDialog` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/theme-provider.tsx :: named export :: ThemeProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/theme-toggle.tsx :: named export :: ThemeToggle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/truncated-content.tsx :: named export :: TruncatedContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialog` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogAction` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogCancel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogHeader` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogOverlay` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogPortal` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogTitle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert-dialog.tsx :: named re-export :: AlertDialogTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert.tsx :: named re-export :: Alert` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert.tsx :: named re-export :: AlertTitle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/alert.tsx :: named re-export :: AlertDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/avatar.tsx :: named re-export :: Avatar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/avatar.tsx :: named re-export :: AvatarImage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/avatar.tsx :: named re-export :: AvatarFallback` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/badge.tsx :: named re-export :: Badge` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/badge.tsx :: named re-export :: badgeVariants` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/banner.tsx :: named export :: BannerHost` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: Breadcrumb` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbList` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbLink` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbPage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbSeparator` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/breadcrumb.tsx :: named re-export :: BreadcrumbEllipsis` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/button.tsx :: named re-export :: Button` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/button.tsx :: named re-export :: buttonVariants` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: Card` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardHeader` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardTitle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardAction` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/card.tsx :: named re-export :: CardContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/collapsible.tsx :: named re-export :: Collapsible` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/collapsible.tsx :: named re-export :: CollapsibleTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/collapsible.tsx :: named re-export :: CollapsibleContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: Dialog` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogClose` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogHeader` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogOverlay` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogPortal` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogTitle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dialog.tsx :: named re-export :: DialogTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenu` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuPortal` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuCheckboxItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuRadioGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuRadioItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuSeparator` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuShortcut` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuSub` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuSubTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/dropdown-menu.tsx :: named re-export :: DropdownMenuSubContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: useFormField` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: Form` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormControl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/form.tsx :: named re-export :: FormField` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/hover-card.tsx :: named re-export :: HoverCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/hover-card.tsx :: named re-export :: HoverCardTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/hover-card.tsx :: named re-export :: HoverCardContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/input.tsx :: named re-export :: Input` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/label.tsx :: named re-export :: Label` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/radio-group.tsx :: named re-export :: RadioGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/radio-group.tsx :: named re-export :: RadioGroupItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/resizable.tsx :: named re-export :: ResizableHandle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/resizable.tsx :: named re-export :: ResizablePanel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/resizable.tsx :: named re-export :: ResizablePanelGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/scroll-area.tsx :: named re-export :: ScrollArea` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/scroll-area.tsx :: named re-export :: ScrollBar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: Select` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectScrollDownButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectScrollUpButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectSeparator` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/select.tsx :: named re-export :: SelectValue` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/separator.tsx :: named re-export :: Separator` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: Sheet` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetClose` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetHeader` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetTitle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sheet.tsx :: named re-export :: SheetDescription` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: Sidebar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarFooter` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarGroupAction` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarGroupContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarGroupLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarHeader` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarInset` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenu` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuAction` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuBadge` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuSkeleton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuSub` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuSubButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarMenuSubItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarRail` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarSeparator` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: SidebarTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sidebar.tsx :: named re-export :: useSidebar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/skeleton.tsx :: named re-export :: Skeleton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/sonner.tsx :: named re-export :: Toaster` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/switch.tsx :: named re-export :: Switch` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/textarea.tsx :: named re-export :: Textarea` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/tooltip.tsx :: named re-export :: Tooltip` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/tooltip.tsx :: named re-export :: TooltipTrigger` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/tooltip.tsx :: named re-export :: TooltipContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/ui/tooltip.tsx :: named re-export :: TooltipProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/upgrade-button.tsx :: named export :: UpgradeButton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/use-case-card.tsx :: named export :: UseCaseCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/components/video-embed.tsx :: named export :: VideoEmbed` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/index.ts :: named re-export :: useRoomWebhook` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/overlay-pending-hitl.ts :: named re-export :: overlayPendingHitlRequests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/processing-lifecycle.ts :: named export :: ProcessingLifecycle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/processing-lifecycle.ts :: named export :: createProcessingLifecycle` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/apply-commands.ts :: named export :: RoomCommand` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/apply-commands.ts :: named export :: applyRoomCommands` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/artifacts.ts :: named export :: isRenderableArtifactPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/artifacts.ts :: named export :: partsToArtifacts` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/artifacts.ts :: named export :: sseArtifactDataFromPayload` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/correlation.ts :: named export :: CorrelationResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/correlation.ts :: named export :: resolveSseCorrelation` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/correlation.ts :: named export :: bufferCorrelatedEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/dispatch.ts :: named export :: createSSEDispatcher` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/agent-response.ts :: named export :: handleAgentResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/artifact-update.ts :: named export :: ArtifactUpdateContext` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/artifact-update.ts :: named export :: handleArtifactUpdate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/hitl.ts :: named export :: handleHitlInputRequested` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/hitl.ts :: named export :: handleHitlStatusUpdate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/misc.ts :: named export :: handleError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/misc.ts :: named export :: handleHeartbeat` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/misc.ts :: named export :: handleTurnEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/misc.ts :: named export :: handleRunEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/processing-status.ts :: named export :: handleProcessingStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/task-submitted.ts :: named export :: handleTaskSubmitted` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/task-update.ts :: named export :: handleTaskUpdate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/handlers/user-message.ts :: named export :: handleUserMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/index.ts :: named re-export :: createSSEDispatcher` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/index.ts :: type re-export :: SSEHandlerDeps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/index.ts :: named re-export :: applyRoomCommands` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/index.ts :: type re-export :: RoomCommand` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: getResolvedMessageId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: resolveClientRequestMessageId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: clearPendingSseForClientRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: enqueuePendingSseEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: resetPendingTurnBufferForTests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts :: named export :: flushPendingSseEvents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/sse-handlers/types.ts :: named export :: SSEHandlerDeps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/types.ts :: named export :: UseRoomWebhookProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useAgentCatalog.ts :: named export :: useAgentCatalog` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useProcessingRestore.ts :: named export :: useProcessingRestore` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomActions.ts :: named export :: useRoomActions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomData.ts :: named export :: RoomWithActiveRuns` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomData.ts :: named export :: useRoomData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomHydration.ts :: named export :: useRoomHydration` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomReset.ts :: named export :: useRoomReset` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomSSEConnection.ts :: named export :: useRoomSSEConnection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useRoomWebhook.ts :: named export :: useRoomWebhook` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/room/useSendMessage.ts :: named export :: useSendMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/use-mobile.ts :: named export :: useIsMobile` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useAutoHideScroll.ts :: named export :: useAutoHideScroll` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useChatRoomCreation.ts :: named export :: useChatRoomCreation` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useGroupManagement.ts :: named export :: useGroupManagement` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useHubStatus.ts :: named export :: useHubStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useMessageScrollAnchoring.ts :: named export :: ScrollAnchoringInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useMessageScrollAnchoring.ts :: named export :: useMessageScrollAnchoring` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useMyAgents.ts :: named export :: useMyAgents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/usePrimaryStreamScroll.ts :: named export :: usePrimaryStreamScroll` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useOrderedIds` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useOrderedMessages` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useMessageCount` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useMessagesHydrated` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useActiveHitlRequests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomMessages.ts :: named export :: useMessageStoreRoomId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomSSE.ts :: named export :: useRoomSSE` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useRoomWebhook.ts :: named re-export :: useRoomWebhook` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useStreamBuffer.ts :: named export :: useStreamBuffer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useStreamBuffer.ts :: named export :: ResultStreamDisplay` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useStreamBuffer.ts :: named export :: useResultStreamDisplay` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useTextSelectionQuote.ts :: named export :: useTextSelectionQuote` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/hooks/useTurnViewModels.ts :: named export :: useTurnViewModels` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-avatar.ts :: named export :: getAgentAvatarUri` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-colors.ts :: named export :: getAgentColorClasses` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-colors.ts :: named export :: getAgentInitials` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-icon-utils.ts :: named export :: getModeIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-icon-utils.ts :: named export :: deduplicateIcons` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/agent-icon-utils.ts :: named export :: getModeLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: A2ATaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: A2ATaskListItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: GetTaskStatusResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: ListTasksResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: getTaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: listRoomTasks` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: listUserPendingTasks` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: extractTaskContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/a2a-tasks.ts :: named export :: extractTaskError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent-group.ts :: named export :: createAgentGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent-group.ts :: named export :: listAgentGroups` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent-group.ts :: named export :: getAgentGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent-group.ts :: named export :: updateAgentGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent-group.ts :: named export :: deleteAgentGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: registerAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAgentsByProviderId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: UpdateAgentRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: updateAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: uploadAgentAvatar` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: deleteAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAgentCardFromUrl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAllAgents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAllActiveAgents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/agent.ts :: named export :: getAgentListWithConditions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/discovery-api-keys.ts :: named export :: listApiKeys` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/discovery-api-keys.ts :: named export :: createApiKey` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/discovery-api-keys.ts :: named export :: deleteApiKey` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/files.ts :: named export :: FileUploadResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/files.ts :: named export :: uploadFile` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/health.ts :: named export :: healthCheck` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hitl.ts :: named export :: HitlPendingRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hitl.ts :: named export :: HitlRespondResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hitl.ts :: named export :: HitlPendingResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hitl.ts :: named export :: respondToHitl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hitl.ts :: named export :: fetchPendingHitlRequests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hub.ts :: named export :: HubStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hub.ts :: named export :: HubStatusResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/hub.ts :: named export :: getMyHubStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./agent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./task` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./inspection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./health` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./sse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./room` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./discovery-api-keys` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/index.ts :: namespace re-export :: ./hitl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/inspection.ts :: named export :: inspectAgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/inspection.ts :: named export :: inspectA2AConnection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: CreateRoomParams` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: createNewRoom` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: inquiryRoomSetting` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: inquiryActiveRuns` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: inquiryRoomsByRoomOwnerId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: updateRoomAgentSet` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: updateRoomName` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: updateRoomExtendInfo` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: inquiryRoomMessagesByRoomId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: SendMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: SuggestAgentsResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/room.ts :: named export :: suggestAgents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: type re-export :: SSEMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: named export :: SSECloseReason` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: named export :: SSEConnectionOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: named export :: SSEConnection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: named export :: getSSEStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/sse.ts :: named export :: cancelMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/task.ts :: named export :: queryTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/task.ts :: named export :: queryBaseTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/task.ts :: named export :: getAllSessions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/task.ts :: named export :: getBaseTasksBySessionId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api/task.ts :: named export :: getMetaTasksByParentId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: ApiError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: apiClient` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: apiGet` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: apiPost` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: apiPut` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/api-client.ts :: named export :: apiDelete` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/auth.ts :: named export :: setDefaultGetToken` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/auth.ts :: named export :: getClientAuthHeaders` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/clerk-error.ts :: named export :: getClerkErrorMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/file-icon-utils.ts :: named export :: getFileIcon` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/nav-items.ts :: named export :: NavAgentItem` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/presigned-url.ts :: named export :: isPresignedUrlExpired` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/apply-db-messages.ts :: named export :: ApplyDbMessagesResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/apply-db-messages.ts :: named export :: applyDbMessages` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/hitl-overlay.ts :: named export :: overlayPendingHitlRequests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/hitl-overlay.ts :: named export :: markResolvedHitlFromHydrationBatch` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/hitl-overlay.ts :: named export :: OverlayHitlOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/hitl-overlay.ts :: named export :: overlayHitlForRoom` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/hydrate-room.ts :: named export :: hydrateRoomFromDb` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: type re-export :: HydrateRoomOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: type re-export :: HydrateRoomPhase` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: type re-export :: HydrateRoomResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: named re-export :: applyDbMessages` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: type re-export :: ApplyDbMessagesResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: named re-export :: overlayPendingHitlRequests` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: named re-export :: overlayHitlForRoom` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: named re-export :: markResolvedHitlFromHydrationBatch` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/index.ts :: named re-export :: hydrateRoomFromDb` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/types.ts :: named export :: HydrateRoomPhase` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/types.ts :: named export :: HydrateRoomAgentResolver` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/types.ts :: named export :: HydrateRoomOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-sync/types.ts :: named export :: HydrateRoomResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/build-turns.ts :: named export :: buildTurns` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/build-turns.ts :: named export :: deriveTurnPhase` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/build-turns.ts :: named export :: selectSummary` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/build-turns.ts :: named export :: buildTurnsIncremental` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: buildDeterministicIntro` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: isFailedMultiAgentTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: isCanceledMultiAgentTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: deriveFinalAnswer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: deriveDisplayModeFromFinalAnswer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: derivePrimaryStreamFromFinalAnswer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named re-export :: isSupervisorClarifyAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/derive-final-answer.ts :: named export :: parseSummaryOrigin` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/event-log.ts :: named export :: appendEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/event-log.ts :: named export :: getEvents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/event-log.ts :: named export :: clearRoom` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/event-log.ts :: named export :: resetEventStore` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/map-result-display.ts :: named export :: mapResultDisplayProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/message-groups.ts :: named export :: MessageTurnGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/message-groups.ts :: named export :: groupMessagesByUserTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/message-groups.ts :: named export :: escapeCssIdent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-agent-terminal.ts :: named export :: allAgentsTerminalForUserMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: getActivityStripListMaxHeight` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: getStripSourceResults` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: getAgentIndexSummary` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: getSupervisorStatusLine` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: getCollectingProgressLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/turn-live-shell.ts :: named export :: defaultAgentIndexOpen` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TurnStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TurnDisplayMode` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TurnPhase` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TurnViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerKind` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: SummaryOrigin` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerLabel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerSection` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerHitlPrompt` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerHitlViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: FinalAnswerViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TimelineEventKind` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TimelineEventViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: TurnSummaryViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: AgentResultViewModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/room-timeline/types.ts :: named export :: RawTimelineEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selection-plain-text.ts :: named export :: getPlainTextFromRange` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selection-plain-text.ts :: named export :: getSelectionPlainText` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: AgentTheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: getAgentTheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: AgentDisplayProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: ConversationBlock` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: ConversationTurnView` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: PendingHitl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: HitlState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: ComposerState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: ContentView` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/conversation-types.ts :: named export :: AgentResponseDetail` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: namespace re-export :: ./conversation-types` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: routeAgentToTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: mapAgentDisplayProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: selectPendingHitls` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: selectAgentHitlState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: selectComposerState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/index.ts :: named re-export :: selectAgentResponseDetail` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/map-agent-display.ts :: named export :: mapAgentDisplayProps` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/route-agent.ts :: named export :: ClientRequestUserMessageIndex` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/route-agent.ts :: named export :: buildClientRequestUserMessageIndex` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/route-agent.ts :: named export :: routeAgentToTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/select-agent-response-detail.ts :: named export :: selectAgentResponseDetail` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/select-composer-state.ts :: named export :: selectComposerState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/select-hitl.ts :: named export :: selectPendingHitls` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/selectors/select-hitl.ts :: named export :: selectAgentHitlState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: isBufferStreaming` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: resolveViewModelStreaming` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: resolveEntityStreaming` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: resolveStreamText` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: resolveStreamArtifacts` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/streaming/display.ts :: named export :: resolveDetailArtifacts` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: SystemAgentInfo` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: isSystemAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: isSupervisorSystemAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: isSummarySystemAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: isSupervisorClarifyAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/system-agents.ts :: named export :: getSystemAgentName` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: normalizeTimestamp` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: normalizeTimestampOrNow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: parseTimestamp` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: utcNow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: elapsedSeconds` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: formatElapsedTime` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: isStale` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/time.ts :: named export :: formatTimestamp` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentAvailability` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomMembershipState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomMembershipSeedInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: MembershipOrigin` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: MembershipOriginStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: MessageTargetMode` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomDefaultStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomAgentRef` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: StaleAgentRef` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomMembershipReadModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: SavedGroupReadModel` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: RoomMembershipWriteInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: TargetModeDispatchInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: MentionDispatchInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: MessageDispatchInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: ScopeResolutionError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: DispatchAcceptedResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: DispatchRejectedResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: DispatchResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentExecutionResult` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentGroupCreateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentGroupUpdateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentGroupResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: AgentGroupListResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: isBuiltinGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: getGroupDisplayName` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent-group.ts :: named export :: normalizeLegacyTargetGroup` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AgentCapabilities` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AgentExtension` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AgentProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AgentSkill` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: APIKeySecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: HTTPAuthSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: OAuth2SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: OpenIdConnectSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: OAuthFlows` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: AuthorizationCodeOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: ClientCredentialsOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: ImplicitOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: type re-export :: PasswordOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: named export :: In` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: named export :: AgentStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/agent.ts :: named export :: Agent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/attachments.ts :: named export :: AttachmentStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/attachments.ts :: named export :: PendingAttachment` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/attachments.ts :: named export :: AttachmentData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/chat-mode.ts :: named export :: ChatMode` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/chat-mode.ts :: named export :: chatModeToFlags` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/chat-mode.ts :: named export :: flagsToChatMode` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: In` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AgentCapabilities` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AgentExtension` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AgentProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: APIKeySecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: HTTPAuthSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: OAuth2SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: OAuthFlows` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AuthorizationCodeOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: ClientCredentialsOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: ImplicitOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: PasswordOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: OpenIdConnectSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: AgentSkill` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: Error` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/error.ts :: named export :: ErrorResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/health.ts :: named export :: HealthCheckResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/health.ts :: named export :: ServiceHealth` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Agent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentSkill` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentCapabilities` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Role` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Part` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Message` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Task` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskSession` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: Artifact` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TextPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: FilePart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: DataPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeySecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: HTTPAuthSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: OAuth2SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: OpenIdConnectSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: In` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: MetaTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: BaseTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: InspectionCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: UserInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: ChatMemoryRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeyCreateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: AgentCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeyCreateResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeyItemResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeyListResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: APIKeyOperationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: InspectionCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: InsepectionCenterConnectionValidationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: OrchestrationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: TaskResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: UserResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: type re-export :: ChatMemoryResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: namespace re-export :: ./error` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: namespace re-export :: ./health` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: named export :: MessageType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: named export :: MessageData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/index.ts :: named export :: ApiResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/memory.ts :: named export :: ChatContext` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/memory.ts :: named export :: ContextData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/memory.ts :: named export :: ConversationTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/memory.ts :: named export :: MemoryContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/memory.ts :: named export :: RoomMemory` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/quote.ts :: named export :: QuoteSourceKind` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/quote.ts :: named export :: QuoteData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/quote.ts :: named export :: RoomQuoteWire` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: TaskState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: Part` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: TextPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: FilePart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: DataPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: FileWithBytes` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: FileWithUri` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: Message` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: Task` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: TaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: Artifact` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentCapabilities` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentExtension` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentInterface` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentSkill` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AgentCardSignature` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: APIKeySecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: HTTPAuthSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: OAuth2SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: OpenIdConnectSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: MutualTLSSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: OAuthFlows` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: AuthorizationCodeOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: ClientCredentialsOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: ImplicitOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: type re-export :: PasswordOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: In` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: Role` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: Agent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: APIKeyCreateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentCreate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentGroupCreateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentGroupRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentGroupUpdateRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentPatch` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentTaskRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: AgentUpdate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: BaseAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: BaseTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: ChatContext` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: ContextData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: ChatMemoryRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: ChatRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: DebatationCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: FilterParams` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: InspectionCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: MetaTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: OrchestrationRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: PaginationParams` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: Room` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomAgentMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: MessageContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomCenterAgentMessageRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomCenterMemoryRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomMemory` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: MemoryContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: ConversationTurn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomCenterRoomMessageRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomCenterRoomSettingRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomCenterUserMessageRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: RoomUserMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: TaskCenterRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: TaskSession` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: TaskRequest` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/request.ts :: named export :: UserInput` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: Part` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TextPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: FilePart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: DataPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: FileWithBytes` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: FileWithUri` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: Message` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: Task` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: Artifact` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentCard` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentCapabilities` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentExtension` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentInterface` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentProvider` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentSkill` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AgentCardSignature` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: APIKeySecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: HTTPAuthSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: OAuth2SecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: OpenIdConnectSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: MutualTLSSecurityScheme` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: OAuthFlows` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: AuthorizationCodeOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: ClientCredentialsOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: ImplicitOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: PasswordOAuthFlow` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: JSONRPCErrorResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: JSONRPCError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: JSONParseError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: InvalidRequestError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: MethodNotFoundError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: InvalidParamsError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: InternalError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskNotFoundError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskNotCancelableError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: PushNotificationNotSupportedError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: UnsupportedOperationError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: ContentTypeNotSupportedError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: InvalidAgentResponseError` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskStatusUpdateEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: TaskArtifactUpdateEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: SendMessageResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: SendStreamingMessageResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: SendMessageSuccessResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: type re-export :: SendStreamingMessageSuccessResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: In` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: Role` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: AgentStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: Agent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: AgentCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: APIKeyItemResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: APIKeyListResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: APIKeyCreateResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: APIKeyOperationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: BaseTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ChatContext` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ContextData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ChatMemoryResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ChatResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: DebatationCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: InsepectionCenterConnectionValidationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: InspectionCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: MetaTask` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: OrchestrationResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomAgentMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: MessageContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: Room` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterAgentMessageResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterMemoryResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomMemory` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: MemoryContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterRoomMessageResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterRoomSettingResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ActiveRunRefWire` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterActiveRunsResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomAgentRefWire` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: ScopeResolutionErrorWire` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomCenterUserMessageResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: RoomUserMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: Step` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: TaskCenterResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: TaskSession` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: TaskResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/response.ts :: named export :: UserResponse` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: Task` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: TaskStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: Part` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: TextPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: FilePart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: DataPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: FileWithBytes` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: FileWithUri` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: TaskState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: type re-export :: A2AMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: Role` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: Message` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: MessageContent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: Room` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: RoomAgentMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: RoomMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/room.ts :: named export :: RoomUserMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: type re-export :: TaskState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: SSEMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: SSEConnectionStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: isTerminalState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: isFailureState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: isInteractiveState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: isPendingState` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: TaskSubmittedEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: TaskUpdateEvent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: HITLPromptType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: HITLStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: ProcessingStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/types/sse.ts :: named export :: isProcessingDone` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/urls.ts :: named export :: consumerUrl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/urls.ts :: named export :: developerUrl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/use-case-templates.ts :: named export :: UseCaseAgent` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/use-case-templates.ts :: named export :: UseCaseTemplate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/use-case-templates.ts :: named export :: resolveTemplateAgents` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: cn` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: getApiUrl` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: isWaitlistEnabled` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: getInspectionTimeoutMs` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: tryParseJson` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/lib/utils.ts :: named export :: formatIfJson` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/proxy.ts :: named re-export :: isDeveloperHost` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/proxy.ts :: named re-export :: isSharedPath` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/proxy.ts :: named re-export :: isStaticFile` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/proxy.ts :: named re-export :: handleSubdomainRewrite` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/proxy.ts :: default export :: proxy` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/convert-api-message.ts :: named export :: ConvertApiMessageOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/convert-api-message.ts :: named export :: convertApiMessageToIncoming` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/hydration-filter.ts :: named export :: filterHydrationMessages` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: MessageEntity` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: IncomingMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: MessageSource` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: DisplayType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: resolveDisplayType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: detectAndMarkStaleTasks` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: filterHydrationMessages` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: convertApiMessageToIncoming` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: ConvertApiMessageOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: stampInferredTurnTerminalStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: named re-export :: collectActiveRunTriggerMessageIds` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/index.ts :: type re-export :: StampInferredTurnTerminalOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/infer-turn-terminal-status.ts :: named export :: StampInferredTurnTerminalOptions` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/infer-turn-terminal-status.ts :: named export :: stampInferredTurnTerminalStatus` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/infer-turn-terminal-status.ts :: named export :: collectActiveRunTriggerMessageIds` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/resolve-display-type.ts :: named export :: resolveDisplayType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/stale-detection.ts :: named export :: detectAndMarkStaleTasks` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: ArtifactPart` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: ArtifactData` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: MessageSource` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: DisplayType` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: MessageEntity` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/types.ts :: named export :: IncomingMessage` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/upsert.ts :: named export :: applyUpsert` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/upsert.ts :: named export :: isNoOpUpdate` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/upsert.ts :: named export :: buildSortedIds` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/upsert.ts :: named export :: mergeArtifacts` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/message-store/upsert.ts :: named export :: extractTextFromArtifacts` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: PendingTurnSkeleton` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: RoomFlags` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useRoomProcessing` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useRoomSending` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useRoomCancelling` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useRoomUpdating` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useRoomSseEnabled` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useLocalSendSeq` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useInitialHydrationSeq` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/room-ui-store.ts :: named export :: useSelectedAgentMessageId` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |
| `src/stores/streaming-store/index.ts :: named export :: StreamBuffer` | export | unsupported by analyzer | TypeScript AST export enumeration | Export-level findings disabled; namespace-precise reference pass not calibrated |

## Findings

### `src/components/scroll-range-spacer.tsx`

- Type: file-level dead code
- Risk: Low
- Entrypoint basis: Next/Vitest/Playwright/package-script manifest and live closure
- Positive live probes checked: absent from `/tmp/hybro-live-closure.txt`
- Negative reference command/output: exact `ScrollRangeSpacer` and import-specifier searches return only the declaration
- Calibration modes relied on: alias, extensionless import, manifest roots
- Exclusion probes: no dynamic import, registry, public barrel, route handler, generated-file, or config-driven reference found
- Notes: component is isolated and has no live import path.

Reference probe:

```bash
rg -n "ScrollRangeSpacer|scroll-range-spacer" src tests
```

Output:

```text
src/components/scroll-range-spacer.tsx:5:interface ScrollRangeSpacerProps {
src/components/scroll-range-spacer.tsx:9:export function ScrollRangeSpacer({ scrollContainerRef }: ScrollRangeSpacerProps) {
```

Live-closure absence probe:

```bash
rg -n "^src/components/scroll-range-spacer\\.tsx$" /tmp/hybro-live-closure.txt
```

Output: no rows, exit 1.

### `src/components/upgrade-button.tsx`

- Type: file-level dead code
- Risk: Low
- Entrypoint basis: Next/Vitest/Playwright/package-script manifest and live closure
- Positive live probes checked: absent from `/tmp/hybro-live-closure.txt`
- Negative reference command/output: exact `UpgradeButton` and import-specifier searches return only the declaration
- Calibration modes relied on: alias, extensionless import, manifest roots
- Exclusion probes: no dynamic import, registry, public barrel, route handler, generated-file, or config-driven reference found
- Notes: component is isolated and not imported by sidebar/navigation code.

Reference probe:

```bash
rg -n "UpgradeButton|upgrade-button" src tests
```

Output:

```text
src/components/upgrade-button.tsx:13:export function UpgradeButton() {
```

Live-closure absence probe:

```bash
rg -n "^src/components/upgrade-button\\.tsx$" /tmp/hybro-live-closure.txt
```

Output: no rows, exit 1.

### `src/hooks/room/overlay-pending-hitl.ts`

- Type: file-level dead code
- Risk: Low
- Entrypoint basis: Next/Vitest/Playwright/package-script manifest and live closure
- Positive live probes checked: absent from `/tmp/hybro-live-closure.txt`
- Negative reference command/output: exact path search returns only its own deprecated re-export; active code imports from `@/lib/room-sync`
- Calibration modes relied on: alias, extensionless import, barrel tracing
- Exclusion probes: no dynamic import, registry, public barrel, route handler, generated-file, or config-driven reference found
- Notes: deprecated compatibility re-export has no remaining consumers.

Reference probe:

```bash
rg -n "overlay-pending-hitl|useOverlayPendingHitl|overlayPendingHitl|pendingHitl" src tests
```

Output excerpt:

```text
src/lib/room-sync/hitl-overlay.ts:9:export async function overlayPendingHitlRequests(
src/lib/room-sync/hitl-overlay.ts:109:    pendingMessageIds = await overlayPendingHitlRequests(roomId, hitlRes.requests, {
src/lib/room-sync/index.ts:5:  overlayPendingHitlRequests,
src/hooks/room/overlay-pending-hitl.ts:2:export { overlayPendingHitlRequests } from '@/lib/room-sync/hitl-overlay'
```

The search finds active `@/lib/room-sync` consumers and the compatibility re-export itself, but no import of `src/hooks/room/overlay-pending-hitl.ts`.

Live-closure absence probe:

```bash
rg -n "^src/hooks/room/overlay-pending-hitl\\.ts$" /tmp/hybro-live-closure.txt
```

Output: no rows, exit 1.

### `src/hooks/useAutoHideScroll.ts`

- Type: file-level dead code
- Risk: Low
- Entrypoint basis: Next/Vitest/Playwright/package-script manifest and live closure
- Positive live probes checked: absent from `/tmp/hybro-live-closure.txt`
- Negative reference command/output: exact `useAutoHideScroll` and import-specifier searches return only the declaration
- Calibration modes relied on: alias, extensionless import, manifest roots
- Exclusion probes: no dynamic import, registry, public barrel, route handler, generated-file, or config-driven reference found
- Notes: hook has no live consumers.

Reference probe:

```bash
rg -n "useAutoHideScroll|auto-hide-scroll|AutoHideScroll" src tests
```

Output:

```text
src/hooks/useAutoHideScroll.ts:12:export function useAutoHideScroll(
```

Live-closure absence probe:

```bash
rg -n "^src/hooks/useAutoHideScroll\\.ts$" /tmp/hybro-live-closure.txt
```

Output: no rows, exit 1.

### `src/lib/agent-colors.ts`

- Type: file-level dead code
- Risk: Low
- Entrypoint basis: Next/Vitest/Playwright/package-script manifest and live closure
- Positive live probes checked: absent from `/tmp/hybro-live-closure.txt`
- Negative reference command/output: exact exported-symbol searches only hit this file
- Calibration modes relied on: alias, extensionless import, manifest roots
- Exclusion probes: no dynamic import, registry, public barrel, route handler, generated-file, or config-driven reference found
- Notes: color utility has no live consumers; newer conversation color logic appears to live under selectors/tokens.

Reference probe:

```bash
rg -n "AGENT_COLOR_PALETTE|getAgentColorClasses|getAgentInitials|agent-colors" src tests
```

Output:

```text
src/lib/agent-colors.ts:9:export const AGENT_COLOR_PALETTE = [
src/lib/agent-colors.ts:84:export function getAgentColorClasses(agentId: string) {
src/lib/agent-colors.ts:85:  const index = hashString(agentId) % AGENT_COLOR_PALETTE.length
src/lib/agent-colors.ts:86:  return AGENT_COLOR_PALETTE[index]
src/lib/agent-colors.ts:92:export function getAgentInitials(agentName: string): string {
```

Live-closure absence probe:

```bash
rg -n "^src/lib/agent-colors\\.ts$" /tmp/hybro-live-closure.txt
```

Output: no rows, exit 1.

## Excluded

### Export-level candidates

- Reason excluded: unsupported analyzer evidence for accepted findings
- Evidence checked: TypeScript AST export universe found 918 exports; `ts-prune@0.10.3` produced candidates but did not satisfy the full calibrated type/value namespace reference standard for this audit.
- Required evidence to promote later: a calibrated `tsserver`/language-service reference pass or TypeScript AST reference graph that classifies every export as live, finding, excluded, or unsupported.

### `src/components/ui/*` unused-looking primitives

- Reason excluded: generated/design-system style primitives under `src/components/ui`
- Evidence checked: files such as `alert.tsx`, `radio-group.tsx`, `scroll-area.tsx`, and `select.tsx` are absent from live closure, but they are local UI primitive inventory.
- Required evidence to promote later: a product/design-system decision that unused shadcn primitives are in scope for removal.

### Other not-live source files

- Reason excluded: ambiguous textual collisions, barrel/index semantics, or overlapping domain models.
- Evidence checked: exact search and live closure for `src/components/agent-card.tsx`, `src/hooks/room/index.ts`, `src/hooks/useRoomMessages.ts`, and `src/lib/types/memory.ts`.
- Required evidence to promote later: file-specific manual review proving no public/barrel/type/model compatibility surface remains.

## No-Action Evidence

This audit made no source, test, or config changes. It only created this Markdown report.

Post-audit verification commands run:

```bash
git diff --no-index --check /dev/null docs/DEAD_CODE_INVENTORY_AUDIT.md >/tmp/hybro-report-diff-check.txt; rc=$?; cat /tmp/hybro-report-diff-check.txt; test "$rc" -eq 1
rg -n "T[B]D|T[O]DO|P[E]NDING_PLACEHOLDER|\\x3c[A-Za-z]" docs/DEAD_CODE_INVENTORY_AUDIT.md
git status --short
while IFS= read -r file; do { git diff --binary -- "$file"; git diff --cached --binary -- "$file"; } | shasum -a 256; done < /tmp/hybro-pre-audit-dirty-files.txt > /tmp/hybro-post-audit-tracked-hashes.txt
git ls-files --others --exclude-standard -z | tr '\0' '\n' | rg -v '^docs/DEAD_CODE_INVENTORY_AUDIT\.md$' | xargs shasum -a 256 > /tmp/hybro-post-audit-untracked-hashes.txt 2>/dev/null || true
diff -u /tmp/hybro-pre-audit-tracked-hashes.txt /tmp/hybro-post-audit-tracked-hashes.txt
diff -u /tmp/hybro-pre-audit-untracked-hashes.txt /tmp/hybro-post-audit-untracked-hashes.txt
```

Verification output:

```text
git diff --no-index --check /dev/null docs/DEAD_CODE_INVENTORY_AUDIT.md >/tmp/hybro-report-diff-check.txt; rc=$?; cat /tmp/hybro-report-diff-check.txt; test "$rc" -eq 1
# no output from --check; wrapper exits 0 after confirming git's expected no-index difference status 1

rg -n "T[B]D|T[O]DO|P[E]NDING_PLACEHOLDER|\\x3c[A-Za-z]" docs/DEAD_CODE_INVENTORY_AUDIT.md
# no output; exit 1 because no matches

git status --short
 M docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md
 M package-lock.json
?? docs/DEAD_CODE_INVENTORY_AUDIT.md
?? docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md

diff -u /tmp/hybro-pre-audit-tracked-hashes.txt /tmp/hybro-post-audit-tracked-hashes.txt
# no output; exit 0

diff -u /tmp/hybro-pre-audit-untracked-hashes.txt /tmp/hybro-post-audit-untracked-hashes.txt
# no output; exit 0
```

Pre-existing dirty files were recorded before the report was created:

- `docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md`
- `package-lock.json`
- `docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md` (untracked)
