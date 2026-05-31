# Phase 9 Repo-Local Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce repo-local Phase 9 cleanup blockers by migrating safe config imports, removing type-only legacy service imports from `AgentResponseHandler`, and making the cleanup manifest more precise without claiming full legacy workflow decommission.

**Architecture:** Keep the runtime architecture unchanged. Migrate only behavior-equivalent config imports to `common.config.settings`, replace `TYPE_CHECKING` imports in `execution/dispatch/response_handler.py` with local structural typing, and preserve `main.py` legacy startup imports as explicit manifest blockers. Operational scripts remain on legacy services in this pass because no confirmed non-legacy parity API exists yet; they are documented as script blockers.

**Tech Stack:** Python 3.11+, FastAPI app shell, pytest, Ruff, AST-based import boundary tests, existing Phase 9 cleanup manifest.

---

## File Structure

Modify:
- `tests/test_phase9_cleanup_gate.py`: add repo-local cleanup assertions for config imports, `response_handler.py` type-only imports, `main.py` legacy import inventory, and operational script blocker shape.
- `container.py`: import settings helpers from `common.config.settings`.
- `database/mongodb.py`: import `settings` from `common.config.settings`.
- `main.py`: import `settings` from `common.config.settings`; do not change legacy `services.*`, `modules.*`, or `infrastructure.*` imports.
- `scripts/_discovery_client.py`: import `settings` from `common.config.settings`.
- `execution/dispatch/response_handler.py`: replace `TYPE_CHECKING` legacy service imports with local structural `Protocol` types.
- `tests/fixtures/phase9_cleanup_manifest.json`: refresh package removal evidence and add explicit operational script blockers.

Read-only in this pass:
- `services/**`
- `modules/**`
- `infrastructure/**`
- `config/settings.py`
- `config/__init__.py`
- `scripts/backfill_domain_aliases.py`
- `scripts/reupsert_agents_pinecone.py`

The two operational scripts are intentionally left unchanged and documented as blockers. A later pass can migrate them after identifying parity-safe non-legacy APIs and adding mocked script tests.

## Task 1: Add Repo-Local Cleanup Gates

**Files:**
- Modify: `tests/test_phase9_cleanup_gate.py`
- Reference: `docs/superpowers/specs/2026-05-30-phase-9-repo-local-cleanup-design.md`
- Reference: `tests/fixtures/phase9_cleanup_manifest.json`

- [ ] **Step 0: Capture the implementation baseline commit**

Run:

```bash
BASE_SHA_FILE="$(git rev-parse --git-path phase9-base-sha)"
git rev-parse HEAD > "$BASE_SHA_FILE"
cat "$BASE_SHA_FILE"
```

Expected: prints the commit SHA before this implementation starts and persists it under Git's metadata directory for Task 5 changed-file verification.

- [ ] **Step 1: Add AST helpers and expected legacy import inventory**

Append these helpers near the existing import helper functions in `tests/test_phase9_cleanup_gate.py`, after `_imports_package`:

```python
def _import_modules_including_type_checking(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return sorted(modules)


def _import_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[str] = []
    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
    for node in ast.walk(tree):
        if hasattr(node, "lineno") and node.lineno in type_checking_lines:
            continue
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return sorted(modules)


EXPECTED_MAIN_LEGACY_IMPORTS = {
    "services": [
        "services.a2a_service",
        "services.agent_capability_issue_service",
        "services.agent_health_service",
        "services.agent_liveness_service",
        "services.agent_matcher",
        "services.agent_resolver_service",
        "services.agent_selection_service",
        "services.agent_service",
        "services.compaction_service",
        "services.context_assembly_service",
        "services.database_service",
        "services.debate_service",
        "services.hitl_service",
        "services.memory_search_service",
        "services.memory_service",
        "services.notification_service",
        "services.openai_service",
        "services.relay_service",
        "services.room_coordinator_service",
        "services.room_membership_source",
        "services.room_services",
        "services.room_supervisor_service",
        "services.run_command_handler",
        "services.run_metrics",
        "services.s3_service",
        "services.sse_services",
        "services.task_notification_service",
        "services.task_service",
    ],
    "modules": [
        "modules.AgentCenter",
        "modules.InspectionCenter",
        "modules.MemoryCenter",
        "modules.RoomCenter",
        "modules.RoomMessageCenter",
    ],
    "infrastructure": [
        "infrastructure.leader_election",
        "infrastructure.redis_service",
        "infrastructure.relay_streams",
    ],
}


EXTERNAL_DECOMMISSION_FORBIDDEN_TERMS = (
    "traffic evidence collected",
    "deployment evidence collected",
    "ready to delete",
    "ready=true",
)
```

