from fastapi.testclient import TestClient


def test_open_cors_gateway_groups_allow_external_preflight():
    from main import app

    client = TestClient(app)

    for path in (
        "/api/v1/discovery/agents",
        "/api/v1/gateway/agents/discover",
        "/api/v1/relay/hub/status",
    ):
        response = client.options(
            path,
            headers={
                "Origin": "https://external.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,x-api-key",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"


def test_open_cors_actual_responses_do_not_allow_credentials_with_wildcard():
    from main import app

    client = TestClient(app)
    response = client.get(
        "/api/v1/relay/hub/status",
        headers={"Origin": "https://external.example"},
    )

    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_open_cors_matching_is_path_segment_bounded():
    from fastapi import FastAPI

    from common.middleware.discovery_cors_middleware import DiscoveryCORSMiddleware

    app = FastAPI()
    app.add_middleware(DiscoveryCORSMiddleware, api_prefix="/api/v1")
    client = TestClient(app)

    response = client.options(
        "/api/v1/gateway2/agents/discover",
        headers={
            "Origin": "https://external.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers
