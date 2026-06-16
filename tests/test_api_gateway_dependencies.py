import importlib

import pytest


def test_api_gateway_deps_report_missing_required_fields():
    from api_gateway.dependencies import APIGatewayDeps, missing_required_deps

    deps = APIGatewayDeps(
        gateway_service=object(),
        file_storage=None,
        relay_service=object(),
        execution_deps=None,
        platform_facade=object(),
    )

    assert missing_required_deps(deps) == ["file_storage", "execution_deps"]


def test_bind_api_gateway_deps_rejects_incomplete_bindings():
    from api_gateway.dependencies import APIGatewayDeps, bind_api_gateway_deps

    deps = APIGatewayDeps(
        gateway_service=object(),
        file_storage=object(),
        relay_service=None,
        execution_deps=object(),
        platform_facade=object(),
    )

    with pytest.raises(RuntimeError, match="relay_service"):
        bind_api_gateway_deps(deps)


@pytest.mark.parametrize(
    ("module_name", "binding_name"),
    [
        ("api_gateway.routes.files_routes", "file_storage"),
        ("api_gateway.routes.agent_routes", "agent_center"),
        ("api_gateway.routes.agent_routes", "agent_liveness_checker"),
        ("api_gateway.routes.room_routes", "room_center"),
        ("api_gateway.routes.room_routes", "room_store"),
        ("api_gateway.routes.sse_routes", "sse_manager"),
        ("api_gateway.routes.webhook_routes", "webhook_receiver"),
        ("api_gateway.routes.inspection_routes", "inspection_center"),
        ("api_gateway.routes.memory_routes", "memory_center"),
    ],
)
def test_gateway_dependency_validation_checks_route_module_bindings(
    monkeypatch,
    module_name,
    binding_name,
):
    from api_gateway.dependencies import missing_gateway_route_bindings

    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, binding_name, None)

    assert f"{module_name}.{binding_name}" in missing_gateway_route_bindings()
