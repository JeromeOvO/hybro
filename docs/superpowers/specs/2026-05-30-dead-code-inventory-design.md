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
- Product-level feature relevance where static evidence is not enough.
- Low-confidence suspicious code.
- Removal steps, PR batches, or implementation changes.

## Approach

Use a static reference audit as the primary method. The audit should trace references from known entrypoints and configuration patterns, then report only candidates whose deadness can be reproduced with simple commands.

The audit should include light route and test-entry validation, but only to support static evidence. It should not rely on runtime coverage or subjective age-of-code signals.

## Evidence Standards

Include a candidate only when it meets one of these standards:

- **File-level dead code**: no live entrypoint, barrel export, framework route, test, or config file references the file.
- **Export-level dead code**: the containing file is live, but a specific exported function, type, constant, or helper has no consumers outside its declaration and tests that only exist for that export.
- **Dead test**: the test covers only a confirmed-dead module, or a test helper/setup file has no consumers in the active Vitest or Playwright include paths.
- **Dead config/script entry**: a config or script entry is not referenced by package scripts, active tool configuration, or framework/tool conventions.

Exclude any candidate when static analysis cannot distinguish it from:

- a Next.js framework convention entrypoint,
- dynamic import or string-based lookup,
- public API surface that may be consumed outside the repository,
- generated or externally loaded file,
- product behavior requiring manual confirmation.

## Report Format

The final audit report should be Markdown and contain:

1. **Scope**: exact paths and config files included.
2. **Method**: commands and reference checks used.
3. **Findings**: high-confidence candidates only. Each finding should include path, type, evidence, risk, and notes.
4. **Excluded**: notable suspicious areas intentionally left out because evidence was insufficient.
5. **No-action note**: explicit statement that the audit made no code or test changes.

## Risk Levels

Use these labels in the inventory:

- **Low**: candidate appears isolated; removal would usually affect only itself or tests that only cover it.
- **Medium**: candidate has barrels, test fixtures, public exports, or framework-adjacent behavior that should be manually reviewed before removal.
- **High**: do not include high-risk candidates in this inventory unless the static evidence is exceptionally clear.

## Validation

The audit is acceptable when:

- Every finding has reproducible static evidence.
- The inventory contains only high-confidence items.
- No npm dependency audit is included.
- No deletion steps or PR batches are included.
- No source, test, or config files are modified as part of producing the inventory.

## Deliverable

The deliverable is a read-only Markdown inventory report. A later implementation plan can decide the exact report path and commands, then perform the audit without changing code.
