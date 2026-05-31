# Dead Code Inventory Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a read-only Markdown inventory of high-confidence dead code, dead tests, and eligible dead config entries for the frontend.

**Architecture:** This is an audit-only workflow. Build an explicit entrypoint manifest, calibrate static reference checks against known-live examples, scan the scoped candidate universe, and write only the final inventory report. Do not delete code, edit tests, edit config, audit npm dependency usage, or batch PR cleanup work.

**Tech Stack:** Next.js 16, TypeScript, Vitest, Playwright, `rg`, `git`, `find`, `npm`/`npx`, TypeScript-aware reference tooling where available

**Spec:** `docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `docs/DEAD_CODE_INVENTORY_AUDIT.md` | Create | Final read-only audit report with scope, method, candidate universe, findings, exclusions, and no-action evidence |
| `docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md` | Read | Source of truth for audit scope and evidence standards |
| `package.json` | Read | Package scripts and tool command entrypoints |
| `tsconfig.json` | Read | TypeScript path aliases, include patterns, and project configuration |
| `vitest.config.ts` | Read | Unit/component test projects, include/exclude globs, setup files |
| `playwright.config.ts` | Read | E2E test directory, global setup, fixture reachability, web server command |
| `eslint.config.mjs` | Read | Eligible eslint override/config-entry universe |
| `src/` | Read | Candidate source files, framework entrypoints, imports, exports |
| `tests/` | Read | Candidate tests, fixtures, setup files, and test utilities |

## Guardrails

- Do not modify any file except `docs/DEAD_CODE_INVENTORY_AUDIT.md`.
- Treat the existing `package-lock.json` dirty state as unrelated. Do not stage, edit, or revert it.
- If a candidate requires product judgment, dynamic runtime behavior, CI/docs/hooks/editor/deployment evidence, or dependency usage analysis, record it as excluded rather than a finding.
- If a tool or analyzer cannot resolve a required import mode, exclude findings that depend on that mode.
- If there are no high-confidence findings, still write the report with the candidate universe, calibration evidence, exclusions, and no-action note.

### Task 1: Create the report skeleton and capture pre-audit worktree evidence

**Files:**
- Create: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md`

- [ ] **Step 1: Capture pre-audit status**

Run:

```bash
git status --short
```

Expected: output may include pre-existing dirty files. Record the complete output in the report under `No-Action Evidence`.

- [ ] **Step 1a: Capture pre-audit fingerprints for existing dirty files**

Run:

```bash
{ git diff --name-only; git diff --cached --name-only; } | sort -u > /tmp/hybro-pre-audit-dirty-files.txt
git status --porcelain=v1 -z > /tmp/hybro-pre-audit-status.z
while IFS= read -r file; do
  { git diff --binary -- "$file"; git diff --cached --binary -- "$file"; } | shasum -a 256
done < /tmp/hybro-pre-audit-dirty-files.txt > /tmp/hybro-pre-audit-tracked-hashes.txt
git ls-files --others --exclude-standard -z | xargs -0 shasum -a 256 > /tmp/hybro-pre-audit-untracked-hashes.txt 2>/dev/null || true
cat /tmp/hybro-pre-audit-dirty-files.txt
cat /tmp/hybro-pre-audit-tracked-hashes.txt
cat /tmp/hybro-pre-audit-untracked-hashes.txt
```

Expected: records baseline status, tracked diff hashes, and untracked file hashes before the audit report is created. Add the dirty file list and hash output to `No-Action Evidence`.

- [ ] **Step 2: Create the report skeleton**

Create `docs/DEAD_CODE_INVENTORY_AUDIT.md` with this exact structure:

```markdown
# Dead Code Inventory Audit

## Scope

## Method

## Candidate Universe

## Findings

## Excluded

## No-Action Evidence
```

- [ ] **Step 3: Fill the initial Scope section**

Add this content under `## Scope`:

```markdown
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
```

- [ ] **Step 4: Commit is not allowed yet**

Do not commit after this task. The report is incomplete until all audit sections are populated and self-reviewed.

### Task 2: Build the entrypoint manifest

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `package.json`
- Read: `tsconfig.json`
- Read: `vitest.config.ts`
- Read: `playwright.config.ts`
- Read: `eslint.config.mjs`
- Read: `src/`
- Read: `tests/`

- [ ] **Step 1: Record package scripts**

Run:

```bash
node -e "const p=require('./package.json'); for (const [name, cmd] of Object.entries(p.scripts || {})) console.log(name + ': ' + cmd)"
```

Expected: prints each package script and command. Add the output to `## Method` under `### Package Scripts`.

- [ ] **Step 1a: Parse package script targets and unresolved paths**

Run:

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

Expected: prints one JSON object per script. Add the output to `## Method` under `### Package Script Target Extraction`. Treat target files as live reachability inputs when they import `src/` or `tests/`. If `unresolvedShell` is true and the script path affects a candidate, exclude that candidate rather than using the script as proof of deadness.

- [ ] **Step 1b: Resolve package script targets into reachability edges**

Run:

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

Expected: prints script target tokens and whether each resolves to a repository file. For every resolved JavaScript or TypeScript target outside `src/` and `tests/`, traverse its imports with the same resolver used in Task 5 and add any resulting `src/` or `tests/` targets to the entrypoint manifest. For unresolved targets or shell steps, record the stop point and exclude dependent candidates.