- [ ] **Step 2: Add failing tests for config migration and main import inventory**

Append these tests near the other Phase 9 cleanup tests in `tests/test_phase9_cleanup_gate.py`:

```python
def test_repo_local_config_callers_use_common_config_settings():
    expected_callers = {
        Path("container.py"),
        Path("database/mongodb.py"),
        Path("main.py"),
        Path("scripts/_discovery_client.py"),
    }
    violations: list[str] = []
    for path in sorted(expected_callers):
        modules = _import_modules_including_type_checking(path)
        if "config.settings" in modules:
            violations.append(f"{path}: still imports config.settings")
        if "common.config.settings" not in modules:
            violations.append(f"{path}: missing common.config.settings import")

    assert not violations, "Repo-local config callers are not migrated:\n" + "\n".join(
        violations
    )


def test_main_legacy_startup_import_inventory_is_preserved():
    modules = _import_modules(Path("main.py"))
    actual = {
        package: sorted(
            {
                module
                for module in modules
                if module == package or module.startswith(f"{package}.")
            }
        )
        for package in EXPECTED_MAIN_LEGACY_IMPORTS
    }

    assert actual == EXPECTED_MAIN_LEGACY_IMPORTS
```

- [ ] **Step 3: Run new gates and confirm expected failures**

Run:

```bash
uv run pytest -q \
  tests/test_phase9_cleanup_gate.py::test_repo_local_config_callers_use_common_config_settings \
  tests/test_phase9_cleanup_gate.py::test_main_legacy_startup_import_inventory_is_preserved
```

Expected:
- `test_repo_local_config_callers_use_common_config_settings` fails because the four caller files still import `config.settings`.
- `test_main_legacy_startup_import_inventory_is_preserved` passes.

- [ ] **Step 4: Keep the red gates uncommitted**

Run:

```bash
git status --short
```

Expected: `tests/test_phase9_cleanup_gate.py` is modified and uncommitted. Do not commit the intentionally failing gates as a standalone commit. The test changes should be committed with the first passing slice that makes each new gate green.

## Task 2: Migrate Safe Config Imports

**Files:**
- Modify: `container.py`
- Modify: `database/mongodb.py`
- Modify: `main.py`
- Modify: `scripts/_discovery_client.py`
- Test: `tests/test_phase9_cleanup_gate.py`
- Test: `tests/test_common_foundation.py`

- [ ] **Step 1: Update `container.py` import**

Replace:

```python
from config.settings import (
    get_memory_search_index_name,
    get_pinecone_index_name,
    settings,
)
```

with:

```python
from common.config.settings import (
    get_memory_search_index_name,
    get_pinecone_index_name,
    settings,
)
```

- [ ] **Step 2: Update `database/mongodb.py` import**

Replace:

```python
from config.settings import settings
```

with:

```python
from common.config.settings import settings
```

- [ ] **Step 3: Update `main.py` import**

Replace:

```python
from config.settings import settings
```

with:

```python
from common.config.settings import settings
```

Do not change any `services.*`, `modules.*`, or `infrastructure.*` imports in `main.py`.

- [ ] **Step 4: Update `scripts/_discovery_client.py` import**

Replace:

```python
from config.settings import settings
```

with:

```python
from common.config.settings import settings
```

- [ ] **Step 5: Run focused config checks**

Run:

```bash
uv run pytest -q \
  tests/test_phase9_cleanup_gate.py::test_repo_local_config_callers_use_common_config_settings \
  tests/test_phase9_cleanup_gate.py::test_main_legacy_startup_import_inventory_is_preserved \
  tests/test_common_foundation.py -k settings
```

