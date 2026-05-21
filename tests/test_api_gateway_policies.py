import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "api_gateway_policy_matrix.json"


def _fixture_matrix():
    return json.loads(FIXTURE.read_text())


def test_route_policy_matrix_matches_fixture():
    from api_gateway.policies import ROUTE_POLICIES

    current = {
        name: {
            "auth": policy.auth,
            "cors": policy.cors,
            "api_key": policy.api_key,
            "tags": list(policy.tags),
            **({"deprecated": policy.deprecated} if policy.deprecated else {}),
        }
        for name, policy in ROUTE_POLICIES.items()
    }

    assert current == _fixture_matrix()


def test_open_cors_groups_are_explicitly_limited():
    from api_gateway.policies import open_cors_groups

    assert open_cors_groups() == frozenset(
        {"discovery", "platform_gateway", "relay"}
    )


def test_every_public_route_group_has_policy():
    from api_gateway.policies import ROUTE_POLICIES
    from api_gateway.registry import route_group_for_path
    from main import app

    missing = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        group = route_group_for_path(path)
        if group not in ROUTE_POLICIES:
            missing.add((path, group))

    assert missing == set()


def test_route_tags_follow_policy_matrix():
    from api_gateway.policies import ROUTE_POLICIES
    from api_gateway.registry import route_group_for_path
    from main import app

    bad = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        group = route_group_for_path(path)
        policy = ROUTE_POLICIES[group]
        tags = set(getattr(route, "tags", []) or [])
        if not set(policy.tags).issubset(tags):
            bad.append((path, group, sorted(tags), list(policy.tags)))

    assert bad == []


def test_relay_policy_matches_api_key_only_route_dependencies():
    from api_gateway.policies import ROUTE_POLICIES
    from main import app

    assert ROUTE_POLICIES["relay"].auth == "api-key-route-level"

    bad = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/relay"):
            continue
        dependencies = {
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in route.dependant.dependencies
        }
        if not dependencies & {"get_api_key", "get_api_key_no_track"}:
            bad.append((path, getattr(route, "name", ""), sorted(dependencies)))
        if dependencies & {"get_current_user", "get_optional_user"}:
            bad.append((path, getattr(route, "name", ""), sorted(dependencies)))

    assert bad == []
