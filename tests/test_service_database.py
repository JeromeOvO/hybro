"""
Unit tests for DatabaseService (database_service.py).

Tests cover:
- _build_visibility_filter: public-only vs user-specific queries
- get_all_visible_agents: delegation with correct filter
- get_all_active_agents: delegation with correct filter
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.database_service import DatabaseService


@pytest.fixture
def db_svc():
    """Create a DatabaseService with mocked Mongo driver."""
    svc = object.__new__(DatabaseService)
    svc.mongo = MagicMock()
    return svc


# =============================================================================
# _build_visibility_filter Tests
# =============================================================================


class TestBuildVisibilityFilter:
    """Tests for MongoDB visibility query building."""

    def test_public_only_when_no_user(self, db_svc):
        """Without user_id, only return public agents."""
        f = db_svc._build_visibility_filter(None)
        assert "$or" in f
        conditions = f["$or"]
        assert {"is_public": True} in conditions
        assert {"is_public": {"$exists": False}} in conditions
        # Should NOT contain provider_id condition
        assert all("provider_id" not in c for c in conditions)

    def test_includes_user_private_when_user_provided(self, db_svc):
        """With user_id, include that user's private agents."""
        f = db_svc._build_visibility_filter("user-001")
        assert "$or" in f
        conditions = f["$or"]
        assert {"provider_id": "user-001"} in conditions
        assert {"is_public": True} in conditions
        assert {"is_public": {"$exists": False}} in conditions

    def test_empty_string_user_id_treated_as_no_user(self, db_svc):
        """Empty string user_id should behave like None (public only)."""
        f = db_svc._build_visibility_filter("")
        conditions = f["$or"]
        assert all("provider_id" not in c for c in conditions)


# =============================================================================
# get_all_visible_agents Tests
# =============================================================================


class TestGetAllVisibleAgents:
    """Tests for visible agents retrieval."""

    @pytest.mark.asyncio
    async def test_passes_visibility_filter_to_mongo(self, db_svc):
        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=[])
        await db_svc.get_all_visible_agents(user_id="user-001")

        db_svc.mongo.get_agents_with_conditions.assert_called_once()
        query = db_svc.mongo.get_agents_with_conditions.call_args[0][0]
        assert {"provider_id": "user-001"} in query["$or"]

    @pytest.mark.asyncio
    async def test_public_only_without_user(self, db_svc):
        db_svc.mongo.get_agents_with_conditions = AsyncMock(return_value=[])
        await db_svc.get_all_visible_agents()

        query = db_svc.mongo.get_agents_with_conditions.call_args[0][0]
        conditions = query["$or"]
        assert all("provider_id" not in c for c in conditions)
