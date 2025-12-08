"""
Agent Group API Endpoints

Provides CRUD operations for user-created agent groups.
"""

from fastapi import APIRouter, Query, Request

from models.agent_group import (
    BUILTIN_GROUP_ALL_AGENTS,
    BUILTIN_GROUP_ROOM_TEAM,
    AgentGroup,
)
from services.database_service import db_service

router = APIRouter()


@router.post("/agentGroups")
async def create_agent_group(request: Request):
    """
    Create a new agent group.
    """
    request_data = await request.json()
    name = request_data.get("name")
    description = request_data.get("description")
    owner_id = request_data.get("owner_id")
    agents = request_data.get("agents", [])

    if not name:
        return {"success": False, "error": "Group name is required", "status_code": 400}

    if not owner_id:
        return {"success": False, "error": "Owner ID is required", "status_code": 400}

    agent_group = AgentGroup(
        name=name,
        description=description,
        type="user",
        owner_id=owner_id,
        agents=agents,
    )

    success = await db_service.add_agent_group(agent_group)

    if success:
        return {
            "success": True,
            "group": agent_group.model_dump(mode="json"),
            "status_code": 200,
        }
    else:
        return {
            "success": False,
            "error": "Failed to create agent group",
            "status_code": 500,
        }


@router.get("/agentGroups")
async def list_agent_groups(owner_id: str | None = Query(default=None)):
    """
    List all agent groups for a user.
    Returns both built-in groups and user-created groups.
    """
    if not owner_id:
        return {"success": False, "error": "Owner ID is required", "status_code": 400}

    # Get user's custom groups
    user_groups = await db_service.get_agent_groups_by_owner(owner_id)

    # Add built-in groups at the beginning
    builtin_groups = [
        {
            "group_id": BUILTIN_GROUP_ALL_AGENTS,
            "name": "All Agents",
            "description": "Search the entire agent network for the best match",
            "type": "builtin",
            "owner_id": None,
            "agents": [],
        },
        {
            "group_id": BUILTIN_GROUP_ROOM_TEAM,
            "name": "Room Team",
            "description": "Use agents assigned to this room",
            "type": "builtin",
            "owner_id": None,
            "agents": [],
        },
    ]

    all_groups = builtin_groups + [g.model_dump(mode="json") for g in user_groups]

    return {"success": True, "groups": all_groups, "status_code": 200}


@router.get("/agentGroups/{group_id}")
async def get_agent_group(group_id: str):
    """
    Get a specific agent group by ID.
    """
    if not group_id:
        return {"success": False, "error": "Group ID is required", "status_code": 400}

    # Check for built-in groups
    if group_id == BUILTIN_GROUP_ALL_AGENTS:
        return {
            "success": True,
            "group": {
                "group_id": BUILTIN_GROUP_ALL_AGENTS,
                "name": "All Agents",
                "description": "Search the entire agent network for the best match",
                "type": "builtin",
                "owner_id": None,
                "agents": [],
            },
            "status_code": 200,
        }

    if group_id == BUILTIN_GROUP_ROOM_TEAM:
        return {
            "success": True,
            "group": {
                "group_id": BUILTIN_GROUP_ROOM_TEAM,
                "name": "Room Team",
                "description": "Use agents assigned to this room",
                "type": "builtin",
                "owner_id": None,
                "agents": [],
            },
            "status_code": 200,
        }

    group = await db_service.get_agent_group_by_id(group_id)

    if group:
        return {
            "success": True,
            "group": group.model_dump(mode="json"),
            "status_code": 200,
        }
    else:
        return {"success": False, "error": "Agent group not found", "status_code": 404}


@router.put("/agentGroups/{group_id}")
async def update_agent_group(group_id: str, request: Request):
    """
    Update an agent group.
    """
    if not group_id:
        return {"success": False, "error": "Group ID is required", "status_code": 400}

    # Cannot update built-in groups
    if group_id in [BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM]:
        return {
            "success": False,
            "error": "Cannot update built-in groups",
            "status_code": 400,
        }

    request_data = await request.json()

    # Build updates dict
    updates = {}
    if "name" in request_data:
        updates["name"] = request_data["name"]
    if "description" in request_data:
        updates["description"] = request_data["description"]
    if "agents" in request_data:
        updates["agents"] = request_data["agents"]

    if not updates:
        return {"success": False, "error": "No updates provided", "status_code": 400}

    success = await db_service.update_agent_group(group_id, updates)

    if success:
        # Fetch updated group
        updated_group = await db_service.get_agent_group_by_id(group_id)
        return {
            "success": True,
            "group": updated_group.model_dump(mode="json") if updated_group else None,
            "status_code": 200,
        }
    else:
        return {
            "success": False,
            "error": "Failed to update agent group",
            "status_code": 500,
        }


@router.delete("/agentGroups/{group_id}")
async def delete_agent_group(group_id: str):
    """
    Delete an agent group.
    """
    if not group_id:
        return {"success": False, "error": "Group ID is required", "status_code": 400}

    # Cannot delete built-in groups
    if group_id in [BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM]:
        return {
            "success": False,
            "error": "Cannot delete built-in groups",
            "status_code": 400,
        }

    success = await db_service.delete_agent_group(group_id)

    if success:
        return {"success": True, "status_code": 200}
    else:
        return {
            "success": False,
            "error": "Failed to delete agent group",
            "status_code": 500,
        }