Expected:
- `test_repo_local_config_callers_use_common_config_settings` passes.
- `test_main_legacy_startup_import_inventory_is_preserved` passes.
- `tests/test_common_foundation.py -k settings` passes.

- [ ] **Step 6: Commit config migration and the now-passing config/main gates**

Run:

```bash
git add container.py database/mongodb.py main.py scripts/_discovery_client.py tests/test_phase9_cleanup_gate.py
git commit -m "chore: use common config settings in repo-local callers"
```

## Task 3: Remove Type-Only Legacy Service Imports From Response Handler

**Files:**
- Modify: `execution/dispatch/response_handler.py`
- Test: `tests/test_agent_response_handler.py`
- Test: `tests/test_phase9_cleanup_gate.py`

- [ ] **Step 0: Add the failing response-handler import gate**

Append this test near the other Phase 9 cleanup tests in `tests/test_phase9_cleanup_gate.py`:

```python
def test_response_handler_has_no_services_imports_including_type_checking():
    path = Path("execution/dispatch/response_handler.py")
    modules = _import_modules_including_type_checking(path)
    violations = [
        module
        for module in modules
        if module == "services" or module.startswith("services.")
    ]

    assert not violations, (
        "response_handler.py still imports legacy services, including type-only imports:\n"
        + "\n".join(violations)
    )
```

Run:

```bash
uv run pytest -q tests/test_phase9_cleanup_gate.py::test_response_handler_has_no_services_imports_including_type_checking
```

Expected: FAIL because `response_handler.py` still imports `services.database_service` and `services.sse_services` under `TYPE_CHECKING`.

- [ ] **Step 1: Update typing imports**

In `execution/dispatch/response_handler.py`, replace:

```python
from typing import TYPE_CHECKING, Any
```

with:

```python
from typing import Any, Protocol
```

- [ ] **Step 2: Remove legacy `TYPE_CHECKING` imports**

Delete this block:

```python
if TYPE_CHECKING:
    from services.database_service import DatabaseService
    from services.sse_services import SSEManager
```

- [ ] **Step 3: Add local structural protocols**

Insert this code after the existing imports and before `logger = get_logger(__name__)`. Leave two blank lines between the import block and `_DatabaseServiceLike` so Ruff import/order checks stay clean:

```python
class _DatabaseServiceLike(Protocol):
    async def accumulate_artifact_on_message(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def update_task_state_on_message(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def get_pending_continuation_on_message(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def get_room_agent_message_by_message_id(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def get_room_by_room_id(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...


class _SSEManagerLike(Protocol):
    async def send_artifact_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def send_task_submitted(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def send_task_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    async def send_processing_status(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...
```

Keep `get_pending_hitl_requests_for_message` out of `_DatabaseServiceLike`; the handler reaches it through `getattr`, so it remains duck-typed optional behavior.

- [ ] **Step 4: Update constructor annotations**

Replace:

```python
        db: DatabaseService,
        sse: SSEManager,
```

with:

```python
        db: _DatabaseServiceLike,
        sse: _SSEManagerLike,
```

- [ ] **Step 5: Run response handler checks**

Run:

```bash
uv run pytest -q \
  tests/test_agent_response_handler.py \
  tests/test_phase9_cleanup_gate.py::test_response_handler_has_no_services_imports_including_type_checking
uv run --with ruff ruff check execution/dispatch/response_handler.py
```

Expected:
- `tests/test_agent_response_handler.py` passes.
- `test_response_handler_has_no_services_imports_including_type_checking` passes.
- `uv run --with ruff ruff check execution/dispatch/response_handler.py` exits with status 0.

- [ ] **Step 6: Commit response handler cleanup and its gate**

Run:

```bash
git add execution/dispatch/response_handler.py tests/test_phase9_cleanup_gate.py
git commit -m "chore: remove response handler type-only service imports"
```

## Task 4: Make Operational Script Blockers Explicit

**Files:**
- Modify: `tests/fixtures/phase9_cleanup_manifest.json`
- Test: `tests/test_phase9_cleanup_gate.py`
- Reference: `scripts/backfill_domain_aliases.py`
- Reference: `scripts/reupsert_agents_pinecone.py`

