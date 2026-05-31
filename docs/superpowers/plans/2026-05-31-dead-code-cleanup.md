# Dead Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the five file-level dead-code findings accepted in `docs/DEAD_CODE_INVENTORY_AUDIT.md` and update stale architecture documentation that names removed files.

**Architecture:** This is a narrow deletion-only cleanup. The implementation must remove only the accepted low-risk file-level findings from the audit, avoid every excluded or unsupported candidate, verify there are no source/test/config consumers before and after deletion, and keep package metadata unchanged.

**Tech Stack:** Next.js 16, TypeScript, Vitest, `rg`, `git`, `npm`

**Source Documents:**

- `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- `docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md`
- `docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/components/scroll-range-spacer.tsx` | Delete | Accepted dead component with no live import path |
| `src/components/upgrade-button.tsx` | Delete | Accepted dead sidebar upgrade button with no live import path |
| `src/hooks/room/overlay-pending-hitl.ts` | Delete | Accepted dead deprecated compatibility re-export |
| `src/hooks/useAutoHideScroll.ts` | Delete | Accepted dead scroll behavior hook with no live import path |
| `src/lib/agent-colors.ts` | Delete | Accepted dead agent color utility with no live import path |
| `docs/architecture.md` | Modify | Remove stale file-tree entries for deleted files |
| `docs/FRONTEND_ISSUES.md` | Modify | Remove deleted `overlay-pending-hitl.ts` from the open ARC-8 architecture issue |
| `docs/superpowers/plans/2026-05-31-dead-code-cleanup.md` | Commit separately before cleanup | Planning artifact for this cleanup; not part of the cleanup implementation commit |

## Guardrails

- Do not delete or edit any candidate from the audit `Excluded` section.
- Do not make standalone export-level cleanup changes outside the five accepted file-level deletions. Deleting an accepted file necessarily removes exports inside that file; that is allowed only as part of the file-level deletion.
- Do not edit `package.json`, `package-lock.json`, `tsconfig.json`, `vitest.config.ts`, `playwright.config.ts`, `eslint.config.mjs`, `next.config.ts`, `postcss.config.mjs`, or `components.json`.
- Do not remove files referenced only in old specs/plans, except for updating `docs/architecture.md` entries that describe current source layout and the open `docs/FRONTEND_ISSUES.md` ARC-8 entry that names a deleted file.
- If any pre-deletion reference probe finds a live `src/`, `tests/`, package-script, or config consumer for a target file, stop and update this plan before deleting code.
- Commit this plan after the 15-review loop is complete and before starting code cleanup. The cleanup implementation commit must not include this plan file.
- Keep the cleanup in one commit after verification passes.

## Accepted Cleanup Targets

| Target | Audit finding | Expected pre-delete references outside docs |
|--------|---------------|---------------------------------------------|
| `src/components/scroll-range-spacer.tsx` | File-level dead code, low risk | Only its own declaration |
| `src/components/upgrade-button.tsx` | File-level dead code, low risk | Only its own declaration |
| `src/hooks/room/overlay-pending-hitl.ts` | File-level dead code, low risk | Only its own deprecated re-export; active code imports `@/lib/room-sync` |
| `src/hooks/useAutoHideScroll.ts` | File-level dead code, low risk | Only its own declaration |
| `src/lib/agent-colors.ts` | File-level dead code, low risk | Only symbols inside the same file |

## Task 0: Commit This Plan Before Cleanup

**Files:**
- Stage: `docs/superpowers/plans/2026-05-31-dead-code-cleanup.md`

- [ ] **Step 1: Confirm only this plan file is uncommitted**

Run:

```bash
git status --short
```

Expected output:

```text
?? docs/superpowers/plans/2026-05-31-dead-code-cleanup.md
```

- [ ] **Step 2: Commit only this plan file**

Run:

```bash
git add docs/superpowers/plans/2026-05-31-dead-code-cleanup.md
git commit -m "docs: add dead code cleanup plan"
```

Expected result: commit succeeds and includes only `docs/superpowers/plans/2026-05-31-dead-code-cleanup.md`.

- [ ] **Step 3: Confirm cleanup starts from a clean worktree**

Run:

```bash
git status --short --branch
```

Expected output shape:

```text
## main...origin/main [ahead 1]
```

`[ahead 1]` may be absent if the plan commit has already been pushed. There must be no `M`, `A`, `D`, `??`, or staged rows before Task 1 starts.

## Task 1: Capture Baseline And Confirm Cleanup Scope

**Files:**
- Read: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `src/components/scroll-range-spacer.tsx`
- Read: `src/components/upgrade-button.tsx`
- Read: `src/hooks/room/overlay-pending-hitl.ts`
- Read: `src/hooks/useAutoHideScroll.ts`
- Read: `src/lib/agent-colors.ts`

- [ ] **Step 1: Confirm the worktree is clean**

Run:

```bash
git status --short --branch
```

Expected output shape:

```text
## main...origin/main
```

Branch metadata such as `[ahead 1]` is allowed. There must be no `M`, `A`, `D`, `??`, or staged rows before cleanup starts.

- [ ] **Step 2: Confirm the audit still lists exactly five accepted file-level findings**

Run:

```bash
node - <<'NODE'
const fs = require('fs')
const report = fs.readFileSync('docs/DEAD_CODE_INVENTORY_AUDIT.md', 'utf8')
const findings = report.split('\n## Excluded\n')[0].split('\n## Findings\n')[1]
const targets = [
  'src/components/scroll-range-spacer.tsx',
  'src/components/upgrade-button.tsx',
  'src/hooks/room/overlay-pending-hitl.ts',
  'src/hooks/useAutoHideScroll.ts',
  'src/lib/agent-colors.ts',
]
const sections = findings
  .split(/\n(?=### `)/)
  .map((section) => {
    const match = section.match(/^### `([^`]+)`\n([\s\S]*)$/)
    return match ? [match[1], match[2]] : null
  })
  .filter(Boolean)
const accepted = sections
  .filter(([, body]) => body.includes('- Type: file-level dead code') && body.includes('- Risk: Low'))
  .map(([path]) => path)
console.log(JSON.stringify(accepted, null, 2))
if (accepted.length !== targets.length) process.exit(1)
for (const target of targets) {
  if (!accepted.includes(target)) {
    console.error(`missing accepted finding: ${target}`)
    process.exit(1)
  }
}
NODE
```

Expected output:

```json
[
  "src/components/scroll-range-spacer.tsx",
  "src/components/upgrade-button.tsx",
  "src/hooks/room/overlay-pending-hitl.ts",
  "src/hooks/useAutoHideScroll.ts",
  "src/lib/agent-colors.ts"
]
```

- [ ] **Step 3: Confirm all target files currently exist**

Run:

```bash
test -f src/components/scroll-range-spacer.tsx
test -f src/components/upgrade-button.tsx
test -f src/hooks/room/overlay-pending-hitl.ts
test -f src/hooks/useAutoHideScroll.ts
test -f src/lib/agent-colors.ts
```

Expected: command exits `0`.

## Task 2: Re-run Pre-delete Reference Guards

**Files:**
- Read: `src/`
- Read: `tests/`
- Read: `package.json`
- Read: `tsconfig.json`
- Read: `vitest.config.ts`
- Read: `playwright.config.ts`
- Read: `eslint.config.mjs`
- Read: `next.config.ts`
- Read: `postcss.config.mjs`
- Read: `components.json`

- [ ] **Step 0: Confirm package scripts still have no concrete repository file targets**

Run:

```bash
node - <<'NODE'
const fs = require('fs')
const path = require('path')
const pkg = require('./package.json')
const scripts = pkg.scripts || {}
const tokenRe = /([A-Za-z0-9_./-]+\.(?:js|jsx|cjs|mjs|ts|tsx|json|config\.ts|config\.mjs))/g
const unresolved = []
let foundResolvedTarget = false
for (const [name, command] of Object.entries(scripts)) {
  let match
  while ((match = tokenRe.exec(command))) {
    const token = match[1]
    const normalized = path.normalize(token)
    if (fs.existsSync(normalized)) {
      console.error(JSON.stringify({ name, token, resolved: normalized }))
      foundResolvedTarget = true
    } else {
      unresolved.push({ name, token })
    }
  }
}
if (foundResolvedTarget) process.exit(1)
console.log('no concrete package-script file targets')
console.log(JSON.stringify({ unresolvedScriptFileTokens: unresolved }))
NODE
```

Expected output:

```text
no concrete package-script file targets
{"unresolvedScriptFileTokens":[]}
```

If this fails, stop before deletion and update the plan to trace resolved package-script targets into `src/` and `tests/` using the reachability method from `docs/superpowers/plans/2026-05-30-dead-code-inventory-audit.md`. If `unresolvedScriptFileTokens` is non-empty, stop before deletion and manually classify each token as a package binary argument, shell glob, or repository path before continuing.

- [ ] **Step 1: Check source, tests, and scoped config for target references**

Run:

```bash
rg -n "ScrollRangeSpacer|scroll-range-spacer|UpgradeButton|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|AGENT_COLOR_PALETTE|getAgentColorClasses|getAgentInitials" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs next.config.ts postcss.config.mjs components.json
```

Expected output rows may appear in any order, but every row must be from one of these four files. `src/hooks/room/overlay-pending-hitl.ts` is covered by the filename existence check and dedicated HITL probes because the file content does not contain the kebab-case filename.

```text
src/components/scroll-range-spacer.tsx:5:interface ScrollRangeSpacerProps {
src/components/scroll-range-spacer.tsx:9:export function ScrollRangeSpacer({ scrollContainerRef }: ScrollRangeSpacerProps) {
src/components/upgrade-button.tsx:13:export function UpgradeButton() {
src/hooks/useAutoHideScroll.ts:12:export function useAutoHideScroll(
src/lib/agent-colors.ts:9:export const AGENT_COLOR_PALETTE = [
src/lib/agent-colors.ts:84:export function getAgentColorClasses(agentId: string) {
src/lib/agent-colors.ts:85:  const index = hashString(agentId) % AGENT_COLOR_PALETTE.length
src/lib/agent-colors.ts:86:  return AGENT_COLOR_PALETTE[index]
src/lib/agent-colors.ts:92:export function getAgentInitials(agentName: string): string {
```

If any output row outside those four files appears, stop and investigate before deleting files.

Use this parser if the unordered output is hard to inspect manually:

```bash
rg -n "ScrollRangeSpacer|scroll-range-spacer|UpgradeButton|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|AGENT_COLOR_PALETTE|getAgentColorClasses|getAgentInitials" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs next.config.ts postcss.config.mjs components.json \
  | cut -d: -f1 \
  | sort -u > /tmp/dead-code-cleanup-reference-files.txt
node - <<'NODE'
const fs = require('fs')
const allowed = new Set([
  'src/components/scroll-range-spacer.tsx',
  'src/components/upgrade-button.tsx',
  'src/hooks/useAutoHideScroll.ts',
  'src/lib/agent-colors.ts',
])
let ok = true
const files = fs.readFileSync('/tmp/dead-code-cleanup-reference-files.txt', 'utf8').trim().split('\n').filter(Boolean)
for (const file of files) {
  if (!allowed.has(file)) {
    console.error(`unexpected reference file: ${file}`)
    ok = false
  }
}
process.exit(ok ? 0 : 1)
NODE
```

Expected: no output, exit `0`.

- [ ] **Step 1a: Confirm the target filename paths exist in the repository**

Run:

```bash
rg --files src \
  | rg "^(src/components/scroll-range-spacer\\.tsx|src/components/upgrade-button\\.tsx|src/hooks/room/overlay-pending-hitl\\.ts|src/hooks/useAutoHideScroll\\.ts|src/lib/agent-colors\\.ts)$" \
  | sort
```

Expected output:

```text
src/components/scroll-range-spacer.tsx
src/components/upgrade-button.tsx
src/hooks/room/overlay-pending-hitl.ts
src/hooks/useAutoHideScroll.ts
src/lib/agent-colors.ts
```

- [ ] **Step 2: Check active HITL compatibility re-export consumers**

Run:

```bash
rg -n "from ['\"][^'\"]*overlay-pending-hitl|from ['\"]@/hooks/room/overlay-pending-hitl|overlay-pending-hitl" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs next.config.ts postcss.config.mjs components.json
```

Expected: no output, exit `1`.

- [ ] **Step 2a: Check HITL symbol references and allow active room-sync consumers**

Run:

```bash
rg -n "overlayPendingHitlRequests|pendingHitl" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs next.config.ts postcss.config.mjs components.json
```

Expected output includes active room-sync, room hydration, selector, test, and composer references plus the target re-export. Do not compare the full output literally; this is a broad symbol probe.

```text
src/lib/selectors/select-composer-state.ts:11:  const pendingHitls = selectPendingHitls(roomId, entities, orderedIds)
src/lib/room-sync/types.ts:35:  pendingHitlCount: number
src/lib/room-sync/hydrate-room.ts:140:    let pendingHitlCount = 0
src/lib/room-sync/hitl-overlay.ts:9:export async function overlayPendingHitlRequests(
src/lib/room-sync/hitl-overlay.ts:109:    pendingMessageIds = await overlayPendingHitlRequests(roomId, hitlRes.requests, {
src/lib/room-sync/index.ts:5:  overlayPendingHitlRequests,
src/components/composer/ComposerShell.tsx:85:  const hitlBar = composerState.pendingHitls.length > 0 ? (
src/hooks/room/overlay-pending-hitl.ts:2:export { overlayPendingHitlRequests } from '@/lib/room-sync/hitl-overlay'
```

If any import path references `src/hooks/room/overlay-pending-hitl.ts` or `@/hooks/room/overlay-pending-hitl`, stop and update the cleanup scope.

- [ ] **Step 3: Confirm documentation-only references are not treated as code consumers**

Run:

```bash
node - <<'NODE'
const fs = require('fs')
const lines = fs.readFileSync('docs/architecture.md', 'utf8').split('\n')
const pattern = /scroll-range-spacer|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|getAgentColorClasses/
const rows = lines
  .map((line, index) => `${index + 1}:${line}`)
  .filter((line) => pattern.test(line))
const expectedSubstrings = [
  'upgrade-button.tsx        # Pricing/upgrade sidebar button',
  'useAutoHideScroll.ts      # UI scroll behavior',
  'agent-colors.ts           # Agent color assignments',
]
const ok =
  rows.length === expectedSubstrings.length &&
  expectedSubstrings.every((expected) => rows.some((row) => row.includes(expected)))
if (!ok) {
  console.error(rows.join('\n'))
  process.exit(1)
}
console.log(rows.join('\n'))
NODE

rg -n "scroll-range-spacer|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|getAgentColorClasses" docs \
  --glob '!docs/superpowers/plans/2026-05-31-dead-code-cleanup.md'
```

Expected `docs/architecture.md` parser output:

```text
148:│   └── upgrade-button.tsx        # Pricing/upgrade sidebar button
157:│   ├── useAutoHideScroll.ts      # UI scroll behavior
200:│   ├── agent-colors.ts           # Agent color assignments
```

Expected broad docs output: rows may appear in historical top-level design documents, redesign notes, prior superpowers plans/specs, and the audit report, including `docs/ROOM_TIMELINE_DESIGN.md`, `docs/STREAMING_RENDERING_REDESIGN.md`, prior `docs/superpowers/plans/**`, and prior `docs/superpowers/specs/**`. `docs/FRONTEND_ISSUES.md` currently has an open ARC-8 architecture issue naming `overlay-pending-hitl.ts`; update that current issue in Task 4. Only the three `docs/architecture.md` rows shown above and the `docs/FRONTEND_ISSUES.md` ARC-8 location row should be updated by this cleanup; historical design, redesign, plan/spec, and audit records remain untouched.

## Task 3: Delete Accepted Dead-code Files

**Files:**
- Delete: `src/components/scroll-range-spacer.tsx`
- Delete: `src/components/upgrade-button.tsx`
- Delete: `src/hooks/room/overlay-pending-hitl.ts`
- Delete: `src/hooks/useAutoHideScroll.ts`
- Delete: `src/lib/agent-colors.ts`

- [ ] **Step 1: Delete the five accepted dead-code files**

Run:

```bash
rm src/components/scroll-range-spacer.tsx
rm src/components/upgrade-button.tsx
rm src/hooks/room/overlay-pending-hitl.ts
rm src/hooks/useAutoHideScroll.ts
rm src/lib/agent-colors.ts
```

Expected: commands exit `0`.

- [ ] **Step 2: Confirm Git sees exactly five source deletions**

Run:

```bash
git status --short -- src/components/scroll-range-spacer.tsx src/components/upgrade-button.tsx src/hooks/room/overlay-pending-hitl.ts src/hooks/useAutoHideScroll.ts src/lib/agent-colors.ts
```

Expected output:

```text
 D src/components/scroll-range-spacer.tsx
 D src/components/upgrade-button.tsx
 D src/hooks/room/overlay-pending-hitl.ts
 D src/hooks/useAutoHideScroll.ts
 D src/lib/agent-colors.ts
```

## Task 4: Update Current Architecture Documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/FRONTEND_ISSUES.md`

- [ ] **Step 1: Remove stale entries for deleted files**

Edit `docs/architecture.md` and remove these exact file-tree rows:

```text
│   └── upgrade-button.tsx        # Pricing/upgrade sidebar button
│   ├── useAutoHideScroll.ts      # UI scroll behavior
│   ├── agent-colors.ts           # Agent color assignments
```

Expected: no other `docs/architecture.md` content changes are made, except changing the remaining last sibling connector from `├──` to `└──` if removing `upgrade-button.tsx` makes `nav-discord-button.tsx` the final item in the `components/` tree.

- [ ] **Step 2: Update the open ARC-8 architecture issue**

Edit `docs/FRONTEND_ISSUES.md` and change the ARC-8 location row from:

```markdown
**Location**: `src/hooks/room/processing-lifecycle.ts`, `overlay-pending-hitl.ts`, `sse-handlers/`
```

to:

```markdown
**Location**: `src/hooks/room/processing-lifecycle.ts`, `sse-handlers/`
```

Expected: no other `docs/FRONTEND_ISSUES.md` content changes are made.

- [ ] **Step 3: Confirm removed architecture entries are gone**

Run:

```bash
rg -n "upgrade-button\\.tsx|useAutoHideScroll\\.ts|agent-colors\\.ts" docs/architecture.md
```

Expected: no output, exit `1`.

- [ ] **Step 4: Confirm the open issue no longer names the deleted file**

Run:

```bash
rg -n "overlay-pending-hitl\\.ts|overlay-pending-hitl" docs/FRONTEND_ISSUES.md
```

Expected: no output, exit `1`.

- [ ] **Step 5: Confirm documentation diffs are limited to stale current references**

Run:

```bash
git diff -- docs/architecture.md docs/FRONTEND_ISSUES.md
```

Expected: the `docs/architecture.md` diff removes only the three stale file-tree rows and, if needed, changes `nav-discord-button.tsx` from `├──` to `└──` so the `components/` tree remains valid. The `docs/FRONTEND_ISSUES.md` diff removes only `overlay-pending-hitl.ts` from the ARC-8 location row. No prose, section, or unrelated file-tree changes should appear.

## Task 5: Verify No Target References Remain

**Files:**
- Read: `src/`
- Read: `tests/`
- Read: `docs/architecture.md`
- Read: `docs/FRONTEND_ISSUES.md`
- Read: config files

- [ ] **Step 1: Re-run source/test/config reference guard**

Run:

```bash
rg -n "ScrollRangeSpacer|scroll-range-spacer|UpgradeButton|upgrade-button|overlay-pending-hitl|useAutoHideScroll|agent-colors|AGENT_COLOR_PALETTE|getAgentColorClasses|getAgentInitials" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs next.config.ts postcss.config.mjs components.json
```

Expected: no output, exit `1`.

- [ ] **Step 2: Verify deleted files do not exist**

Run:

```bash
for f in \
  src/components/scroll-range-spacer.tsx \
  src/components/upgrade-button.tsx \
  src/hooks/room/overlay-pending-hitl.ts \
  src/hooks/useAutoHideScroll.ts \
  src/lib/agent-colors.ts
do
  test ! -e "$f" || { echo "still exists: $f"; exit 1; }
done
```

Expected: no output, exit `0`.

- [ ] **Step 3: Confirm only expected files changed**

Run:

```bash
git status --short
```

Expected output:

```text
 M docs/architecture.md
 M docs/FRONTEND_ISSUES.md
 D src/components/scroll-range-spacer.tsx
 D src/components/upgrade-button.tsx
 D src/hooks/room/overlay-pending-hitl.ts
 D src/hooks/useAutoHideScroll.ts
 D src/lib/agent-colors.ts
```

If any other file appears, inspect it before continuing.

## Task 6: Run Validation Commands

**Files:**
- Read: `package.json`
- Read: `src/`
- Read: `tests/`

- [ ] **Step 1: Run lint**

Run:

```bash
npm run lint
```

Expected: Next lint exits `0`. If the script is unavailable because the installed Next.js version no longer exposes `next lint`, record the exact command output and rely on `npm run build` for TypeScript/Next validation.

- [ ] **Step 2: Run unit tests**

Run:

```bash
npm run test
```

Expected: Vitest exits `0`.

- [ ] **Step 3: Run production build**

Run:

```bash
npm run build
```

Expected: Next.js build exits `0`.

- [ ] **Step 4: Decide whether e2e is required**

Run:

```bash
printf '%s\n' "e2e not required for this deletion-only cleanup unless source/test reference guards changed, runtime UI code changed outside docs deletion fallout, or a reviewer requests it."
```

Expected output:

```text
e2e not required for this deletion-only cleanup unless source/test reference guards changed, runtime UI code changed outside docs deletion fallout, or a reviewer requests it.
```

If any condition in that sentence is true, run:

```bash
npm run test:e2e
```

Expected: Playwright exits `0`.

- [ ] **Step 5: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no output, exit `0`.

## Task 7: Commit The Cleanup

**Files:**
- Stage: `docs/architecture.md`
- Stage: `docs/FRONTEND_ISSUES.md`
- Stage: `src/components/scroll-range-spacer.tsx`
- Stage: `src/components/upgrade-button.tsx`
- Stage: `src/hooks/room/overlay-pending-hitl.ts`
- Stage: `src/hooks/useAutoHideScroll.ts`
- Stage: `src/lib/agent-colors.ts`

- [ ] **Step 1: Stage only the cleanup files**

Run:

```bash
git add docs/architecture.md \
  docs/FRONTEND_ISSUES.md \
  src/components/scroll-range-spacer.tsx \
  src/components/upgrade-button.tsx \
  src/hooks/room/overlay-pending-hitl.ts \
  src/hooks/useAutoHideScroll.ts \
  src/lib/agent-colors.ts
```

Expected: command exits `0`.

- [ ] **Step 2: Confirm staged scope**

Run:

```bash
git diff --cached --name-status
```

Expected output:

```text
M	docs/architecture.md
M	docs/FRONTEND_ISSUES.md
D	src/components/scroll-range-spacer.tsx
D	src/components/upgrade-button.tsx
D	src/hooks/room/overlay-pending-hitl.ts
D	src/hooks/useAutoHideScroll.ts
D	src/lib/agent-colors.ts
```

- [ ] **Step 3: Commit**

Run:

```bash
git commit -m "chore: remove audited dead code"
```

Expected: commit succeeds and includes only the seven staged paths.

- [ ] **Step 4: Verify cleanup commit and final worktree**

Run:

```bash
git diff-tree --no-commit-id --name-status -r HEAD
git status --short
```

Expected `git diff-tree` output:

```text
M	docs/architecture.md
M	docs/FRONTEND_ISSUES.md
D	src/components/scroll-range-spacer.tsx
D	src/components/upgrade-button.tsx
D	src/hooks/room/overlay-pending-hitl.ts
D	src/hooks/useAutoHideScroll.ts
D	src/lib/agent-colors.ts
```

Expected `git status --short`: no output.

## Completion Checklist

- [ ] The five accepted dead-code files from `docs/DEAD_CODE_INVENTORY_AUDIT.md` are deleted.
- [ ] No excluded audit candidates are modified, and no unsupported export-level candidates are edited except as a consequence of deleting one of the five accepted file-level targets.
- [ ] `docs/architecture.md` no longer lists deleted files as current source files.
- [ ] `docs/FRONTEND_ISSUES.md` ARC-8 no longer names `overlay-pending-hitl.ts` as an open current issue location.
- [ ] `docs/superpowers/plans/2026-05-31-dead-code-cleanup.md` was committed separately before Task 1 started.
- [ ] The cleanup implementation commit excludes `docs/superpowers/plans/2026-05-31-dead-code-cleanup.md`.
- [ ] Targeted `rg` reference guard returns no source/test/config matches after deletion.
- [ ] `npm run lint` passes, or its exact unsupported-script output is recorded and `npm run build` passes.
- [ ] `npm run test` passes.
- [ ] `npm run build` passes.
- [ ] `git diff --check` passes before commit.
- [ ] Final commit contains only the expected cleanup paths.
