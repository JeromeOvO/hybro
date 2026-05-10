import ast
import sys
import tomllib
from pathlib import Path
from typing import get_type_hints
from unittest.mock import MagicMock

from common.protocols import (
    AgentCardResolver,
    AgentTransport,
    LLMProvider,
    ModelRegistry,
)


def test_adapter_implementations_satisfy_runtime_protocols():
    from a2a_adapter import AgentCardResolverImpl, AgentTransportImpl
    from llm_gateway import LLMGatewayImpl, ModelRegistryImpl

    fake_client = MagicMock()
    registry = ModelRegistryImpl()

    assert isinstance(AgentTransportImpl(timeout=1, client=fake_client), AgentTransport)
    assert isinstance(AgentCardResolverImpl(client=fake_client), AgentCardResolver)
    assert isinstance(
        LLMGatewayImpl(model_registry=registry, providers={"openai": MagicMock()}),
        LLMProvider,
    )
    assert isinstance(registry, ModelRegistry)


def test_adapter_top_level_exports_are_explicit():
    import a2a_adapter
    import llm_gateway
    from a2a_adapter import AgentCardResolverImpl, AgentTransportImpl
    from llm_gateway import LLMGatewayImpl, ModelRegistryImpl

    assert AgentTransportImpl is a2a_adapter.AgentTransportImpl
    assert AgentCardResolverImpl is a2a_adapter.AgentCardResolverImpl
    assert LLMGatewayImpl is llm_gateway.LLMGatewayImpl
    assert ModelRegistryImpl is llm_gateway.ModelRegistryImpl
    assert set(a2a_adapter.__all__) == {"AgentTransportImpl", "AgentCardResolverImpl"}
    assert set(llm_gateway.__all__) == {"LLMGatewayImpl", "ModelRegistryImpl"}


def test_llm_gateway_provider_mapping_is_typed_to_provider_protocol():
    from llm_gateway.gateway import LLMGatewayImpl

    hints = get_type_hints(LLMGatewayImpl.__init__)

    assert hints["providers"] == dict[str, LLMProvider] | None


def test_adapter_subpackages_are_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {
        "a2a_adapter",
        "llm_gateway",
        "llm_gateway.providers",
    }.issubset(packages)


def test_a2a_adapter_import_boundary():
    allowed_roots = set(sys.stdlib_module_names) | {
        "__future__",
        "a2a",
        "common",
        "dal",
        "httpx",
        "httpx_sse",
    }
    forbidden_roots = {
        "config",
        "container",
        "database",
        "infrastructure",
        "main",
        "models",
        "modules",
        "services",
    }

    _assert_import_boundary(Path("a2a_adapter"), allowed_roots, forbidden_roots)


def test_llm_gateway_import_boundary():
    allowed_roots = set(sys.stdlib_module_names) | {
        "__future__",
        "aioboto3",
        "botocore",
        "common",
        "dal",
        "google",
        "openai",
    }
    forbidden_roots = {
        "config",
        "container",
        "database",
        "infrastructure",
        "main",
        "models",
        "modules",
        "services",
    }

    _assert_import_boundary(Path("llm_gateway"), allowed_roots, forbidden_roots)


def _assert_import_boundary(
    package_path: Path,
    allowed_roots: set[str],
    forbidden_roots: set[str],
) -> None:
    assert package_path.exists(), f"{package_path} does not exist"

    for path in package_path.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported_roots: set[str] = set()
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level:
                continue
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = {node.module.split(".")[0]}

            assert imported_roots.isdisjoint(forbidden_roots), (
                f"{path} imports forbidden root {imported_roots & forbidden_roots}"
            )
            unexpected = imported_roots - allowed_roots
            assert not unexpected, f"{path} imports unexpected root {unexpected}"
