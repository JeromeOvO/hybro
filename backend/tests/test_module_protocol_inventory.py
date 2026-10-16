"""Exact method inventories of module-local ``protocols`` modules.

``tests/test_common_foundation.py`` pins ``common.protocols``. This test pins
the protocols that modules publish beside their facades, with the same
completeness check: every exported Protocol must be listed here.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from tests.test_common_foundation import _assert_methods

EXPECTED_METHODS = {
    "agent.protocols": {
        "AgentCapabilityIssueStore": {
            "get_issue_by_id",
            "get_issues_for_agent",
            "resolve_all_for_agent",
            "resolve_issue",
        },
        "AgentCenterCompatibility": {
            "delete_agent_from_route",
            "finalize_agent_response_for_route",
            "get_agent_card_from_url_for_route",
            "get_agents_by_provider_for_route",
            "get_visible_agent_for_route",
            "list_visible_agents_for_route",
            "register_agent_from_route",
        },
        "AgentGroupStoreCompatibility": {
            "add_agent_group",
            "delete_agent_group",
            "get_agent_group_by_id",
            "get_agent_groups_by_owner",
            "update_agent_group",
        },
        "AgentInspection": {"inspect_a2a_connection", "inspect_agent_card"},
        "AgentLivenessChecker": {"__call__"},
        "AgentNetworkAccess": {"discover", "get_profile", "list_groups", "search"},
        "AgentSuggestionService": {"suggest_agents"},
    },
    "local_agents.protocols": {
        "LocalAgentDiscovery": {"request_discovery"},
        "LocalAgentWriter": {
            "list_local_agent_ids",
            "mark_local_agents_inactive",
            "upsert_local_agent",
        },
    },
    "chat.protocols": {
        "RoomCenterCompatibility": {
            "create_new_room",
            "delete_room_by_room_id",
            "inquiry_active_runs",
            "inquiry_room_history_by_owner_id",
            "inquiry_room_messages_by_room_id",
            "inquiry_room_setting",
            "inquiry_rooms_by_room_owner_id",
            "update_room_agent_set",
            "update_room_default_mode",
            "update_room_history_fields",
            "update_room_name",
        },
    },
    "session.protocols": {
        "ConversationEventsPort": {"latest_seq", "read_range"},
        "SessionAccess": {"check", "close", "get_record", "list_sessions", "send"},
        "SessionService": {
            "check",
            "close",
            "list_for_owner",
            "record_for_owner",
            "send",
        },
        "SessionStore": {
            "get",
            "list_for_owner",
            "list_open_conversations",
            "mark_closed",
            "record_accepted_agent",
            "record_send",
            "reserve",
            "set_conversation_if_unset",
        },
    },
}


@pytest.mark.parametrize("module_name", sorted(EXPECTED_METHODS))
def test_module_protocol_methods_match_inventory(module_name):
    module = importlib.import_module(module_name)
    expected = EXPECTED_METHODS[module_name]

    for protocol_name, methods in expected.items():
        _assert_methods(getattr(module, protocol_name), methods)

    exported_protocols = {
        name
        for name in module.__all__
        if inspect.isclass(getattr(module, name))
        and getattr(getattr(module, name), "_is_protocol", False)
    }
    assert exported_protocols == set(expected)