- [ ] **Step 0: Add failing manifest precision gates**

Append these tests near the other Phase 9 cleanup tests in `tests/test_phase9_cleanup_gate.py`:

```python
def test_operational_script_blockers_are_explicit():
    manifest = _manifest()
    blockers = {
        entry.get("path"): entry
        for entry in manifest.get("operational_script_blockers", [])
        if isinstance(entry.get("path"), str)
    }
    expected = {
        "scripts/backfill_domain_aliases.py": "services.domain_alias_service.domain_alias_service",
        "scripts/reupsert_agents_pinecone.py": "services.database_service.db_service",
    }
    violations: list[str] = []

    for path, legacy_import in expected.items():
        entry = blockers.get(path)
        if entry is None:
            violations.append(f"{path}: missing operational script blocker")
            continue
        if entry.get("legacy_import") != legacy_import:
            violations.append(f"{path}: expected legacy_import={legacy_import}")
        if entry.get("package") != "services":
            violations.append(f"{path}: expected package=services")
        if entry.get("status") != "blocked":
            violations.append(f"{path}: expected status=blocked")
        if not entry.get("reason"):
            violations.append(f"{path}: missing reason")
        if not entry.get("required_before_remove"):
            violations.append(f"{path}: missing required_before_remove")
        parity = entry.get("parity_note")
        if not isinstance(parity, dict):
            violations.append(f"{path}: missing parity_note object")
            continue
        for key in ("database", "logging", "cleanup"):
            if not parity.get(key):
                violations.append(f"{path}: missing parity_note.{key}")
        if path == "scripts/reupsert_agents_pinecone.py" and not parity.get("pinecone"):
            violations.append(f"{path}: missing parity_note.pinecone")

    assert not violations, "Operational script blockers are incomplete:\n" + "\n".join(
        violations
    )


def test_app_shell_runtime_blockers_match_main_inventory():
    manifest = _manifest()
    blockers = manifest.get("app_shell_runtime_blockers", [])
    blocker_by_package = {
        entry.get("package"): entry
        for entry in blockers
        if isinstance(entry.get("package"), str)
    }
    violations: list[str] = []

    for package, expected_imports in EXPECTED_MAIN_LEGACY_IMPORTS.items():
        entry = blocker_by_package.get(package)
        if entry is None:
            violations.append(f"{package}: missing app-shell runtime blocker")
            continue
        if entry.get("path") != "main.py":
            violations.append(f"{package}: expected path=main.py")
        if entry.get("purpose") not in {"startup_shutdown_binding", "startup_binding"}:
            violations.append(f"{package}: missing app-shell purpose")
        if entry.get("status") != "blocked":
            violations.append(f"{package}: expected status=blocked")
        if entry.get("legacy_imports") != expected_imports:
            violations.append(f"{package}: legacy_imports do not match main.py inventory")
        if not entry.get("required_before_remove"):
            violations.append(f"{package}: missing required_before_remove")

    assert not violations, "App-shell runtime blockers are incomplete:\n" + "\n".join(
        violations
    )


def test_external_decommission_evidence_remains_deferred_and_repo_local_only():
    manifest = _manifest()
    legacy_workflow = manifest.get("legacy_workflow_decommission", {})
    evidence = legacy_workflow.get("evidence") or []
    violations: list[str] = []

    if legacy_workflow.get("ready") is not False:
        violations.append("legacy_workflow_decommission.ready must remain false")
    for entry in evidence:
        if entry.get("classification") != "blocked_decommission_readiness":
            violations.append("external evidence classification changed")
        if entry.get("status") != "deferred_non_actionable":
            violations.append("external evidence status must be deferred_non_actionable")
        text = json.dumps(entry, sort_keys=True).lower()
        for term in EXTERNAL_DECOMMISSION_FORBIDDEN_TERMS:
            if term in text:
                violations.append(f"external evidence adds forbidden claim: {term}")

    assert not violations, (
        "External decommission evidence is no longer repo-local/deferred:\n"
        + "\n".join(violations)
    )
```

Run:

