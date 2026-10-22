"""C8: compatibility and legacy tolerances are removed once nothing needs them."""

from __future__ import annotations

import inspect

import pytest


def test_health_body_drops_the_legacy_redis_key():
    from main import compute_health_status

    result = compute_health_status(
        delivery_pubsub_connected=True,
        delivery_kv_connected=True,
        redis_runtime_connected=True,
        redis_url="redis://localhost:6379/0",
        agent_search_index_ready=True,
        memory_search_index_ready=True,
        search_indexes_ready=True,
    )

    assert "legacy_redis_service_connected" not in result["body"]
    assert result["body"]["redis_service_connected"] is True


@pytest.mark.parametrize(
    "name", ["store", "database" + "_service", "db" + "_service", "a2a_service"]
)
def test_hitl_factory_rejects_retired_arguments_without_an_alias_list(name):
    from approval import factory

    with pytest.raises(TypeError, match="unexpected argument"):
        factory.create_hitl_service(**{name: object()})
    assert "legacy" not in inspect.getsource(factory)
