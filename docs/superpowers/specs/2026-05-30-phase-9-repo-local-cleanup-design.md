# Phase 9 Repo-Local Cleanup Design

## Goal

Reduce repo-local Phase 9 cleanup blockers without claiming full legacy workflow
decommission. This pass should remove or narrow safe in-repository references to
legacy compatibility packages, then update the cleanup manifest so the remaining
blockers are concrete and actionable.

The work is intentionally conservative: it should make `services/`, `modules/`,
`config/`, and `infrastructure/` easier to remove later, but it must not delete
large legacy packages, drop collections, or depend on evidence from other repos
or production traffic.

## Current Context

The existing Phase 9 gates pass:

- `tests/test_phase9_cleanup_gate.py`
- `tests/test_api_thin_adapters.py`

Those gates pass because `tests/fixtures/phase9_cleanup_manifest.json` records
the remaining blockers. The most important repo-local blockers are:

- `main.py` imports many `services.*` and `modules.*` compatibility objects for
  app-shell startup binding.
- `main.py` still imports legacy `infrastructure.*` helpers for Redis service,
  leader election, and relay streams.
- `container.py`, `database/mongodb.py`, `main.py`, and
  `scripts/_discovery_client.py` import `config.settings`, even though
  `config/settings.py` is only a re-export of `common.config.settings`.
- `execution/dispatch/response_handler.py` imports `services.*` types under
  `TYPE_CHECKING`; these are not runtime dependencies but still appear in static
  cleanup accounting.
- `scripts/backfill_domain_aliases.py` and
  `scripts/reupsert_agents_pinecone.py` import `services.*` singletons.
- Many tests still directly exercise `services.*` and `modules.*` compatibility
  paths.

## Scope

### Current Review Loop Change Control

During the design review loop, only this design document may be edited.
Implementation files, tests, and `tests/fixtures/phase9_cleanup_manifest.json`
are deferred until the design is accepted and an implementation plan is created.

The later implementation pass includes:

1. Replace non-legacy `config.settings` imports with `common.config.settings`
   where behavior is equivalent. The initial required caller set is
   `container.py`, `database/mongodb.py`, `main.py`, and
   `scripts/_discovery_client.py`; any caller in this set that cannot be safely
   migrated must be listed as an explicit manifest blocker with the reason.
2. Remove type-only `services.*` imports from
   `execution/dispatch/response_handler.py` by using protocols or local
   structural types.
3. Review `scripts/backfill_domain_aliases.py` and
   `scripts/reupsert_agents_pinecone.py`.
   - If a script can safely use DAL, Common, or module APIs without changing
     operational behavior, migrate it.
   - If a script still needs a legacy singleton, keep it and mark it as an
     explicit script blocker in the cleanup manifest.
4. Update `tests/fixtures/phase9_cleanup_manifest.json` so package-level
   removal blockers remain present while finer per-package evidence is added for:
   - app-shell runtime blockers
   - operational script blockers
   - test-only compatibility blockers
   - external decommission evidence blockers
   External decommission blockers may only preserve or reclassify evidence
   already present in this repository's manifest. The later implementation pass
   must not collect, infer, or assert new cross-repo, production traffic, or
   deployment evidence.
5. Keep the existing Phase 9 gates passing. Package-level removal blockers must
   remain for still-shipped legacy packages, but any import-boundary or
   regression failure should cite the specific path/import evidence that explains
   the still-blocked package.

The later implementation pass excludes:

- Cross-repo scans of frontend, agents, docs, or deployed workers.
- Route traffic analysis.
- Production collection backup, deletion, or rollback execution.
- Broad deletion of `services/`, `modules/`, `config/`, or `infrastructure/`.
- Public API shape changes.
- Startup behavior changes that are not covered by focused tests.
- In `main.py`, any intentional behavioral change beyond migrating
  `config.settings` to `common.config.settings`. This pass must not change the
  AST-level `services.*`, `modules.*`, or `infrastructure.*` startup/shutdown
  import sets or binding logic. Those imports remain manifest blockers and
  follow-up work.

## Implementation File Inventory

Allowed implementation write set:

| Purpose | Files |
| --- | --- |
| Config import migration | `container.py`, `database/mongodb.py`, `main.py`, `scripts/_discovery_client.py` |
| Type-only service import cleanup | `execution/dispatch/response_handler.py` |
| Operational script migration, only if chosen | `scripts/backfill_domain_aliases.py`, `scripts/reupsert_agents_pinecone.py` |
| Cleanup manifest precision | `tests/fixtures/phase9_cleanup_manifest.json` |
| Existing cleanup gates | `tests/test_phase9_cleanup_gate.py`, `tests/test_api_thin_adapters.py` |
| Focused config tests | `tests/test_common_foundation.py` if existing settings assertions need extension |
| New focused script tests, if scripts migrate | New files such as `tests/test_backfill_domain_aliases.py` and `tests/test_reupsert_agents_pinecone.py`, or equivalent checked-in focused tests |

Deferred or read-only unless a later approved plan expands scope:

- `services/**`
- `modules/**`
- `infrastructure/**`
- `config/settings.py` and `config/__init__.py`
- non-target tests, except when an existing focused test must be updated to keep
  assertions aligned with the changed import boundary

## Architecture

The cleanup keeps the current app architecture intact. It only narrows
compatibility dependencies at the edges.

### Configuration Boundary

`common.config.settings` is the canonical settings module. The legacy
`config.settings` package remains only as a compatibility shim for code that has
not yet migrated.

Non-legacy code should import from `common.config.settings` directly. Legacy
packages may continue importing `config.settings` until their larger removal is
planned.

Config migration is behavior-equivalent only if callers use the same canonical
objects and helper functions exported by `common.config.settings`. The
implementation must not instantiate a second `Settings` object, change settings
initialization timing, remove helper exports, or delete the `config.settings`
shim in this pass.

### Type Boundary

Execution code should not depend on concrete legacy service modules for type
annotations. Where only a few methods are needed, use local `Protocol` classes
or existing Common protocols.

This avoids turning type hints into cleanup blockers and makes later runtime
dependency changes easier.

For `execution/dispatch/response_handler.py`, the local structural types should
cover the methods the handler actually calls rather than importing
`services.database_service.DatabaseService` or `services.sse_services.SSEManager`.
The database-facing structural type should be derived from direct method calls in
the current handler and should include:

- `accumulate_artifact_on_message`
- `update_task_state_on_message`
- `get_pending_continuation_on_message`
- `get_room_agent_message_by_message_id`
- `get_room_by_room_id`

The SSE-facing structural type should include:

- `send_artifact_update`
- `send_task_submitted`
- `send_task_update`
- `send_processing_status`

Optional or duck-typed behavior, such as methods reached through `getattr`,
should remain untyped or use a narrow optional helper type only if needed for
static checking. The implementation should only replace constructor annotations
with local protocol or structural names, keep `from __future__ import
annotations`, avoid `runtime_checkable`, avoid `isinstance` or runtime
validation, avoid new adapters or wrappers, and add no non-typing imports from
`services.*`.

### Script Boundary

Operational scripts are treated separately from app runtime. A script import of
`services.*` should not be hidden inside a broad package blocker. Each script
should either:

- use a non-legacy API and leave the blocker list, or
- remain explicitly listed as an operational script blocker with a short reason.

Each script decision must name the exact legacy import path, the replacement API
when migrated, and a parity note for the behaviors the script owns. For these
scripts, the parity note must cover database access, Pinecone writes where
applicable, logging behavior, and connection cleanup.

Script verification must be mocked and repo-local. It must not require real
MongoDB, Pinecone, OpenAI, network access, deployment state, or production
credentials. Migrated scripts must preserve CLI flags and prompts, connection
setup and cleanup, best-effort per-record failure handling, and Pinecone upsert
metadata shape where applicable.

Initial script decision matrix:

| Script | Current legacy import | Owned behaviors | Candidate replacement API | Migration decision criteria | Required test if migrated | Blocker fields if not migrated |
| --- | --- | --- | --- | --- | --- | --- |
| `scripts/backfill_domain_aliases.py` | `services.domain_alias_service.domain_alias_service` | CLI dry-run/force flow, agent query, public URL generation, agent update, logging, Mongo connection lifecycle | Prefer a non-legacy domain-alias helper or repository-backed function if one already exists; otherwise keep blocked | Migrate only if URL generation and update semantics match without importing `services.*` or constructing live clients in tests | `tests/test_backfill_domain_aliases.py` | exact import path, reason replacement API is not available or not parity-safe, required-before-remove action, database/logging/cleanup parity note |
| `scripts/reupsert_agents_pinecone.py` | `services.database_service.db_service` | Mongo connection lifecycle, agent enumeration, OpenAI embedding call, Pinecone upsert metadata shape, per-agent best-effort failure logging | Prefer DAL/vector/LLM gateway APIs if they preserve script behavior without legacy singleton initialization | Migrate only if mocked tests prove connection lifecycle, embedding delegation, Pinecone metadata shape, and per-agent failure handling | `tests/test_reupsert_agents_pinecone.py` | exact import path, reason replacement API is not available or not parity-safe, required-before-remove action, database/Pinecone/logging/cleanup parity note |

### Manifest Boundary

The cleanup manifest is the source of truth for deferred cleanup. After this
pass, it should distinguish between blockers that require engineering work in
this repository and blockers that require product or operations evidence outside
this repository.

Manifest precision must not weaken the existing package-removal gates. While
`services`, `modules`, `config`, or `infrastructure` are still shipped packages,
the manifest must keep package-level removal blockers for those packages. Finer
categories should live in `package_removal_checklist` or equivalent per-package
evidence fields, with runtime and test import inventories regenerated from
repo-local scans. The implementation must not add broad path blockers or broad
`legacy_import_boundary` exceptions that hide new violations.

The app-shell runtime blocker category must enumerate exact `main.py` legacy
import paths by package and purpose, including startup and shutdown imports from
`services.*`, `modules.*`, and `infrastructure.*`. Each entry should include a
required-before-remove action. Repo-local AST or import inventory checks should
prove these imports were reclassified or preserved as blockers rather than
silently removed or hidden.

The manifest records classification, exact paths and imports, blocker reasons,
current repo-local inventories, and repo-local required-before-remove actions.
Behavioral claims belong in tests. The manifest may reference relevant test
names, but it must not replace assertions for CLI compatibility, cleanup
behavior, best-effort failure handling, or Pinecone metadata parity with prose.

External decommission entries are classification-only in this pass. They may be
copied, renamed, or moved from existing manifest content, but the implementation
must not add new external-evidence claims, readiness conclusions, traffic or
deployment assertions, or requirements to collect external proof.
Existing records of missing external prerequisites may remain as deferred,
non-actionable blockers for this pass; satisfying those prerequisites is not part
of repo-local gate satisfaction, and this pass must not add new action
requirements to collect that external proof.

Target manifest inventory shape:

- `blocked_cleanup`: keep package-level removal blockers for each still-shipped
  legacy package. Entries should remain package-scoped and should not become
  broad import-boundary allowlists.
- `package_removal_checklist`: for each legacy package, record `runtime_import_files`,
  `test_import_files`, `runtime_blockers`, `test_blockers`,
  `required_before_remove`, and a status.
- App-shell runtime evidence: include exact `main.py` import paths grouped by
  package and purpose, generated from a repo-local AST import scan.
- Operational script evidence: include exact script path, legacy import path,
  migration status, blocker reason when blocked, replacement API when known, and
  focused test names when migrated.
- Test-only evidence: include test files importing each legacy package, generated
  from a repo-local AST import scan.
- External decommission evidence: preserve only existing missing-prerequisite
  records and mark them deferred/non-actionable for this pass.

## Data Flow

There is no new runtime data flow.

For config imports, callers read the same `settings` object through
`common.config.settings` instead of the legacy re-export path.

For type-only service references, runtime behavior is unchanged because the
imports only existed under `TYPE_CHECKING`. The implementation must preserve
that by changing annotations only; it must not introduce runtime checks,
wrappers, adapters, or new service imports.

For operational scripts, migrated scripts must preserve their existing database,
Pinecone, and logging behavior. Scripts that cannot be safely migrated remain on
their current path and are documented as blockers.

## Implementation Sequencing

The implementation should use this order to avoid stale manifest evidence or
broken intermediate states:

1. Capture the baseline repo-local import inventory and confirm the current
   Phase 9 gates pass.