```bash
uv run pytest -q \
  tests/test_phase9_cleanup_gate.py::test_operational_script_blockers_are_explicit \
  tests/test_phase9_cleanup_gate.py::test_app_shell_runtime_blockers_match_main_inventory \
  tests/test_phase9_cleanup_gate.py::test_external_decommission_evidence_remains_deferred_and_repo_local_only
```

Expected:
- `test_operational_script_blockers_are_explicit` fails because `operational_script_blockers` is not yet in the manifest.
- `test_app_shell_runtime_blockers_match_main_inventory` fails because `app_shell_runtime_blockers` is not yet in the manifest.
- `test_external_decommission_evidence_remains_deferred_and_repo_local_only` fails until the existing external decommission evidence is explicitly marked `deferred_non_actionable`.

- [ ] **Step 1: Add top-level `operational_script_blockers` to the manifest**

In `tests/fixtures/phase9_cleanup_manifest.json`, add this top-level key before `package_removal_checklist`:

```json
  "operational_script_blockers": [
    {
      "legacy_import": "services.domain_alias_service.domain_alias_service",
      "package": "services",
      "parity_note": {
        "cleanup": "Script opens and closes the shared MongoDB connection through database.mongodb.mongodb.",
        "database": "Script queries and updates agent documents with missing public_url values.",
        "logging": "Script logs dry-run and live migration status through common.utils.logger."
      },
      "path": "scripts/backfill_domain_aliases.py",
      "reason": "No repo-local non-legacy domain-alias API has been verified to preserve URL generation and update semantics without the legacy singleton.",
      "required_before_remove": [
        "Extract or identify a non-legacy domain-alias generation API with parity tests.",
        "Add mocked tests covering dry-run, force prompt behavior, Mongo update shape, per-agent failure handling, logging, and connection cleanup.",
        "Remove the services.domain_alias_service import after the replacement API is proven."
      ],
      "replacement_api": null,
      "status": "blocked"
    },
    {
      "legacy_import": "services.database_service.db_service",
      "package": "services",
      "parity_note": {
        "cleanup": "Script opens and closes the MongoDB connection through db_service.mongo.",
        "database": "Script enumerates agents through db_service.get_all_agents.",
        "logging": "Script logs per-agent Pinecone upsert success and failure.",
        "pinecone": "Script writes vectors with metadata type=a2a_agent and agent_id."
      },
      "path": "scripts/reupsert_agents_pinecone.py",
      "reason": "No repo-local non-legacy DAL/vector/LLM gateway combination has been verified to preserve db_service initialization, embedding, and Pinecone upsert behavior for this operational script.",
      "required_before_remove": [
        "Identify a non-legacy agent enumeration, embedding, and vector upsert path with equivalent initialization semantics.",
        "Add mocked tests covering connection lifecycle, embedding delegation, Pinecone vector metadata shape, per-agent best-effort failures, logging, and cleanup.",
        "Remove the services.database_service import after the replacement API is proven."
      ],
      "replacement_api": null,
      "status": "blocked"
    }
  ],
```

- [ ] **Step 2: Update the `services` package checklist runtime blockers**

In the `package_removal_checklist` entry with `"package": "services"`, replace the existing `runtime_blockers` array with:

```json
      "runtime_blockers": [
        "main.py imports legacy service compatibility objects during app-shell startup and shutdown binding.",
        "modules/* compatibility files still import services.* while legacy module shims remain shipped.",
        "scripts/backfill_domain_aliases.py imports services.domain_alias_service.domain_alias_service for public URL generation.",
        "scripts/reupsert_agents_pinecone.py imports services.database_service.db_service for Mongo, embedding, and Pinecone operations."
      ],
```

Keep `runtime_import_files` generated by the existing gate. It should still include the two operational scripts until a later script migration removes those imports.

In the same `services` checklist entry, ensure `required_before_remove` includes this item:

```json
        "Migrate or delete modules/* compatibility paths that still import services.* before removing services from shipped packages."
```

- [ ] **Step 3: Add app-shell runtime blockers to the manifest**

In `tests/fixtures/phase9_cleanup_manifest.json`, add this top-level key before `operational_script_blockers`:

