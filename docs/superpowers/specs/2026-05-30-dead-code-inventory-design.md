# Dead Code Inventory Audit Design

## Status

Approved for planning. This design defines a read-only audit that produces a high-confidence inventory of dead code, dead tests, and unused test/config entrypoints. It does not include code deletion, test deletion, dependency cleanup, or PR batching.

## Problem

The frontend already completed one dead-code cleanup pass. A second cleanup pass should avoid speculative removals. The useful next step is a focused inventory that identifies only items with strong static evidence of being dead, so later cleanup work can be planned with low risk.

## Scope

Audit these areas:

- `src/` application code, hooks, components, stores, selectors, API helpers, room sync, and timeline logic.
- `tests/` unit tests, e2e tests, fixtures, setup files, and test utilities.
- Project entry/config files: `package.json`, `vitest.config.ts`, `playwright.config.ts`, `tsconfig.json`, and `eslint.config.mjs`.

Do not audit:

- npm dependency or devDependency usage.
- Backend or sibling repositories.
- Implementation files outside `src/` and `tests/` that are merely referenced by
  package scripts, such as `scripts/`, `tools/`, or ad hoc maintenance helpers.
  Root scripts and their target files are reachability inputs only, not findings.
- Product-level feature relevance where static evidence is not enough.
- Unused `package.json` script findings. Root scripts may be invoked by CI,
  documentation, hooks, release tooling, or humans outside the static source
  graph, so they can only inform reachability for `src/` and `tests/`.
- Config entries that may be activated by CI, docs, hooks, deployment settings,
  editor integrations, or other files outside the scoped source of truth.
- Low-confidence suspicious code.
- Removal steps, PR batches, or implementation changes.

## Approach

Use a static reference audit as the primary method. The audit should first build an explicit entrypoint manifest, then trace references from those entrypoints and configuration patterns. Report only candidates whose deadness is supported by reproducible static evidence.

The audit should include light route and test-entry validation, but only to support static evidence. It should not rely on runtime coverage or subjective age-of-code signals.

The entrypoint manifest must account for:

- Next.js convention files and route segments across `src/`, including `src/app/`,
  `src/pages/` if present, middleware/proxy files, route handlers, layouts,
  pages, loading/error boundaries, metadata routes, and special files for the
  installed Next.js version. The manifest must record the source used for the
  exact convention list rather than relying only on examples.
- TypeScript path aliases from `tsconfig.json`.
- Root package scripts and the tool configs those scripts invoke.
- Root package script targets outside `src/` and `tests/` as live entrypoints for
  tracing references back into `src/` and `tests/`, even though those target files
  are not themselves dead-code candidates. This tracing should be transitive for
  imports into candidate-scope files, but findings may only target files inside
  the stated source and test scope.
- Package-script reachability must show how script targets were extracted. If a
  script uses chained shell syntax, environment wrappers, package binaries,
  `npx`, or non-import file references that cannot be parsed confidently, any
  candidate whose liveness depends on that script path should be excluded.
  Traversal through outside-scope script targets should stop at unresolved shell
  steps, generated helpers, non-TypeScript/JavaScript file references, or imports
  that leave the repository. Those stop points should be recorded as exclusions
  for dependent candidates rather than treated as proof of deadness.
- Vitest project include/exclude globs, setup files, and test utilities reachable from those globs.
- Playwright test directory, global setup, fixtures, and configured web server command.
- Side-effect imports from live entrypoints.
- Generated or framework-owned files that should be excluded rather than treated as dead code.

For tool-owned configuration, prefer resolved configuration output from the tool
when available. If the audit relies on static reading of TypeScript config files
instead, the report must record that limitation and exclude findings whose
confidence depends on tool resolution semantics.

Reference checks must resolve normal project import forms before treating a
negative search as evidence. At minimum, they must handle `@/*` aliases,
extensionless imports, directory `index` imports, type-only imports, barrel
re-exports, and Next.js route/layout/page conventions.

Before accepting negative evidence, choose and record calibration fixtures
upfront, before candidate evaluation. These fixtures should be known-live
examples in this repository that exercise every resolution mode the audit relies
on: aliases, extensionless imports, directory `index` imports, barrel re-exports,
type-only imports, side-effect imports, Next route or layout/page conventions,
Vitest setup/include behavior, and Playwright setup or fixture behavior. If the
checks cannot find those known-live references, findings that depend on the
failed resolution mode must be excluded.

Export-level findings need TypeScript-aware reference checking, or an explicit
exclusion when the available checks cannot distinguish symbol usage from text
matches. The audit must not rely only on raw string search for exported symbols
when barrels, namespace imports, JSX component usage, computed property access,
or type/value namespace differences could change the result.

Acceptable export-level evidence includes `tsserver`/language-service
references, a TypeScript AST pass, or a dedicated unused-export analyzer that
uses the project `tsconfig.json`. The report must name the tool or script and
include enough output to show that the export has no value or type consumers. If
only text search is available, export-level candidates should be excluded.
Each export-level finding must state whether the unused conclusion applies to
the type namespace, value namespace, or both.

Analyzer metadata must include the tool name, version when available, config,
`tsconfig` project coverage, skipped files, and unsupported syntax or resolution
modes discovered during calibration.

## Evidence Standards

Include a candidate only when it meets one of these standards:

