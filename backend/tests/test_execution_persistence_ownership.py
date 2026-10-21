"""C5 step 1e: execution owns the orchestrator persistence adapters."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

from tests.test_module_boundaries import DAL_IMPORT_DEBT

BACKEND = Path(__file__).resolve().parents[1]
MODULES = (
    "artifacts",
    "epoch_bindings",
    "event_store",
    "hitl",
    "projection",
    "run_store",
    "stores",
)


def test_dal_orchestrator_package_is_gone():
    assert not (BACKEND / "dal" / "orchestrator").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("dal.orchestrator")


@pytest.mark.parametrize("name", MODULES)
def test_orchestrator_adapters_live_in_execution_repository(name):
    module = importlib.import_module(f"execution.repository.orchestrator.{name}")

    assert module.__name__ == f"execution.repository.orchestrator.{name}"


def test_package_root_exports_the_adapters():
    from execution.repository import orchestrator

    for name in (
        "GuardedRoomFileArtifactWriter",
        "MongoAgentCallLedgerStore",
        "MongoAppendEventProjector",
        "MongoFinalMessageProjector",
        "MongoHITLApplicationStore",
        "MongoOrchestratorEventStore",
        "MongoOrchestratorRunStore",
        "MongoRoomEpochStore",
    ):
        assert name in orchestrator.__all__, name


def test_dal_import_debt_is_empty():
    assert DAL_IMPORT_DEBT == frozenset()


def test_packaging_lists_the_new_package():
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert "execution.repository.orchestrator" in packages
    assert "dal.orchestrator" not in packages


def test_no_source_imports_dal_orchestrator():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in {".venv", "build"} or relative == Path(
            __file__
        ).relative_to(BACKEND):
            continue
        if "dal.orchestrator" in path.read_text():
            offenders.append(str(relative))
    assert offenders == []
