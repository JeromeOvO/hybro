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
        "AgentGroupStore": {
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
    "approval.protocols": {
        "HITLDeliveryPort": {"emit"},
        "HITLLifecyclePersistencePort": {
            "attach_interaction_request",
            "claim_interaction_application",
            "claim_resume_command",
            "claim_run_answer_projection",
            "create_resume_command",
            "ensure_indexes",
            "get_interaction_for_request_strict",
            "get_interaction_strict",
            "get_resume_command_for_interaction_strict",
            "get_resume_command_strict",
            "iter_active_interactions",
            "iter_due_interactions",
            "iter_due_resume_commands",
            "iter_materializing_interactions",
            "iter_stale_applications",
            "iter_unreconciled_terminal_interactions",
            "iter_unreconciled_terminal_requests",
            "mark_interaction_application_state",
            "mark_interaction_terminal_reconciled",
            "mark_resume_command_aggregate_applied",
            "mark_resume_command_state",
            "mark_run_answer_projection",
            "materialize_interaction",
            "reclaim_stale_resume_command",
            "record_interaction_answer",
            "renew_interaction_application",
            "renew_resume_command",
            "renew_run_answer_projection",
            "resume_uncertain_interaction",
            "terminalize_interaction",
        },
        "HITLPersistencePort": {
            "cas_update_hitl_request",
            "cas_update_hitl_request_strict",
            "claim_hitl_open_projection",
            "claim_hitl_request",
            "complete_hitl_open_projection",
            "count_hitl_requests_for_message",
            "create_hitl_request",
            "create_or_reuse_pending_hitl_request",
            "fenced_update_hitl_request",
            "find_pending_hitl_request_for_agent_message",
            "get_hitl_request",
            "get_pending_hitl_requests",
            "get_pending_hitl_requests_for_message",
            "get_pending_hitl_requests_strict",
            "get_room_agent_message_by_message_id",
            "get_room_user_message_by_message_id",
            "iter_stale_processing_hitl_requests",
            "persist_hitl_interaction_metadata",
            "persist_hitl_request_id_on_message",
            "persist_hitl_user_answer",
            "persist_pending_hitl_on_agent_message",
            "release_hitl_open_projection",
            "reset_last_notified_state",
            "resolve_client_request_id_for_message_id",
            "update_agent_message_task_state",
            "update_hitl_request",
        },
        "HITLTerminalLifecyclePort": {"terminalize_owning_run"},
    },
    "local_agents.protocols": {
        "LocalAgentDiscovery": {"request_discovery"},
        "LocalAgentWriter": {
            "list_local_agent_ids",
            "mark_local_agents_inactive",
            "upsert_local_agent",
        },
    },
    "conversation.protocols": {
        "ClientRequestIdResolver": {
            "resolve_client_request_id_for_agent_message",
            "resolve_client_request_id_for_message_id",
        },
        "ConversationMessageWriter": {
            "persist_final_agent_message",
            "persist_hitl_interaction_metadata",
            "persist_hitl_request_id_on_message",
            "persist_hitl_user_answer",
            "persist_pending_hitl_on_agent_message",
            "update_agent_message_task_state",
        },
    },
    "chat.protocols": {
        "RoomCenterCompatibility": {
            "create_new_room",
            "delete_room_by_room_id",
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