- [ ] **Step 1c: Traverse resolved script targets into candidate-scope files**

Run:

```bash
node - <<'NODE' > /tmp/hybro-script-reachability.tsv
const fs = require('fs')
const path = require('path')
const exts = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']
const importRe = /(?:import\s+(?:type\s+)?[^'"]*from\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)|export\s+[^'"]*from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|^import\s*['"]([^'"]+)['"])/gm
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
const targets = fs.existsSync('/tmp/hybro-script-targets.txt')
  ? fs.readFileSync('/tmp/hybro-script-targets.txt', 'utf8').trim().split('\n').filter(Boolean).map((line) => line.split('\t')[1]).filter((file) => file && file !== 'unresolved')
  : []
const seen = new Set()
const queue = [...targets]
while (queue.length) {
  const file = queue.shift()
  if (seen.has(file) || !fs.existsSync(file)) continue
  seen.add(file)
  if (!/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(file)) {
    console.log([file, 'stop', 'non-js-ts-file'].join('\t'))
    continue
  }
  const text = fs.readFileSync(file, 'utf8')
  for (const match of text.matchAll(importRe)) {
    const spec = match[1] || match[2] || match[3] || match[4] || match[5]
    const resolved = resolveSpecifier(file, spec)
    console.log([file, spec, resolved].join('\t'))
    if (resolved !== 'external-or-package' && resolved !== 'unresolved' && !seen.has(resolved)) queue.push(resolved)
  }
}
NODE
sed -n '1,240p' /tmp/hybro-script-reachability.tsv
```

Expected: prints transitive edges from resolved script targets. Add candidate-scope `src/` and `tests/` edges to the entrypoint manifest. Record `external-or-package`, `unresolved`, and `non-js-ts-file` stop points and exclude candidates whose liveness depends on those unresolved paths.

- [ ] **Step 2: Record TypeScript aliases and include patterns**

Run:

```bash
node -e "const ts=require('./tsconfig.json'); console.log(JSON.stringify({baseUrl:ts.compilerOptions?.baseUrl, paths:ts.compilerOptions?.paths, include:ts.include, exclude:ts.exclude}, null, 2))"
```

Expected: prints `@/*` path mapping, include list, and exclude list. Add the output to `## Method` under `### TypeScript Config`.

- [ ] **Step 3: Record Next.js convention entrypoints present in this repository**

Run:

```bash
node -e "const p=require('./package.json'); console.log('next dependency:', p.dependencies?.next || p.devDependencies?.next || 'not listed')"
node - <<'NODE'
const fs = require('fs')
const pkgPath = 'node_modules/next/package.json'
if (fs.existsSync(pkgPath)) {
  const pkg = require('./' + pkgPath)
  console.log('installed next version:', pkg.version)
} else {
  console.log('installed next version: node_modules/next not available')
}
NODE
cat > /tmp/hybro-next-convention-source.md <<'EOF'
Next convention source for this audit:
- Installed/configured Next.js major version from `package.json` and `node_modules/next/package.json` when available.
- Next.js App Router file conventions for the installed major version: `page`, `layout`, `template`, `loading`, `error`, `global-error`, `not-found`, `default`, route handlers, metadata files, and metadata image/file conventions.
- Next.js root request entry conventions: middleware/proxy files when present.

If an installed-package source or official docs source cannot be checked during execution, framework-adjacent candidates that depend on an unverified convention must be excluded.
EOF
cat /tmp/hybro-next-convention-source.md
find node_modules/next -maxdepth 3 -type f \( -name '*.d.ts' -o -name '*.js' -o -name '*.mjs' \) 2>/dev/null | rg '/server|/dist|metadata|route|middleware|proxy' | sed -n '1,80p' || true
find src -type f \( \
  -path 'src/app/page.*' -o \
  -path 'src/app/layout.*' -o \
  -path 'src/app/route.*' -o \
  -path 'src/app/loading.*' -o \
  -path 'src/app/error.*' -o \
  -path 'src/app/not-found.*' -o \
  -path 'src/app/global-error.*' -o \
  -path 'src/app/template.*' -o \
  -path 'src/app/default.*' -o \
  -path 'src/app/icon.*' -o \
  -path 'src/app/apple-icon.*' -o \
  -path 'src/app/opengraph-image.*' -o \
  -path 'src/app/twitter-image.*' -o \
  -path 'src/app/sitemap.*' -o \
  -path 'src/app/robots.*' -o \
  -path 'src/app/manifest.*' -o \
  -path '*/app/*/page.*' -o \
  -path '*/app/*/layout.*' -o \
  -path '*/app/*/route.*' -o \
  -path '*/app/*/loading.*' -o \
  -path '*/app/*/error.*' -o \
  -path '*/app/*/not-found.*' -o \
  -path '*/app/*/global-error.*' -o \
  -path '*/app/*/template.*' -o \
  -path '*/app/*/default.*' -o \
  -path '*/app/*/icon.*' -o \
  -path '*/app/*/apple-icon.*' -o \
  -path '*/app/*/opengraph-image.*' -o \
  -path '*/app/*/twitter-image.*' -o \
  -path '*/app/*/sitemap.*' -o \
  -path '*/app/*/robots.*' -o \
  -path '*/app/*/manifest.*' -o \
  -path '*/pages/*' -o \
  -name 'middleware.ts' -o \
  -name 'middleware.js' -o \
  -name 'proxy.ts' -o \
  -name 'proxy.js' \
\) | sort
```