- **File-level dead code**: no live entrypoint, barrel export, framework route, test, or config file references the file.
- **Export-level dead code**: the containing file is live, but a specific exported function, type, constant, or helper has no consumers outside its declaration and tests that only exist for that export. Dead-code findings must list any test consumers excluded from the live-consumer set, explain whether they are single-subject or mixed-subject tests, and include the command or AST evidence supporting that classification.
- **Dead test**: the test covers only a confirmed-dead module, or a test helper/setup file has no consumers in the active Vitest or Playwright include paths. Each dead-test finding must list the subject under test, connect that subject to a confirmed-dead code finding, and show whether the test file itself is included by active test globs.
- **Dead config entry**: a config entry is not referenced by active tool
  configuration, package-script execution paths, or framework/tool conventions.
  Acceptable config findings are limited to inactive Vitest project globs/setup
  entries, inactive Playwright project/use entries, unreachable `tsconfig`
  include/path entries, or eslint config overrides whose matching files are
  absent in the scoped universe. Unused root package scripts are out of scope as
  findings. Config-entry consumers are limited to the named config files,
  `package.json`, package-script execution paths, and documented tool
  conventions. Any config entry that might require `.github/`, docs, hooks,
  deployment config, editor files, or other external activation sources for a
  high-confidence conclusion must be excluded.
  Each accepted config finding must state the exact bounded consumer set checked.
  If confidence depends on external activation sources that are outside scope,
  the item should be recorded as excluded rather than accepted.

Dead-test findings require import graph or AST evidence for the test's subjects.
Mixed-subject tests are excluded unless every subject under test is tied to a
confirmed-dead code finding.
E2E spec deadness is excluded unless the spec is outside active Playwright globs
or has explicit static subject evidence; browser-flow coverage alone is not
enough to prove a Playwright spec only covers dead code.
Subject identification must be based on direct imports, statically resolved
wrapper imports, or explicit test names that map to a single confirmed-dead
subject. Default to exclusion when a test uses shared fixtures, helpers,
browser-route flows, or multiple live and dead modules.

Exclude any candidate when static analysis cannot distinguish it from:

- a Next.js framework convention entrypoint,
- dynamic import or string-based lookup,
- public API surface that may be consumed outside the repository,
- generated or externally loaded file,
- product behavior requiring manual confirmation.

For exclusions that depend on dynamic or external reachability, the report must
show the probes that were checked before deciding whether the exclusion applies.
The minimum probe checklist is:

- Dynamic reachability: dynamic `import()` calls, route parameter lookups, and registry maps.
- Public API reachability: concrete public-surface artifacts such as package export declarations, documented external entrypoints, framework route handlers, or barrel files explicitly documented as cross-folder import surfaces. Do not infer public intent from a barrel file alone.
- Generated or external files: generated-file conventions, framework output paths, and configured external file references.
- Config-driven reachability: package-script execution paths, tool config references, and test runner setup hooks.

For each accepted finding, the report should show the actual probe scope,
command or analysis step, and pass/fail evidence rather than only naming the
probe category. For bulk exclusions, the main report may summarize the evidence
if it links to an appendix or command log with the full reproducible output.

## Report Format

The final audit report should be Markdown and contain:

1. **Scope**: exact paths and config files included.
2. **Method**: commands and reference checks used, including the entrypoint manifest.
3. **Candidate universe**: counts or summaries for scanned files, exports, tests,
   config entries, exclusions, and analyzer coverage gaps.
4. **Findings**: high-confidence candidates only. Each finding should include path, type, evidence, risk, and notes.
5. **Excluded**: notable suspicious areas intentionally left out because evidence was insufficient.
6. **No-action note**: explicit statement that the audit made no code or test changes.

The method section must also list the source of truth for the entrypoint
manifest and show how configured globs or convention-based entrypoints were
expanded before findings were evaluated.

Each manifest entry should include the path or pattern, its source of truth, the
expansion command or output summary, and a classification such as `live`,
`excluded/generated`, or `candidate-scope-only`.

Each finding should use a consistent evidence template with fields for the
candidate, entrypoint basis, positive live probes checked, negative reference
command and output summary, calibration modes relied on, exclusion probes, risk,
and notes.

The candidate universe summary must reconcile every candidate-scope file,
export, test, and eligible config entry into one of these outcomes: live,
accepted finding, excluded, or unsupported by the analyzer. Silent skips are not
acceptable.

For this reconciliation:

- `export` includes named exports, default exports, re-exports, type-only
  exports, and namespace exports from candidate-scope source files.
- `export` excludes internal unexported symbols and generated declaration files.
- `eligible config entry` means a top-level or named project/include/setup/alias/
  override entry from the scoped config files that matches the dead-config
  evidence standard. Arbitrary nested object fields are excluded unless the tool
  exposes them as independently addressable entries.

The method section must include the calibration examples used for reference
checking, the expected reference type for each example, the actual output
summary, and any resolution modes that failed and caused exclusions.

## Risk Levels

Use these labels in the inventory:

- **Low**: candidate appears isolated; removal would usually affect only itself or tests that only cover it.
- **Medium**: candidate has barrels, test fixtures, public exports, or framework-adjacent behavior that should be manually reviewed before removal.
- **High**: out of scope for this inventory. If a candidate would be high risk,
  record it only in the excluded section with the reason it was not accepted as a finding.

## Validation

The audit is acceptable when:

- Every finding has reproducible static evidence.
- Every finding includes the exact command or analysis step used, the expected empty or non-empty output shape, and the entrypoint or glob basis for the conclusion.
- The report proves the entrypoint manifest was built from the configured source
  of truth before applying candidate-specific checks.
- The report includes reference-check calibration evidence before relying on
  negative reference results.
- The inventory contains only high-confidence items.
- No npm dependency audit is included.
- No deletion steps or PR batches are included.
- No source, test, or config files are modified as part of producing the inventory.
- The report includes pre/post worktree evidence, such as `git status --short`,
  showing that producing the inventory did not modify source, test, or config files.

## Deliverable

The deliverable is a read-only Markdown inventory report. A later implementation plan can decide the exact report path and commands, then perform the audit without changing code.
