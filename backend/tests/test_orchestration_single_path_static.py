import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
PRODUCTION_ROOTS = (
    BACKEND_ROOT / "api_gateway",
    BACKEND_ROOT / "common",
    BACKEND_ROOT / "dal",
    BACKEND_ROOT / "execution",
    BACKEND_ROOT / "jobs",
    BACKEND_ROOT / "models",
    BACKEND_ROOT / "room",
)
ORCHESTRATION_RUNTIME_ROOT = BACKEND_ROOT / "execution" / "orchestration"
ORCHESTRATION_RUNTIME_FILES = {
    BACKEND_ROOT / "jobs" / "stale_task_checker.py",
    BACKEND_ROOT / "models" / "orchestration.py",
    BACKEND_ROOT / "room" / "compat" / "runtime.py",
}


@pytest.fixture(scope="module")
def repository_text_index() -> dict[Path, str]:
    """Read each scanned source file once for all single-path invariants."""
    paths: set[Path] = set()
    for root in PRODUCTION_ROOTS:
        paths.update(root.rglob("*.py"))
    paths.update(
        {
            BACKEND_ROOT / "container.py",
            REPO_ROOT / ".env.example",
            BACKEND_ROOT / "docs" / "System-Architecture.md",
        }
    )

    frontend_root = REPO_ROOT / "frontend"
    assert frontend_root.is_dir(), f"frontend checkout not found: {frontend_root}"
    ignored_parts = {"node_modules", ".next", "dist", "build", "coverage"}
    for path in frontend_root.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            paths.add(path)

    return {path: path.read_text(errors="ignore") for path in paths}


def test_production_dispatch_cancellation_fence_uses_task_store(
    repository_text_index: dict[Path, str],
) -> None:
    container_text = repository_text_index[BACKEND_ROOT / "container.py"]
    assert "task_store.is_message_cancelled_strict" in container_text
    assert "message_store.is_message_cancelled_strict" not in container_text


def test_orchestration_has_no_rollout_selectors_or_external_schema_version(
    repository_text_index,
):
    forbidden = {
        "execution_orchestration_v2",
        "feature_orchestration_v2",
        "feature_run_dual_write",
        "orchestration_schema_version",
        "orchestration_v2",
        "v2_orchestration",
    }
    offenders: list[str] = []
    for path, source in repository_text_index.items():
        if not (path == BACKEND_ROOT / "container.py" or BACKEND_ROOT in path.parents):
            continue
        text = source.casefold()
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker}")

    assert offenders == []


def test_persisted_supervisor_trajectory_cannot_return(repository_text_index):
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path, text in repository_text_index.items()
        if path.suffix == ".py"
        and BACKEND_ROOT in path.parents
        and "supervisor_trajectory" in text
    ]

    assert offenders == []


def test_orchestration_runtime_has_no_versioned_path_names(repository_text_index):
    offenders: list[str] = []
    for path, source in repository_text_index.items():
        is_owned_module = (
            ORCHESTRATION_RUNTIME_ROOT in path.parents
            or path in ORCHESTRATION_RUNTIME_FILES
        )
        if not is_owned_module:
            continue
        text = source.casefold()
        if "_v2_" in text or "run_v2" in text or re.search(r"\bv2\b", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_frontend_does_not_read_private_orchestration_state(repository_text_index):
    frontend_root = REPO_ROOT / "frontend"
    matches = [
        str(path.relative_to(REPO_ROOT))
        for path, text in repository_text_index.items()
        if frontend_root in path.parents
        and ("supervisor_trajectory" in text or "SupervisorTrajectory" in text)
    ]

    assert matches == []