Expected: prints the configured Next.js version range, installed Next.js version if dependencies are installed, available installed-package convention evidence, and framework-owned source entrypoints such as `src/app/...` files and `src/proxy.ts` if present. Before accepting the manifest, record an exact convention source for the installed Next.js major version: either installed package evidence that identifies the convention, or an official docs source checked during execution. If neither source is verified, do not accept framework-adjacent findings whose classification depends on exact Next conventions; record them as excluded.

- [ ] **Step 4: Record Vitest include/exclude and setup paths**

Run:

```bash
sed -n '1,220p' vitest.config.ts
```

Expected: prints the configured Vitest projects, include globs, exclude globs, and setup files. Summarize them in `## Method` under `### Vitest Entrypoints`.

- [ ] **Step 4a: Expand active Vitest globs and setup files**

Run:

```bash
find src tests/unit -type f \( -name '*.test.ts' -o -name '*.test.tsx' \) | sort > /tmp/hybro-vitest-included-tests.txt
find tests/setup -type f \( -name '*.ts' -o -name '*.tsx' \) | sort > /tmp/hybro-vitest-setup-files.txt
sed -n '1,240p' /tmp/hybro-vitest-included-tests.txt
sed -n '1,240p' /tmp/hybro-vitest-setup-files.txt
```

Expected: prints active Vitest test files and setup files based on the configured include/setup patterns. Add this output summary to `## Method`; use it for dead-test and live-closure decisions instead of hand-reading globs.

- [ ] **Step 5: Record Playwright entrypoints**

Run:

```bash
sed -n '1,220p' playwright.config.ts
find tests/e2e -type f | sort
```

Expected: prints Playwright config and all E2E spec/helper files. Summarize `testDir`, `globalSetup`, fixtures, and web server command in `## Method` under `### Playwright Entrypoints`.

- [ ] **Step 5a: Expand active Playwright specs and setup files**

Run:

```bash
find tests/e2e -type f -name '*.spec.ts' | sort > /tmp/hybro-playwright-included-specs.txt
printf 'tests/e2e/global-setup.ts\n' > /tmp/hybro-playwright-setup-files.txt
find tests/e2e/fixtures -type f \( -name '*.ts' -o -name '*.tsx' \) 2>/dev/null | sort > /tmp/hybro-playwright-fixtures.txt
sed -n '1,240p' /tmp/hybro-playwright-included-specs.txt
sed -n '1,240p' /tmp/hybro-playwright-setup-files.txt
sed -n '1,240p' /tmp/hybro-playwright-fixtures.txt
```

Expected: prints active Playwright specs, global setup, and fixtures from the configured `testDir` and `globalSetup`. Add this output summary to `## Method`; use it for E2E dead-test and live-closure decisions.

- [ ] **Step 6: Record eslint config scope**

Run:

```bash
sed -n '1,220p' eslint.config.mjs
```

Expected: prints eslint config. Record whether there are overrides with candidate file patterns. If no overrides exist, state that no eslint config entries are eligible findings.

- [ ] **Step 7: Write accepted manifest roots to a file**

After recording package-script, Next.js, Vitest, and Playwright entrypoints, write every accepted entrypoint root to `/tmp/hybro-manifest-roots.txt`.

Run:

```bash
{
  sed -n '1,240p' /tmp/hybro-vitest-included-tests.txt 2>/dev/null
  sed -n '1,240p' /tmp/hybro-vitest-setup-files.txt 2>/dev/null
  sed -n '1,240p' /tmp/hybro-playwright-included-specs.txt 2>/dev/null
  sed -n '1,240p' /tmp/hybro-playwright-setup-files.txt 2>/dev/null
  sed -n '1,240p' /tmp/hybro-playwright-fixtures.txt 2>/dev/null
  awk -F '\t' '$3 ~ /^(src|tests)\// { print $3 }' /tmp/hybro-script-reachability.tsv 2>/dev/null
} | sort -u > /tmp/hybro-manifest-roots.txt
sort -u /tmp/hybro-manifest-roots.txt | sed -n '1,240p'
```

Expected: prints only files accepted from configured package scripts, verified Next conventions, Vitest include/setup paths, and Playwright test/setup/fixture paths. If an entrypoint category is unverified, do not put approximated roots in this file; record dependent candidates as excluded.

### Task 3: Define candidate universe and reconciliation buckets

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `src/`
- Read: `tests/`
- Read: root config files

- [ ] **Step 1: Count candidate source files**

Run:

```bash
find src -type f \( -name '*.ts' -o -name '*.tsx' \) ! -name '*.test.ts' ! -name '*.test.tsx' | sort > /tmp/hybro-src-files.txt
wc -l /tmp/hybro-src-files.txt
sed -n '1,240p' /tmp/hybro-src-files.txt
```

Expected: prints the candidate source file count and file list, excluding `src/**/*.test.ts` and `src/**/*.test.tsx` because those are reconciled only as tests. Add the count to `## Candidate Universe`; add the full list or a command-log reference.

- [ ] **Step 2: Count candidate test and test-support files**

Run:

