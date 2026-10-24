"""C5 step 1f: approval owns HITL persistence; the runtime-store aggregate is gone."""

from __future__ import annotations

import importlib
import inspect
import tomllib
from pathlib import Path

import pytest

from tests.test_module_boundaries import DAL_IMPORT_DEBT, DOMAIN_MODULES
from tests.test_module_protocol_inventory import EXPECTED_METHODS

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "dal.runtime_store",
        "dal.runtime_store.parts",
        "dal.runtime_store.contracts",
        "common.protocols.runtime_store_protocols",
        "common.dto.runtime_store",
    ],
)
def test_runtime_store_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_approval_is_a_registered_domain_module():
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert "approval" in DOMAIN_MODULES
    assert {"approval", "approval.repository"} <= packages
    assert not {name for name in packages if name.startswith("dal.runtime_store")}
    assert "approval.protocols" in EXPECTED_METHODS


def test_approval_repository_exports_the_hitl_stores():
    from approval.repository import HITLLifecycleStore, HITLRequestStore

    assert HITLRequestStore.__module__ == "approval.repository.hitl_store"
    assert HITLLifecycleStore.__module__ == ("approval.repository.hitl_lifecycle_store")
    assert set(inspect.signature(HITLRequestStore).parameters) == {
        "hitl_requests",
        "message_writer",
    }
    assert set(inspect.signature(HITLLifecycleStore).parameters) == {
        "interactions",
        "resume_commands",
        "hitl_requests",
    }


def test_runtime_store_names_left_the_shared_packages():
    import common.dto as dto
    import common.protocols as protocols

    for name in ("RuntimeHITLStore", "RuntimeHITLLifecycleStore"):
        assert not hasattr(protocols, name), name
    for name in (
        "RuntimeAgentGroup",
        "RuntimeAgentRecord",
        "RuntimeRoomRecord",
        "RuntimeRoomAgentMessage",
        "RuntimeRoomUserMessage",
    ):
        assert not hasattr(dto, name), name


def test_dal_keeps_only_infrastructure():
    assert DAL_IMPORT_DEBT == frozenset()
    source_packages = {
        path.name
        for path in (BACKEND / "dal").iterdir()
        if path.is_dir() and any(path.glob("*.py"))
    }
    assert source_packages <= {"mongo", "redis", "migrations"}


def test_container_builds_the_hitl_stores_directly():
    import container

    source = inspect.getsource(container)

    assert "RuntimeRepositoryStore" not in source
    assert "create_runtime_repository_store" not in source
    assert "HITLRequestStore(" in source
    assert "HITLLifecycleStore(" in source
