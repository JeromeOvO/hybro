"""The tracked ``openapi.json`` snapshot matches the live application schema."""

import json
from pathlib import Path

import pytest


@pytest.mark.core
def test_tracked_openapi_snapshot_matches_live_schema():
    from main import app

    tracked = json.loads(Path("openapi.json").read_text())

    assert tracked == app.openapi(), (
        "openapi.json is stale; regenerate it with "
        "json.dumps(app.openapi(), indent=2) and commit the result"
    )