```bash
find tests src -type f \( -name '*.test.ts' -o -name '*.test.tsx' -o -name '*.spec.ts' -o -name '*.spec.tsx' \) | sort > /tmp/hybro-test-files.txt
find tests -type f \( -name '*.ts' -o -name '*.tsx' \) | sort > /tmp/hybro-test-support-files.txt
wc -l /tmp/hybro-test-files.txt
wc -l /tmp/hybro-test-support-files.txt
sed -n '1,240p' /tmp/hybro-test-files.txt
sed -n '1,240p' /tmp/hybro-test-support-files.txt
```

Expected: prints unit, component, hook, and E2E test file count and list, plus every TypeScript test-support file under `tests/`, including setup files, fixtures, utilities, and Playwright helpers. Add both counts to `## Candidate Universe`.

- [ ] **Step 3: Enumerate exports with a TypeScript-aware analyzer if available**

Run:

```bash
node - <<'NODE' > /tmp/hybro-export-universe.tsv
let ts
try {
  ts = require('typescript')
} catch {
  process.stderr.write('typescript package unavailable; export universe unsupported\n')
  process.exit(2)
}
const fs = require('fs')
const path = require('path')
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (/\.(ts|tsx)$/.test(full) && !/\.test\.(ts|tsx)$/.test(full)) out.push(full)
  }
  return out
}
function emit(file, kind, name) {
  console.log([file, kind, name || 'default'].join('\t'))
}
for (const file of walk('src')) {
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  function visit(node) {
    const mods = ts.canHaveModifiers(node) ? ts.getModifiers(node) || [] : []
    const exported = mods.some((mod) => mod.kind === ts.SyntaxKind.ExportKeyword)
    const isDefault = mods.some((mod) => mod.kind === ts.SyntaxKind.DefaultKeyword)
    if (exported && node.name && ts.isIdentifier(node.name)) emit(file, isDefault ? 'default export' : 'named export', node.name.text)
    else if (exported && isDefault) emit(file, 'default export', 'default')
    if (ts.isExportDeclaration(node)) {
      if (!node.exportClause) emit(file, 'namespace re-export', node.moduleSpecifier?.text || '*')
      else if (ts.isNamespaceExport(node.exportClause)) emit(file, 'namespace export', node.exportClause.name.text)
      else for (const spec of node.exportClause.elements) emit(file, node.isTypeOnly ? 'type re-export' : 'named re-export', spec.name.text)
    }
    if (ts.isExportAssignment(node)) emit(file, 'export assignment', 'default')
    ts.forEachChild(node, visit)
  }
  visit(source)
}
NODE
wc -l /tmp/hybro-export-universe.tsv
sed -n '1,240p' /tmp/hybro-export-universe.tsv
npm ls ts-prune --depth=0 || true
npx --yes ts-prune@0.10.3 --version
npx --yes ts-prune@0.10.3 -p tsconfig.json
```

Expected: prints a TypeScript AST-derived export universe, then either prints analyzer version plus unused-export candidates or fails because the analyzer is unavailable/incompatible. Record the command, exit status, version, project config (`tsconfig.json`), export-universe count, output summary, and any skipped-file/unsupported-mode limitations under `## Method` as analyzer metadata.

- [ ] **Step 4: If `ts-prune` is unavailable or unreliable, mark export findings unsupported**

If Step 3 fails, cannot produce version/config metadata, or cannot prove TypeScript-aware reference behavior, add this to `## Candidate Universe`:

```markdown
Export-level findings: unsupported by the available analyzer in this audit. Export candidates were not accepted as findings because the design requires TypeScript-aware reference checking.
```

Expected: no export-level finding is accepted unless the analyzer output is usable and calibrated in Task 4.
If the TypeScript AST export universe cannot be enumerated, mark export-level analysis unsupported as a whole because row-level export reconciliation would be incomplete.

- [ ] **Step 5: Define reconciliation statuses**

Add this text to `## Candidate Universe`:

```markdown
Each candidate-scope file, export, test, and eligible config entry is reconciled as one of:

- `live`
- `accepted finding`
- `excluded`
- `unsupported by analyzer`
```

Expected: the report has explicit reconciliation buckets before findings are evaluated.

- [ ] **Step 6: Create a per-candidate reconciliation log in the report**

Add a `### Reconciliation Log` subsection under `## Candidate Universe` with one row per candidate category. Use this structure:

```markdown
### Reconciliation Log

Source files: see command output from `/tmp/hybro-src-files.txt`; every source file is later classified as `live`, `accepted finding`, `excluded`, or `unsupported by analyzer`.

Exports: classification is based on the TypeScript-aware analyzer output if calibrated; otherwise all export-level findings are `unsupported by analyzer`.

Tests and test support: see command output from `/tmp/hybro-test-files.txt` and `/tmp/hybro-test-support-files.txt`; every test, setup file, fixture, helper, and utility is later classified as `live`, `accepted finding`, `excluded`, or `unsupported by analyzer`.

Eligible config entries: list each eligible Vitest, Playwright, tsconfig, or eslint entry considered, then classify it as `live`, `accepted finding`, `excluded`, or `unsupported by analyzer`.
```

Expected: the final audit has a place to reconcile every candidate-scope item, not only aggregate counts.

- [ ] **Step 7: Add row-level reconciliation table requirements**

Add this text under `### Reconciliation Log`:

```markdown
Each reconciled item must appear in a row-level table with these columns: `Item`, `Category`, `Outcome`, `Evidence reference`, and `Notes`. Source files, tests, test-support files, eligible config entries, and enumerated exports must each have one row. If TypeScript-aware export analysis is not calibrated, every export from `/tmp/hybro-export-universe.tsv` still gets its own row with outcome `unsupported by analyzer`.
```

Expected: the report cannot satisfy reconciliation with aggregate counts alone.

### Task 4: Calibrate reference checks on known-live examples

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `src/`
- Read: `tests/`
- Read: config files

- [ ] **Step 1: Select calibration fixtures before evaluating candidates**

Add a `### Calibration Fixtures` subsection under `## Method` with this table:

```markdown
| Mode | Fixture | Expected reference behavior | Result |
|------|---------|-----------------------------|--------|
| Alias import | Record the concrete path and imported module selected in Step 2 | Analyzer resolves alias to source file | Record command output before candidate evaluation |
| Extensionless import | Record the concrete path and imported module selected in Step 3 | Analyzer resolves `.ts` or `.tsx` file | Record command output before candidate evaluation |
| Directory index import | Record the concrete path and imported directory selected in Step 3 | Analyzer resolves `index.ts` or `index.tsx` | Record command output before candidate evaluation |
| Barrel re-export | Record the concrete barrel file and re-export selected in Step 3 | Analyzer follows re-export to consumers | Record command output before candidate evaluation |
| Type-only import | Record the concrete `import type` selected in Step 4 | Analyzer distinguishes type namespace | Record command output before candidate evaluation |
| Side-effect import | Record the concrete side-effect import selected in Step 4 | Analyzer treats module as live | Record command output before candidate evaluation |
| Next convention | Record the concrete route, layout, page, or `src/proxy.ts` entrypoint selected in Task 2 | Manifest treats file as live framework entrypoint | Record manifest evidence before candidate evaluation |
| Vitest setup/include | Record the concrete setup file or included test selected from `vitest.config.ts` | Manifest treats setup and included tests as live | Record manifest evidence before candidate evaluation |
| Playwright setup/fixture | Record the concrete global setup, fixture, or spec selected from `playwright.config.ts` | Manifest treats setup, fixture, or spec as live | Record manifest evidence before candidate evaluation |
```

Expected: fixtures are recorded before candidate decisions.

- [ ] **Step 2: Find concrete alias import fixture**

Run:

```bash
rg -n "from ['\"]@/" src tests | sed -n '1,20p'
```

Expected: prints one or more alias import examples. Replace the `Alias import` fixture row with the selected path and symbol or mark the mode unsupported if no examples exist.

- [ ] **Step 3: Find concrete extensionless and barrel fixtures**

Run:

```bash
rg -n "from ['\"]\\./[^'\"]+['\"]|from ['\"]\\.\\./[^'\"]+['\"]" src tests | sed -n '1,40p'
rg -n "^export \\*|^export \\{" src | sed -n '1,40p'
```

Expected: prints local extensionless imports and barrel exports if present. Fill the corresponding fixture rows or mark unsupported.

- [ ] **Step 4: Find concrete type-only and side-effect fixtures**

Run:

```bash
rg -n "import type" src tests | sed -n '1,30p'
rg -n "^import ['\"][^'\"]+['\"]" src tests | sed -n '1,30p'
```

Expected: prints type-only and side-effect import candidates if present. Fill the corresponding fixture rows or mark unsupported.

- [ ] **Step 5: Record calibration pass/fail**

Use the selected fixtures to check whether the chosen analyzer or manual reference method finds the expected references. Record the command and output summary in the `Result` column for each fixture.

Expected: any mode that fails calibration is marked `unsupported`, and findings depending on that mode are excluded.

### Task 5: Evaluate file-level dead-code candidates

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `src/`
- Read: `tests/`

- [ ] **Step 1: Generate import specifier inventory**

Run:

```bash
rg -n "from ['\"][^'\"]+['\"]|import\\(['\"][^'\"]+['\"]\\)|^import ['\"][^'\"]+['\"]" src tests | sort > /tmp/hybro-imports.txt
wc -l /tmp/hybro-imports.txt
sed -n '1,240p' /tmp/hybro-imports.txt
```

Expected: prints import/reference count and import lines. Add the count to `## Candidate Universe`.

- [ ] **Step 1a: Build a resolved import graph before accepting file findings**

Run:

```bash
node - <<'NODE' > /tmp/hybro-import-graph.tsv
const fs = require('fs')
const path = require('path')
const exts = ['.ts', '.tsx', '.js', '.jsx']
const roots = ['src', 'tests']
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (/\.(ts|tsx)$/.test(full)) out.push(full)
  }
  return out
}
function resolveSpecifier(fromFile, spec) {
  const tsconfig = JSON.parse(fs.readFileSync('tsconfig.json', 'utf8'))
  const paths = tsconfig.compilerOptions?.paths || {}
  const baseUrl = tsconfig.compilerOptions?.baseUrl || '.'
  for (const [alias, targets] of Object.entries(paths)) {
    const prefix = alias.replace(/\*$/, '')
    if (spec.startsWith(prefix)) {
      const rest = spec.slice(prefix.length)
      const target = String(targets[0]).replace(/\*$/, rest)
      spec = path.normalize(path.join(baseUrl, target))
      break
    }
  }
  if (spec.startsWith('.')) spec = path.normalize(path.join(path.dirname(fromFile), spec))
  else if (!spec.startsWith('src/') && !spec.startsWith('tests/')) return { spec, resolved: 'external-or-package' }
  const candidates = []
  for (const ext of exts) candidates.push(spec + ext)
  for (const ext of exts) candidates.push(path.join(spec, 'index' + ext))
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return { spec, resolved: candidate }
  }
  return { spec, resolved: 'unresolved' }
}
const importRe = /(?:import\s+(?:type\s+)?[^'"]*from\s*['"]([^'"]+)['"]|export\s+[^'"]*from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|^import\s*['"]([^'"]+)['"])/gm
for (const file of roots.flatMap((root) => walk(root))) {
  const text = fs.readFileSync(file, 'utf8')
  for (const match of text.matchAll(importRe)) {
    const spec = match[1] || match[2] || match[3] || match[4]
    const resolved = resolveSpecifier(file, spec)
    console.log([file, spec, resolved.resolved].join('\t'))
  }
}
NODE
sed -n '1,240p' /tmp/hybro-import-graph.tsv
```

Expected: prints importer, import specifier, and resolved target. Add the command and output summary under `## Method`. File-level findings may be accepted only if this graph resolves the relevant alias, extensionless, directory index, and barrel paths used by the candidate. If the graph reports `unresolved` for paths that affect a candidate, exclude that candidate.

- [ ] **Step 1b: Compute live file closure from the entrypoint manifest**

Run:

```bash
node - <<'NODE' > /tmp/hybro-live-closure.txt
const fs = require('fs')
const graphPath = '/tmp/hybro-import-graph.tsv'
const manifestCandidates = fs.existsSync('/tmp/hybro-manifest-roots.txt')
  ? fs.readFileSync('/tmp/hybro-manifest-roots.txt', 'utf8').split('\n').filter(Boolean)
  : []
if (fs.existsSync('/tmp/hybro-script-reachability.tsv')) {
  for (const line of fs.readFileSync('/tmp/hybro-script-reachability.tsv', 'utf8').split('\n')) {
    const parts = line.split('\t')
    const resolved = parts[2]
    if (resolved && /^(src|tests)\//.test(resolved)) manifestCandidates.push(resolved)
  }
}
const edges = new Map()
if (fs.existsSync(graphPath)) {
  for (const line of fs.readFileSync(graphPath, 'utf8').split('\n').filter(Boolean)) {
    const [from, , to] = line.split('\t')
    if (!edges.has(from)) edges.set(from, [])
    if (to && /^(src|tests)\//.test(to)) edges.get(from).push(to)
  }
}
const seen = new Set()
const queue = [...new Set(manifestCandidates.filter(Boolean))]
while (queue.length) {
  const file = queue.shift()
  if (seen.has(file)) continue
  seen.add(file)
  for (const next of edges.get(file) || []) {
    if (!seen.has(next)) queue.push(next)
  }
}
for (const file of [...seen].sort()) console.log(file)
NODE
wc -l /tmp/hybro-live-closure.txt
sed -n '1,240p' /tmp/hybro-live-closure.txt
```

Expected: prints files reachable from `/tmp/hybro-manifest-roots.txt` and package-script entrypoints. Use this live closure as the primary `live` classification for file-level reconciliation. Do not seed the closure with hard-coded test/support patterns; the manifest root file must come from actual configured globs, setup files, framework conventions, and script reachability. A disconnected cluster that only references itself is not live unless it is reached from the manifest.

- [ ] **Step 2: Identify unreferenced source files conservatively**

For each non-framework candidate source file from `/tmp/hybro-src-files.txt`, check direct path references, basename references, alias references, barrel references, and test references.

Run this template for each candidate path:

```bash
candidate='src/lib/example.ts'
stem="${candidate#src/}"
stem="${stem%.ts}"
stem="${stem%.tsx}"
base="$(basename "$stem")"
printf 'Candidate: %s\n' "$candidate"
rg -n "$stem|$base" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs
```

Expected: accepted findings have no live references after resolving aliases, extensionless imports, directory indexes, barrels, framework entrypoints, tests, and config reachability. Candidates with ambiguous text hits are excluded.

- [ ] **Step 3: Apply exclusion probes to each possible file finding**

For each possible file-level finding, run:

```bash
candidate='src/lib/example.ts'
rg -n "import\\(|registry|Record<|\\[[^\\]]+\\]|route|params|export \\*|export \\{|from ['\"]@/lib/example|from ['\"]\\.*/example" src tests package.json tsconfig.json vitest.config.ts playwright.config.ts eslint.config.mjs
```

Expected: if dynamic reachability, registry maps, public documented surfaces, generated conventions, or config-driven references could apply, record the candidate under `## Excluded` rather than `## Findings`.

- [ ] **Step 4: Add accepted file-level findings using the evidence template**

For each accepted file-level finding, add a subsection under `## Findings` whose heading is the exact candidate path. The subsection must include these fields with actual values: `Type`, `Risk`, `Entrypoint basis`, `Positive live probes checked`, `Negative reference command/output`, `Calibration modes relied on`, `Exclusion probes`, and `Notes`.

Expected: raw `rg` output is supporting evidence only. It is not sufficient to accept a file-level finding unless the resolved import graph and manifest checks also show no live path to the candidate.