```json
  "app_shell_runtime_blockers": [
    {
      "legacy_imports": [
        "services.a2a_service",
        "services.agent_capability_issue_service",
        "services.agent_health_service",
        "services.agent_liveness_service",
        "services.agent_matcher",
        "services.agent_resolver_service",
        "services.agent_selection_service",
        "services.agent_service",
        "services.compaction_service",
        "services.context_assembly_service",
        "services.database_service",
        "services.debate_service",
        "services.hitl_service",
        "services.memory_search_service",
        "services.memory_service",
        "services.notification_service",
        "services.openai_service",
        "services.relay_service",
        "services.room_coordinator_service",
        "services.room_membership_source",
        "services.room_services",
        "services.room_supervisor_service",
        "services.run_command_handler",
        "services.run_metrics",
        "services.s3_service",
        "services.sse_services",
        "services.task_notification_service",
        "services.task_service"
      ],
      "package": "services",
      "path": "main.py",
      "purpose": "startup_shutdown_binding",
      "required_before_remove": [
        "Replace main.py service singleton binding with container-owned module facade and app-shell dependency providers.",
        "Prove startup and shutdown behavior with focused app-shell tests before removing services from shipped packages."
      ],
      "status": "blocked"
    },
    {
      "legacy_imports": [
        "modules.AgentCenter",
        "modules.InspectionCenter",
        "modules.MemoryCenter",
        "modules.RoomCenter",
        "modules.RoomMessageCenter"
      ],
      "package": "modules",
      "path": "main.py",
      "purpose": "startup_shutdown_binding",
      "required_before_remove": [
        "Replace main.py module compatibility centers with execution, room, agent, and app-shell dependency providers.",
        "Prove route binding and startup behavior before removing modules from shipped packages."
      ],
      "status": "blocked"
    },
    {
      "legacy_imports": [
        "infrastructure.leader_election",
        "infrastructure.redis_service",
        "infrastructure.relay_streams"
      ],
      "package": "infrastructure",
      "path": "main.py",
      "purpose": "startup_shutdown_binding",
      "required_before_remove": [
        "Move Redis service, leader election, and relay stream construction behind app-shell or DAL-owned providers.",
        "Prove startup, shutdown, and health-check behavior before removing infrastructure from shipped packages."
      ],
      "status": "blocked"
    }
  ],
```

- [ ] **Step 4: Mark external decommission evidence deferred and non-actionable**

In `tests/fixtures/phase9_cleanup_manifest.json`, keep the existing `legacy_workflow_decommission.ready` value as `false`. In each existing `legacy_workflow_decommission.evidence` entry, add:

```json
        "status": "deferred_non_actionable"
```

Do not add traffic evidence, deployment evidence, readiness conclusions, or new requirements to collect external proof.

- [ ] **Step 5: Regenerate package-removal checklist inventories**

Run this helper to rewrite `package_removal_checklist` inventories and package-level blocker counts from the current repo-local AST scan:

```bash
uv run python - <<'PY'
import ast
import json
import tomllib
from pathlib import Path

LEGACY_PACKAGES = {"modules", "services", "config", "infrastructure"}
PRODUCTION_ROOTS = (
    "api", "agent", "room", "context_memory", "delivery", "execution",
    "hub_runtime_bridge", "a2a_adapter", "llm_gateway", "platform_module",
    "common", "app_shell", "jobs", "models",
)
PACKAGE_REMOVAL_RUNTIME_ROOTS = (
    "main.py", "container.py", "scripts", "database", *PRODUCTION_ROOTS,
    *tuple(sorted(LEGACY_PACKAGES)),
)

def imports_package(path: Path, package: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    type_checking_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
    for node in ast.walk(tree):
        if hasattr(node, "lineno") and node.lineno in type_checking_lines:
            continue
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        else:
            continue
        if any(module == package or module.startswith(f"{package}.") for module in modules):
            return True
    return False

def runtime_import_files(package: str) -> list[str]:
    files = []
    for root in PACKAGE_REMOVAL_RUNTIME_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            continue
        paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.py"))
        for path in paths:
            if path.parts and path.parts[0] == package:
                continue
            if imports_package(path, package):
                files.append(path.as_posix())
    return sorted(files)

def test_import_files(package: str) -> list[str]:
    return sorted(
        path.as_posix()
        for path in Path("tests").rglob("*.py")
        if imports_package(path, package)
    )

def package_python_file_count(package: str) -> int:
    package_path = Path(package)
    if not package_path.exists():
        return 0
    return len(list(package_path.rglob("*.py")))

def replace_count_line(lines: list[str], marker: str, replacement: str) -> list[str]:
    replaced = False
    updated: list[str] = []
    for line in lines:
        if marker in line:
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(replacement)
    return updated

manifest_path = Path("tests/fixtures/phase9_cleanup_manifest.json")
manifest = json.loads(manifest_path.read_text())
packages = set(tomllib.loads(Path("pyproject.toml").read_text())["tool"]["setuptools"]["packages"])
shipped_legacy = sorted(packages & LEGACY_PACKAGES)
checklist = {
    entry["package"]: entry
    for entry in manifest.get("package_removal_checklist", [])
    if isinstance(entry.get("package"), str)
}
blockers = {
    entry["path"]: entry
    for entry in manifest.get("blocked_cleanup", [])
    if isinstance(entry.get("path"), str) and entry["path"] in LEGACY_PACKAGES
}

for package in shipped_legacy:
    runtime_files = runtime_import_files(package)
    test_files = test_import_files(package)
    entry = checklist[package]
    entry["py_files"] = package_python_file_count(package)
    entry["runtime_import_files"] = runtime_files
    entry["test_import_files"] = test_files
    entry["status"] = "blocked"

    blocker = blockers.get(package)
    if blocker is not None:
        deletion_blockers = blocker.get("deletion_blockers") or []
        deletion_blockers = replace_count_line(
            deletion_blockers,
            "runtime files",
            f"{len(runtime_files)} runtime files still bind or import legacy {package} compatibility paths.",
        )
        deletion_blockers = replace_count_line(
            deletion_blockers,
            "test files",
            f"{len(test_files)} test files still exercise legacy {package} compatibility paths.",
        )
        blocker["deletion_blockers"] = deletion_blockers

manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Updated {manifest_path}")
for package in shipped_legacy:
    entry = checklist[package]
    print(
        f"{package}: {len(entry['runtime_import_files'])} runtime files, "
        f"{len(entry['test_import_files'])} test files"
    )
PY
```

Expected: the helper prints updated counts for `config`, `infrastructure`, `modules`, and `services`. Do not remove package-level `blocked_cleanup` entries while the packages remain shipped. For the `config` checklist entry, the regenerated `runtime_import_files` should no longer include `container.py`, `database/mongodb.py`, `main.py`, or `scripts/_discovery_client.py`. After Task 2, the expected config runtime count is `14`.

- [ ] **Step 6: Run manifest-focused gates and fix stale counts**

Run:

```bash
uv run pytest -q \
  tests/test_phase9_cleanup_gate.py::test_operational_script_blockers_are_explicit \
  tests/test_phase9_cleanup_gate.py::test_app_shell_runtime_blockers_match_main_inventory \
  tests/test_phase9_cleanup_gate.py::test_external_decommission_evidence_remains_deferred_and_repo_local_only \
  tests/test_phase9_cleanup_gate.py::test_shipped_legacy_packages_have_package_removal_checklist_entries \
  tests/test_phase9_cleanup_gate.py::test_legacy_package_blocker_counts_match_package_removal_checklist \
  tests/test_phase9_cleanup_gate.py::test_old_implementation_packages_are_not_shipped_without_blocker
```

Expected: all six tests pass after the manifest inventories and blocker count strings match the current repo-local imports.

- [ ] **Step 7: Commit manifest precision**

Run:

```bash
git add tests/fixtures/phase9_cleanup_manifest.json tests/test_phase9_cleanup_gate.py
git commit -m "chore: document repo-local phase 9 cleanup blockers"
```

## Task 5: Run Full Focused Verification

**Files:**
- Test: `tests/test_phase9_cleanup_gate.py`
- Test: `tests/test_api_thin_adapters.py`
- Test: `tests/test_agent_response_handler.py`
- Test: `tests/test_common_foundation.py`

