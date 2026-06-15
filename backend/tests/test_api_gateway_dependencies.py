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


def test_gateway_dependency_validation_checks_route_module_bindings(monkeypatch):
    from api_gateway.dependencies import missing_gateway_route_bindings
    from api_gateway.routes import files_routes

    monkeypatch.setattr(files_routes, "file_storage", None)

    assert "api_gateway.routes.files_routes.file_storage" in (
        missing_gateway_route_bindings()
    )
