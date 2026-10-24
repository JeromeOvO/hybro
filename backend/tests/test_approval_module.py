"""C11: the HITL service lives in the approval module."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

from tests.test_module_boundaries import DOMAIN_IMPORT_DEBT, imported_modules

BACKEND = Path(__file__).resolve().parents[1]
MOVED = (
    "adapters",
    "application",
    "delivery",
    "exceptions",
    "factory",
    "reconciler",
    "service",
    "translators",
    "validation",
)
APPROVAL_PORTS = (
    "HITLPersistencePort",
    "HITLLifecyclePersistencePort",
    "HITLTerminalLifecyclePort",
    "HITLDeliveryPort",
)


def test_execution_hitl_package_is_gone():
    assert not (BACKEND / "execution" / "hitl").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("execution.hitl")


@pytest.mark.parametrize("name", MOVED)
def test_hitl_modules_live_in_approval(name):
    module = importlib.import_module(f"approval.{name}")

    assert module.__name__ == f"approval.{name}"


def test_no_production_source_names_execution_hitl():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in {".venv", "build", "tests"}:
            continue
        if "execution.hitl" in path.read_text():
            offenders.append(str(relative))
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text())

    assert offenders == []
    assert "execution.hitl" not in pyproject["tool"]["setuptools"]["packages"]


def test_approval_ports_moved_out_of_execution():
    import approval.protocols as protocols
    import execution.ports as execution_ports

    for name in APPROVAL_PORTS:
        assert not hasattr(execution_ports, name), name
        assert name in protocols.__all__, name


def test_approval_no_longer_imports_execution():
    for path in (BACKEND / "approval").rglob("*.py"):
        modules = imported_modules(path)
        assert not {
            module
            for module in modules
            if module == "execution" or module.startswith("execution.")
        }, path


def test_execution_reaches_approval_only_through_its_root_or_protocols():
    for relative in ("execution/facade.py", "execution/orchestrator_routing.py"):
        approval_modules = {
            module
            for module in imported_modules(BACKEND / relative)
            if module == "approval" or module.startswith("approval.")
        }
        assert approval_modules <= {"approval", "approval.protocols"}, relative
    assert not {pair for pair in DOMAIN_IMPORT_DEBT if "approval" in pair[0]}


def test_terminal_run_statuses_have_one_definition_in_common():
    from common.run_status import TERMINAL_RUN_STATUSES
    from execution.orchestrator import persistence

    assert persistence.TERMINAL_RUN_STATUSES is TERMINAL_RUN_STATUSES


def test_public_text_sanitizer_is_injected_through_the_factory():
    import inspect

    from approval import service as service_module
    from approval.factory import create_hitl_service

    service = create_hitl_service(public_text_sanitizer=lambda text, **_: text)

    assert service._public_text_sanitizer is not None
    assert "orchestrator.public_text" not in inspect.getsource(service_module)
