from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_relay_and_hub_route_inventory_matches_phase8_fixture() -> None:
    from api.hub import router as hub_router
    from api.relay import router as relay_router

    expected = json.loads((ROOT / "tests/fixtures/phase8_hub_routes.json").read_text())
    actual = [
        {
            "methods": sorted(method for method in route.methods if method != "HEAD"),
            "path": route.path,
        }
        for route in [*relay_router.routes, *hub_router.routes]
    ]

    assert sorted(actual, key=lambda item: item["path"]) == sorted(
        expected, key=lambda item: item["path"]
    )
