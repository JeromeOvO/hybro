"""
Unit tests for Agent Group API endpoints.

Tests cover:
- Group CRUD (create, list, get, update, delete)
- Built-in group protection (cannot update/delete)
- Input validation
- Database error handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api_gateway.routes.agent_group_routes import (
    create_agent_group,
    delete_agent_group,
    get_agent_group,
    list_agent_groups,
    update_agent_group,
)
from models.agent_group import (
    BUILTIN_GROUP_ALL_AGENTS,
    BUILTIN_GROUP_ROOM_TEAM,
    AgentGroup,
)

# =============================================================================
# Create Agent Group Tests
# =============================================================================


class TestCreateAgentGroup:
    """Tests for create_agent_group endpoint."""

    @pytest.mark.asyncio
    async def test_creates_group_with_valid_data(self, mock_db_service, mock_user):
        """Should create group and return it."""
        mock_db_service.add_agent_group = AsyncMock(return_value=True)
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "name": "My Group",
                "description": "Test group",
                "owner_id": mock_user.user_id,
                "agents": ["agent-1", "agent-2"],
            }
        )

        result = await create_agent_group(
            mock_request, user=mock_user, db=mock_db_service
        )

        assert result["success"] is True
        assert result["group"]["name"] == "My Group"
        assert result["group"]["owner_id"] == mock_user.user_id
        mock_db_service.add_agent_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_missing_name(self, mock_db_service, mock_user):
        """Should return error when name is empty."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "owner_id": "user-001",
            }
        )

        result = await create_agent_group(
            mock_request, user=mock_user, db=mock_db_service
        )
        assert result["success"] is False
        assert result["status_code"] == 400

    @pytest.mark.asyncio
    async def test_rejects_missing_owner_id(self, mock_db_service):
        """Should return error when owner_id is empty."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "name": "My Group",
            }
        )

        result = await create_agent_group(mock_request, user=None, db=mock_db_service)
        assert result["success"] is False
        assert result["status_code"] == 400

    @pytest.mark.asyncio
    async def test_handles_db_failure(self, mock_db_service, mock_user):
        """Should return 500 when database insert fails."""
        mock_db_service.add_agent_group = AsyncMock(return_value=False)
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "name": "My Group",
                "owner_id": mock_user.user_id,
            }
        )

        result = await create_agent_group(
            mock_request, user=mock_user, db=mock_db_service
        )

        assert result["success"] is False
        assert result["status_code"] == 500

    @pytest.mark.asyncio
    async def test_rejects_mismatched_owner_id(self, mock_db_service, mock_user):
        """Should not allow creating a group for another owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(
            return_value={
                "name": "Other User Group",
                "owner_id": "someone-else",
            }
        )

        result = await create_agent_group(
            mock_request,
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is False
        assert result["status_code"] == 403
        mock_db_service.add_agent_group.assert_not_called()


# =============================================================================
# List Agent Groups Tests
# =============================================================================


class TestListAgentGroups:
    """Tests for list_agent_groups endpoint."""

    @pytest.mark.asyncio
    async def test_returns_builtin_and_user_groups(self, mock_db_service, mock_user):
        """Should return builtin groups + user groups."""
        user_group = AgentGroup(
            name="Custom Group", type="user", owner_id=mock_user.user_id, agents=["a1"]
        )
        mock_db_service.get_agent_groups_by_owner = AsyncMock(return_value=[user_group])

        result = await list_agent_groups(
            owner_id=mock_user.user_id,
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is True
        assert len(result["groups"]) == 3  # 2 builtin + 1 user
        assert result["groups"][0]["group_id"] == BUILTIN_GROUP_ALL_AGENTS
        assert result["groups"][1]["group_id"] == BUILTIN_GROUP_ROOM_TEAM

    @pytest.mark.asyncio
    async def test_rejects_missing_owner_id(self, mock_db_service):
        """Should return error when owner_id is not provided."""
        result = await list_agent_groups(
            owner_id=None,
            user=None,
            db=mock_db_service,
        )
        assert result["success"] is False
        assert result["status_code"] == 400

    @pytest.mark.asyncio
    async def test_rejects_mismatched_owner_filter(self, mock_db_service, mock_user):
        """Should not allow listing another user's groups."""
        result = await list_agent_groups(
            owner_id="someone-else",
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is False
        assert result["status_code"] == 403
        mock_db_service.get_agent_groups_by_owner.assert_not_called()


# =============================================================================
# Get Agent Group Tests
# =============================================================================


class TestGetAgentGroup:
    """Tests for get_agent_group endpoint."""

    @pytest.mark.asyncio
    async def test_returns_builtin_all_agents(self, mock_db_service, mock_user):
        """Should return All Agents builtin group without DB lookup."""
        result = await get_agent_group(
            BUILTIN_GROUP_ALL_AGENTS,
            user=mock_user,
            db=mock_db_service,
        )
        assert result["success"] is True
        assert result["group"]["group_id"] == BUILTIN_GROUP_ALL_AGENTS
        assert result["group"]["type"] == "builtin"

    @pytest.mark.asyncio
    async def test_returns_builtin_room_team(self, mock_db_service, mock_user):
        """Should return Room Team builtin group without DB lookup."""
        result = await get_agent_group(
            BUILTIN_GROUP_ROOM_TEAM,
            user=mock_user,
            db=mock_db_service,
        )
        assert result["success"] is True
        assert result["group"]["group_id"] == BUILTIN_GROUP_ROOM_TEAM

    @pytest.mark.asyncio
    async def test_returns_user_group_from_db(self, mock_db_service, mock_user):
        """Should return user group from database."""
        group = AgentGroup(
            group_id="grp-001", name="My Group", type="user", owner_id=mock_user.user_id
        )
        mock_db_service.get_agent_group_by_id = AsyncMock(return_value=group)

        result = await get_agent_group(
            "grp-001",
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is True
        assert result["group"]["group_id"] == "grp-001"

    @pytest.mark.asyncio
    async def test_rejects_group_owned_by_another_user(
        self, mock_db_service, mock_user
    ):
        """Should not return a user group owned by another user."""
        group = AgentGroup(
            group_id="grp-001", name="Other Group", type="user", owner_id="other-user"
        )
        mock_db_service.get_agent_group_by_id = AsyncMock(return_value=group)

        result = await get_agent_group("grp-001", user=mock_user, db=mock_db_service)

        assert result["success"] is False
        assert result["status_code"] == 403

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self, mock_db_service, mock_user):
        """Should return 404 when group doesn't exist."""
        mock_db_service.get_agent_group_by_id = AsyncMock(return_value=None)

        result = await get_agent_group(
            "nonexistent",
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is False
        assert result["status_code"] == 404


# =============================================================================
# Update Agent Group Tests
# =============================================================================


class TestUpdateAgentGroup:
    """Tests for update_agent_group endpoint."""

    @pytest.mark.asyncio
    async def test_updates_group(self, mock_db_service, mock_user):
        """Should update and return the group."""
        updated = AgentGroup(
            group_id="grp-001",
            name="Updated Name",
            type="user",
            owner_id=mock_user.user_id,
        )
        mock_db_service.update_agent_group = AsyncMock(return_value=True)
        mock_db_service.get_agent_group_by_id = AsyncMock(return_value=updated)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"name": "Updated Name"})

        result = await update_agent_group(
            "grp-001",
            mock_request,
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is True
        assert result["group"]["name"] == "Updated Name"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "builtin_id", [BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM]
    )
    async def test_rejects_update_of_builtin_group(
        self, builtin_id, mock_db_service, mock_user
    ):
        """Should reject updates to builtin groups."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"name": "Hack"})

        result = await update_agent_group(
            builtin_id,
            mock_request,
            user=mock_user,
            db=mock_db_service,
        )
        assert result["success"] is False
        assert result["status_code"] == 400
        assert "built-in" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_empty_update(self, mock_db_service, mock_user):
        """Should reject when no updates are provided."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})

        result = await update_agent_group(
            "grp-001",
            mock_request,
            user=mock_user,
            db=mock_db_service,
        )
        assert result["success"] is False
        assert result["status_code"] == 400


# =============================================================================
# Delete Agent Group Tests
# =============================================================================


class TestDeleteAgentGroup:
    """Tests for delete_agent_group endpoint."""

    @pytest.mark.asyncio
    async def test_deletes_user_group(self, mock_db_service, mock_user):
        """Should delete a user-created group."""
        mock_db_service.get_agent_group_by_id = AsyncMock(
            return_value=AgentGroup(
                group_id="grp-001",
                name="My Group",
                type="user",
                owner_id=mock_user.user_id,
            )
        )
        mock_db_service.delete_agent_group = AsyncMock(return_value=True)

        result = await delete_agent_group(
            "grp-001",
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is True
        mock_db_service.delete_agent_group.assert_called_once_with("grp-001")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "builtin_id", [BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM]
    )
    async def test_rejects_delete_of_builtin_group(
        self, builtin_id, mock_db_service, mock_user
    ):
        """Should reject deletion of builtin groups."""
        result = await delete_agent_group(
            builtin_id,
            user=mock_user,
            db=mock_db_service,
        )
        assert result["success"] is False
        assert result["status_code"] == 400
        assert "built-in" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_handles_db_failure(self, mock_db_service, mock_user):
        """Should return 500 when database delete fails."""
        mock_db_service.get_agent_group_by_id = AsyncMock(
            return_value=AgentGroup(
                group_id="grp-001",
                name="My Group",
                type="user",
                owner_id=mock_user.user_id,
            )
        )
        mock_db_service.delete_agent_group = AsyncMock(return_value=False)

        result = await delete_agent_group(
            "grp-001",
            user=mock_user,
            db=mock_db_service,
        )

        assert result["success"] is False
        assert result["status_code"] == 500