2. Add or run the `main.py` AST/import inventory guard before or alongside the
   config migration. The guard should be a checked-in pytest assertion,
   preferably in `tests/test_phase9_cleanup_gate.py`, comparing the current
   `services.*`, `modules.*`, and `infrastructure.*` import sets against an
   explicit baseline or manifest-backed expected inventory. The checkpoint is
   that `main.py` compiles, and any controlled import smoke test is only used
   when repo-local monkeypatching makes import side effects safe. Its legacy
   startup/shutdown import sets must remain unchanged.
3. Migrate eligible config imports and run the config import smoke/foundation
   checks.
4. Replace `response_handler.py` type-only service imports with local structural
   annotations and run the focused dispatch/type-boundary tests.
5. Decide each operational script independently. A script may be reclassified as
   resolved only after its focused mocked parity tests pass. Otherwise, keep its
   existing `services.*` import and record it as an operational script blocker
   with exact path and reason.
6. Regenerate and update the cleanup manifest from the final repo-local
   inventory. Do this after code and focused tests settle so the manifest cannot
   hide active violations or preserve stale counts.
7. Run Phase 9 cleanup gates, API thin-adapter gates, focused tests added in
   this pass, and the repo-local Ruff no-new-debt gate described below.

## Error Handling

Config import migration should preserve existing import errors and environment
loading behavior because the target module owns the same `Settings` instance.

Script migration must preserve existing operational failure modes:

- database connection failure should still fail the script clearly;
- per-record processing failures should keep the existing best-effort behavior;
- cleanup and connection closing should still run in `finally` blocks.

Manifest updates must not silence a real cleanup violation. If a blocker remains,
it should be recorded with the exact path and reason.

## Testing

Required cleanup-gate verification:

```bash
uv run pytest -q tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py
```

Focused verification should include the tests closest to touched code:

- `uv run pytest -q tests/test_common_foundation.py -k settings` if settings
  imports change;
- `uv run pytest -q tests/test_agent_response_handler.py` if
  `response_handler.py` type boundaries change;
- checked-in pytest assertions, preferably in `tests/test_phase9_cleanup_gate.py`
  or a focused new test file, proving:
  - `execution/dispatch/response_handler.py` contains no `import services...`
    or `from services...` statements, including inside `TYPE_CHECKING` blocks;
  - `main.py` only changes the `config.settings` import and does not change
    legacy `services.*`, `modules.*`, or `infrastructure.*` startup/shutdown
    import sets;
  - `container`, `database.mongodb`, `main`, and `scripts._discovery_client`
    compile after config import migration, with controlled import smoke checks
    only when repo-local monkeypatching makes import side effects safe;
- if operational scripts are migrated, checked-in mocked repo-local tests such as
  `tests/test_backfill_domain_aliases.py`,
  `tests/test_reupsert_agents_pinecone.py`, or equivalent focused tests. If those
  files are created, run
  `uv run pytest -q tests/test_backfill_domain_aliases.py tests/test_reupsert_agents_pinecone.py`;
- any existing tests named in the failure output from the Phase 9 gates.

Script tests must monkeypatch external clients and fail if they construct
unmocked MongoDB, Pinecone, OpenAI, network, deployment, or production
credential dependencies.

Add any new focused test files for config smoke checks, AST inventory checks, or
migrated scripts to that command or run them separately before final verification.

Final verification should include the focused Ruff gate for files whose Ruff
status is intentionally changed by this pass:

```bash
uv run --with ruff ruff check execution/dispatch/response_handler.py
```

Whole-repo Ruff is an informational no-new-debt check for this pass, not a
cleanup target. If `uv run --with ruff ruff check .` fails because of preexisting
repo-wide lint debt, compare current results with the implementation baseline and
report the residual debt instead of expanding this repo-local cleanup pass into a
full lint cleanup. The implementation must not increase the whole-repo Ruff error
count or introduce new Ruff diagnostics in the files changed by this pass, except
for a documented suppression of a baseline diagnostic that is needed to keep the
runtime diff inside this plan's scope.

Run the full test suite if the focused pass is clean and the runtime changes are
broad enough to justify the time. If full `pytest` is not run, report that
explicitly.

## Acceptance Criteria

These are implementation acceptance criteria for the later cleanup pass. During
the current design review loop, no implementation files, tests, or manifest
files are edited.

- `container.py`, `database/mongodb.py`, `main.py`, and
  `scripts/_discovery_client.py` import `common.config.settings` instead of
  `config.settings`, or each unmigrated caller is recorded in the cleanup
  manifest with an exact reason.