Expected: no finding is added without a complete template.

### Task 6: Evaluate export-level candidates

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `src/`
- Read: `tests/`

- [ ] **Step 1: Use TypeScript-aware analyzer output only if calibrated**

If Task 3 and Task 4 produced usable TypeScript-aware analyzer output, review each unused-export candidate. If not, write this under `## Excluded`:

```markdown
### Export-level candidates

Export-level findings were excluded because the available checks could not provide calibrated TypeScript-aware reference evidence across aliases, barrels, type-only imports, and value/type namespace distinctions.
```

Expected: export findings are absent unless the analyzer is calibrated.

- [ ] **Step 2: For each analyzer candidate, verify namespace and test consumers with TypeScript-aware evidence**

Use the calibrated analyzer, `tsserver`/language-service references, or a TypeScript AST pass to classify type consumers, value consumers, namespace imports, JSX references, barrel consumption, and test-only consumers. Use raw text search only as supplemental context.

Run this supplemental context command after TypeScript-aware evidence is recorded:

```bash
symbol='ExampleExport'
rg -n "\\b${symbol}\\b" src tests
```

Expected: accepted export findings cite TypeScript-aware evidence for namespace and consumer classification. If only raw `rg` evidence is available, classify the export candidate as `unsupported by analyzer` or `excluded`; do not accept it as a finding.

- [ ] **Step 3: Add accepted export-level findings using the evidence template**

For each accepted export-level finding, add a subsection under `## Findings` whose heading is the exact source file and export name. The subsection must include these fields with actual values: `Type`, `Namespace`, `Risk`, `Entrypoint basis`, `Positive live probes checked`, `Negative reference command/output`, `Test consumers excluded`, `Calibration modes relied on`, `Exclusion probes`, and `Notes`.

Expected: no export-level finding is added from raw string search alone.

### Task 7: Evaluate dead-test candidates

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `tests/`
- Read: `src/`
- Read: `vitest.config.ts`
- Read: `playwright.config.ts`

- [ ] **Step 1: Identify tests outside active include globs**

Compare `/tmp/hybro-test-files.txt` with the Vitest and Playwright include patterns recorded in Task 2.

Run:

```bash
printf 'All candidate tests:\n'
sed -n '1,240p' /tmp/hybro-test-files.txt
printf '\nConfigured Vitest/Playwright includes are recorded in docs/DEAD_CODE_INVENTORY_AUDIT.md\n'
```

Expected: tests outside active globs may be dead-test findings if no other runner includes them. Tests inside active globs require subject evidence.

- [ ] **Step 2: Map unit/component test subjects with static imports**

Run this template for each test candidate:

```bash
test_file='tests/unit/example.test.ts'
printf 'Test: %s\n' "$test_file"
rg -n "from ['\"][^'\"]+['\"]|^import ['\"][^'\"]+['\"]" "$test_file"
```

Expected: direct imports or statically resolved wrapper imports identify the subject under test. Tests importing shared fixtures, helpers, multiple live/dead modules, or broad route flows are excluded.

- [ ] **Step 3: Treat E2E specs conservatively**

For each `tests/e2e/*.spec.ts` file, classify it as excluded unless it is outside active Playwright globs or has explicit static subject evidence.

Expected: no Playwright spec is accepted as dead only because a browser flow appears old.

- [ ] **Step 4: Add accepted dead-test findings**

For each accepted dead-test finding, add a subsection under `## Findings` whose heading is the exact test path. The subsection must include these fields with actual values: `Type`, `Risk`, `Subject under test`, `Active glob status`, `Subject evidence`, `Mixed-subject check`, `Calibration modes relied on`, and `Notes`.

Expected: every dead-test finding points to a confirmed-dead code finding or inactive test glob evidence.

### Task 8: Evaluate eligible config entries

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`
- Read: `vitest.config.ts`
- Read: `playwright.config.ts`
- Read: `tsconfig.json`
- Read: `eslint.config.mjs`
- Read: `package.json`

- [ ] **Step 1: Prefer tool-resolved config where available**

Run:

```bash
npx vitest --help >/tmp/hybro-vitest-help.txt 2>&1 || true
npx playwright --help >/tmp/hybro-playwright-help.txt 2>&1 || true
sed -n '1,120p' /tmp/hybro-vitest-help.txt
sed -n '1,120p' /tmp/hybro-playwright-help.txt
```

Expected: records whether the tools expose a useful resolved-config command. If they do not, state that config findings depending on tool resolution semantics are excluded.

- [ ] **Step 2: Check Vitest include/setup entries**

Run:

```bash
find src tests -type f \( -name '*.test.ts' -o -name '*.test.tsx' \) | sort
find tests/setup -type f | sort
```

Expected: compare output to `vitest.config.ts`. Accept only entries whose consumers are fully bounded by the named config files, package scripts, and Vitest conventions.

- [ ] **Step 3: Check Playwright include/setup entries**

Run:

```bash
find tests/e2e -type f | sort
```

Expected: compare output to `playwright.config.ts`. Accept only inactive project/use entries or setup entries that are fully bounded by Playwright config and conventions.

- [ ] **Step 4: Check tsconfig include/path entries**

Run:

```bash
node -e "const ts=require('./tsconfig.json'); console.log(JSON.stringify({paths:ts.compilerOptions?.paths, include:ts.include, exclude:ts.exclude}, null, 2))"
find . -path './node_modules' -prune -o -type f \( -name '*.ts' -o -name '*.tsx' \) -print | sort | sed -n '1,240p'
```

Expected: accept only unreachable include/path entries whose consumer set is fully bounded. Exclude anything that requires editor, CI, generated, or external activation evidence.

- [ ] **Step 5: Add accepted config findings or exclusions**

For each accepted config finding, add a subsection under `## Findings` whose heading is the exact config file and entry name. The subsection must include these fields with actual values: `Type`, `Risk`, `Bounded consumer set checked`, `Positive live probes checked`, `Negative reference command/output`, `External activation handling`, and `Notes`.

