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

This pass includes:

1. Replace non-legacy `config.settings` imports with `common.config.settings`
   where behavior is equivalent.
2. Remove type-only `services.*` imports from
   `execution/dispatch/response_handler.py` by using protocols or local
   structural types.
3. Review `scripts/backfill_domain_aliases.py` and
   `scripts/reupsert_agents_pinecone.py`.
   - If a script can safely use DAL, Common, or module APIs without changing
     operational behavior, migrate it.
   - If a script still needs a legacy singleton, keep it and mark it as an
     explicit script blocker in the cleanup manifest.
4. Update `tests/fixtures/phase9_cleanup_manifest.json` so package blockers are
   split into smaller categories:
   - app-shell runtime blockers
   - operational script blockers
   - test-only compatibility blockers
   - external decommission evidence blockers
5. Keep the existing Phase 9 gates passing and make any new gate failures point
   to a specific remaining blocker rather than a broad package-level statement.

This pass excludes:

- Cross-repo scans of frontend, agents, docs, or deployed workers.
- Route traffic analysis.
- Production collection backup, deletion, or rollback execution.
- Broad deletion of `services/`, `modules/`, `config/`, or `infrastructure/`.
- Public API shape changes.
- Startup behavior changes that are not covered by focused tests.

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

### Type Boundary

Execution code should not depend on concrete legacy service modules for type
annotations. Where only a few methods are needed, use local `Protocol` classes
or existing Common protocols.

This avoids turning type hints into cleanup blockers and makes later runtime
dependency changes easier.

### Script Boundary

Operational scripts are treated separately from app runtime. A script import of
`services.*` should not be hidden inside a broad package blocker. Each script
should either:

- use a non-legacy API and leave the blocker list, or
- remain explicitly listed as an operational script blocker with a short reason.

### Manifest Boundary

The cleanup manifest is the source of truth for deferred cleanup. After this
pass, it should distinguish between blockers that require engineering work in
this repository and blockers that require product or operations evidence outside
this repository.

## Data Flow

There is no new runtime data flow.

For config imports, callers read the same `settings` object through
`common.config.settings` instead of the legacy re-export path.

For type-only service references, runtime behavior is unchanged because the
imports only existed under `TYPE_CHECKING`.

For operational scripts, migrated scripts must preserve their existing database,
Pinecone, and logging behavior. Scripts that cannot be safely migrated remain on
their current path and are documented as blockers.

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

Primary verification:

```bash
uv run pytest -q tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py
```

Focused verification should include the tests closest to touched code:

- config/common foundation tests if settings imports change;
- execution dispatch tests if `response_handler.py` type boundaries change;
- import smoke checks for modified scripts;
- any existing tests named in the failure output from the Phase 9 gates.

Final verification should include:

```bash
uv run ruff check .
```

Run the full test suite if the focused pass is clean and the runtime changes are
broad enough to justify the time. If full `pytest` is not run, report that
explicitly.

## Acceptance Criteria

- Non-legacy config callers touched in this pass import `common.config.settings`
  instead of `config.settings`.
- `execution/dispatch/response_handler.py` has no type-only imports from
  `services.*`.
- The operational script status is explicit: migrated where safe, otherwise
  documented as script blockers.
- `tests/fixtures/phase9_cleanup_manifest.json` separates app-shell runtime,
  operational script, test-only, and external decommission blockers.
- Existing Phase 9 cleanup and API thin-adapter gates pass.
- Ruff passes for the changed files, and preferably for the whole repo.

## Risks

- `main.py` startup binding imports are deeply connected to compatibility
  facades. This pass should not attempt a broad rewrite without a dedicated
  startup-binding plan.
- Operational scripts may rely on legacy singleton initialization semantics.
  Migrating them without preserving connection setup could break maintenance
  workflows.
- Manifest precision can create noisy diffs. Keep it focused on paths touched
  or newly classified by this pass.

## Follow-Up Work

After this pass, the next repo-local cleanup candidates are:

- migrate app-shell startup bindings out of legacy `services.*` and `modules.*`;
- migrate tests that only verify compatibility shims to module facade or Common
  protocol behavior;
- replace legacy Redis and relay infrastructure imports in `main.py` with
  app-shell or DAL-owned construction helpers;
- remove the `config/` compatibility package once all imports use
  `common.config`.