- Config migration preserves the canonical `common.config.settings` exports,
  does not instantiate a second `Settings`, does not change initialization
  timing, and does not delete the `config.settings` compatibility shim.
- `execution/dispatch/response_handler.py` has no type-only imports from
  `services.*`.
- `execution/dispatch/response_handler.py` uses local structural type
  annotations for its database and SSE dependencies, without runtime validation,
  adapters, wrappers, `runtime_checkable`, or new non-typing `services.*`
  imports.
- The operational script status is explicit for
  `scripts/backfill_domain_aliases.py` and
  `scripts/reupsert_agents_pinecone.py`: each script either has no remaining
  `services.*` imports and has mocked repo-local parity tests, or remains listed
  in the manifest as an operational script blocker with exact import path,
  reason, required-before-remove action, replacement API if known, and a parity
  note for database, Pinecone where applicable, logging, and cleanup behavior.
- Any migrated operational script has mocked repo-local verification for CLI
  compatibility, connection setup and cleanup, best-effort per-record failure
  handling, and Pinecone upsert metadata shape where applicable.
- `tests/fixtures/phase9_cleanup_manifest.json` separates app-shell runtime,
  operational script, test-only, and external decommission blockers.
- Package-level removal blockers remain present for any still-shipped legacy
  package (`services`, `modules`, `config`, or `infrastructure`), finer
  categories live in per-package checklist or evidence fields, and the manifest
  adds no broad path blockers or broad `legacy_import_boundary` exceptions that
  hide violations.
- App-shell runtime blockers enumerate exact `main.py` legacy import paths by
  package and purpose, with required-before-remove actions, and repo-local
  inventory evidence shows this pass did not alter legacy startup/shutdown
  import sets beyond the config import migration.
- External decommission entries are only copied, renamed, or reclassified from
  existing manifest content; this pass introduces no new external-evidence
  claims, readiness conclusions, traffic or deployment assertions, or
  requirements to collect external proof.
- Existing Phase 9 cleanup and API thin-adapter gates pass.
- `uv run --with ruff ruff check execution/dispatch/response_handler.py` passes.
- Whole-repo Ruff has no new debt relative to the captured implementation
  baseline. If full `uv run --with ruff ruff check .` still fails, the remaining
  failures must be identified as preexisting repo-wide debt and must not be used
  to broaden this pass into a full lint cleanup.

## Risks

- Risk: `main.py` startup binding imports are deeply connected to compatibility
  facades.
  Mitigation: this pass only changes the `config.settings` import in `main.py`;
  all `services.*`, `modules.*`, and `infrastructure.*` startup/shutdown imports
  remain blockers for a later startup-binding plan.
  Verification: checked-in AST or import inventory tests prove the legacy import
  sets were not changed.
- Risk: operational scripts may rely on legacy singleton initialization
  semantics.
  Mitigation: migrate a script only when the replacement preserves CLI behavior,
  connection setup and cleanup, per-record failure handling, and write shape; if
  not, keep the script as an explicit blocker.
  Verification: migrated scripts have mocked repo-local parity tests that fail on
  unmocked external client construction.
- Risk: manifest restructuring can weaken package-removal gates or hide
  violations behind broad exceptions.
  Mitigation: keep package-level blockers for still-shipped legacy packages,
  store finer categories in per-package evidence, and reject broad path blockers
  or broad `legacy_import_boundary` exceptions.
  Verification: Phase 9 cleanup gate assertions pass and cover those manifest
  invariants.
- Risk: manifest precision can create noisy diffs.
  Mitigation: limit manifest changes to paths touched or newly classified by
  this pass, and regenerate runtime/test inventories from repo-local scans.
  Verification: diff review shows classification changes are path-specific and
  backed by inventory evidence.

## Follow-Up Work

After this pass, the next repo-local cleanup candidates are:

- migrate app-shell startup bindings out of legacy `services.*` and `modules.*`;
- migrate tests that only verify compatibility shims to module facade or Common
  protocol behavior;
- replace legacy Redis and relay infrastructure imports in `main.py` with
  app-shell or DAL-owned construction helpers;
- remove the `config/` compatibility package in a later pass only after manifest
  blockers are cleared, repo-local inventory shows no `config.settings`
  consumers, and compatibility-shim deletion has its own focused test/update
  plan.