Expected: if external activation sources are needed, record the item under `## Excluded`, not `## Findings`.

### Task 9: Reconcile the candidate universe and write exclusions

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`

- [ ] **Step 1: Add candidate universe summary**

Under `## Candidate Universe`, add a summary table with these columns: `Category`, `Total scanned`, `Live`, `Accepted findings`, `Excluded`, and `Unsupported`. Include rows for `Source files`, `Exports`, `Tests`, and `Eligible config entries`. Fill every cell with the actual count from the audit; if export analysis was unsupported, put `unsupported` in the export cells that cannot be counted.

Expected: every candidate-scope item is accounted for as live, accepted finding, excluded, or unsupported.

- [ ] **Step 1a: Populate the row-level reconciliation table**

Under `### Reconciliation Log`, add one row for every candidate-scope source file, test file, test-support file, eligible config entry, and export listed in `/tmp/hybro-export-universe.tsv`. If export analysis was not calibrated, each export row gets outcome `unsupported by analyzer`.

Expected: the row-level table is the deliverable for the no-silent-skips invariant. `/tmp` files and command-output references can support the table, but they do not replace it.

- [ ] **Step 2: Write notable exclusions**

For each notable suspicious item that was not accepted, add a subsection under `## Excluded` whose heading is the exact path or config entry. The subsection must include these fields with actual values: `Reason excluded`, `Evidence checked`, and `Required evidence to promote later`.

Expected: exclusions explain why the report remains high-confidence rather than exhaustive.

- [ ] **Step 3: Add empty-findings statement if needed**

If no findings were accepted, add this under `## Findings`:

```markdown
No high-confidence dead-code, dead-test, or eligible dead-config findings were accepted under this audit's evidence standards.
```

Expected: the report remains complete even with zero accepted findings.

### Task 10: Final no-action verification

**Files:**
- Modify: `docs/DEAD_CODE_INVENTORY_AUDIT.md`

- [ ] **Step 1: Capture post-audit status**

Run:

```bash
git status --short
```

Expected: output shows `docs/DEAD_CODE_INVENTORY_AUDIT.md` plus any pre-existing unrelated dirty files. Add the complete output under `## No-Action Evidence`.

- [ ] **Step 2: Verify only the report was modified by this audit**

Run:

```bash
git diff --name-only
git diff --check -- docs/DEAD_CODE_INVENTORY_AUDIT.md
git status --porcelain=v1 -z > /tmp/hybro-post-audit-status.z
while IFS= read -r file; do
  { git diff --binary -- "$file"; git diff --cached --binary -- "$file"; } | shasum -a 256
done < /tmp/hybro-pre-audit-dirty-files.txt > /tmp/hybro-post-audit-tracked-hashes.txt
git ls-files --others --exclude-standard -z | grep -zv '^docs/DEAD_CODE_INVENTORY_AUDIT.md$' | xargs -0 shasum -a 256 > /tmp/hybro-post-audit-untracked-hashes.txt 2>/dev/null || true
diff -u /tmp/hybro-pre-audit-tracked-hashes.txt /tmp/hybro-post-audit-tracked-hashes.txt
diff -u /tmp/hybro-pre-audit-untracked-hashes.txt /tmp/hybro-post-audit-untracked-hashes.txt
```

Expected: `git diff --check` exits 0. `git diff --name-only` may include pre-existing unrelated files, but the pre/post hash diffs must show pre-existing tracked and untracked dirty files were not changed by the audit. The only new audit-created path should be `docs/DEAD_CODE_INVENTORY_AUDIT.md`.

- [ ] **Step 3: Search the report for placeholders**

Run:

```bash
rg -n "T[B]D|T[O]DO|P[e]nding|\\x3c[A-Za-z]" docs/DEAD_CODE_INVENTORY_AUDIT.md
```

Expected: no output. If there is output, replace placeholders with actual evidence or remove the incomplete candidate.

- [ ] **Step 4: Self-review against the design**

Run:

```bash
sed -n '1,260p' docs/superpowers/specs/2026-05-30-dead-code-inventory-design.md
sed -n '1,320p' docs/DEAD_CODE_INVENTORY_AUDIT.md
```

Expected: every design requirement is reflected in the report: scope, method, manifest, calibration, candidate universe, findings, exclusions, no-action evidence, no dependency audit, and no deletion steps.

- [ ] **Step 5: Leave the report uncommitted unless separately requested**

Run:

```bash
git status --short
```

Expected: `docs/DEAD_CODE_INVENTORY_AUDIT.md` remains the only audit-created file. Do not stage or commit anything as part of this plan unless the user separately requests a commit.