- [ ] **Step 1: Run cleanup and thin-adapter gates**

Run:

```bash
uv run pytest -q tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py
```

Expected: all tests pass.

- [ ] **Step 2: Run focused behavior tests**

Run:

```bash
uv run pytest -q tests/test_agent_response_handler.py tests/test_common_foundation.py
```

Expected: both files pass.

- [ ] **Step 3: Run Ruff no-new-debt checks**

Run:

```bash
uv run --with ruff ruff check execution/dispatch/response_handler.py
```

Expected: Ruff exits with status 0 for the response-handler cleanup file.

Then run the whole-repo Ruff command as an informational no-new-debt check:

```bash
uv run --with ruff ruff check . --statistics
```

Expected: if this command exits with status 0, report that whole-repo Ruff is
clean. If it exits non-zero because of preexisting repo-wide lint debt, do not
expand this repo-local cleanup pass into a full lint cleanup. Instead, compare
the current error count with the implementation baseline and verify this pass did
not increase the whole-repo Ruff error count or add new Ruff diagnostics in the
implementation diff. A current count less than or equal to the baseline count is
acceptable for this plan when the remaining errors are outside the repo-local
cleanup scope.

- [ ] **Step 4: Inspect changed files**

Run:

```bash
git status --short
BASE_SHA="$(cat "$(git rev-parse --git-path phase9-base-sha)")"
git diff --name-only "$BASE_SHA"...HEAD
```

Expected files changed since the implementation baseline:

```text
container.py
database/mongodb.py
execution/dispatch/response_handler.py
main.py
scripts/_discovery_client.py
tests/fixtures/phase9_cleanup_manifest.json
tests/test_phase9_cleanup_gate.py
```

If additional files changed, stop and identify whether each extra change was created by this implementation task. Undo only task-created extra changes. Never revert preexisting or user-owned changes without explicit approval. If a required edit falls outside the expected list, stop for a new approved design/plan update before continuing.

After the approved verification-scope update that replaced full-repo Ruff with
the no-new-debt gate, these documentation files may also appear in the baseline
diff and should be reported separately from the implementation file list:

```text
docs/superpowers/plans/2026-05-30-phase-9-repo-local-cleanup.md
docs/superpowers/specs/2026-05-30-phase-9-repo-local-cleanup-design.md
```

- [ ] **Step 5: Commit final verification note if needed**

If Task 5 required only test runs and no file changes, do not create a commit. If a small verification fix was required, stage only the allowed files shown by `git status --short` after re-running the relevant verification. For example, if only the Phase 9 gate file changed:

```bash
git add tests/test_phase9_cleanup_gate.py
git commit -m "test: finalize repo-local phase 9 cleanup checks"
```

Use the `git add` command above only when `tests/test_phase9_cleanup_gate.py` was the allowed file that needed a final test fix. If no file changed during Task 5, skip this step.

## Self-Review Checklist

- [ ] The four config callers use `common.config.settings` or are explicitly blocked in the manifest.
- [ ] `config/settings.py` and `config/__init__.py` still exist and still act as compatibility shims.
- [ ] `main.py` legacy `services.*`, `modules.*`, and `infrastructure.*` import sets match `EXPECTED_MAIN_LEGACY_IMPORTS`.
- [ ] `execution/dispatch/response_handler.py` has no `services.*` imports, including under `TYPE_CHECKING`.
- [ ] `scripts/backfill_domain_aliases.py` and `scripts/reupsert_agents_pinecone.py` are unchanged and listed as operational script blockers.
- [ ] The manifest keeps package-level blockers for all still-shipped legacy packages.
- [ ] `uv run pytest -q tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py` passes.
- [ ] `uv run pytest -q tests/test_agent_response_handler.py tests/test_common_foundation.py` passes.
- [ ] `uv run --with ruff ruff check execution/dispatch/response_handler.py` passes.
- [ ] Whole-repo Ruff has no new debt relative to the captured baseline. If
      `uv run --with ruff ruff check .` still fails, the residual failures are
      reported as preexisting repo-wide debt rather than fixed in this pass.
