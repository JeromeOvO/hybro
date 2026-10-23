"""C8 step 4b-2: target-scope resolution lives in conversation.target_scope."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conversation.target_scope import (
    TargetScopeResolver,
    orchestration_request_info,
    selected_agent_ids_from_request,
)


def test_runtime_no_longer_owns_scope_resolution():
    from conversation.runtime import ConversationRuntime

    for name in (
        "_routing_excluded_agent_ids",
        "_sanitize_routing_scope",
        "_orchestration_request_info",
        "_selected_agent_ids_from_request",
        "_resolve_selected_candidate_scope",
        "_validate_candidate_scope_metadata",
        "_validate_canonical_mentions",
        "_resolve_explicit_target_scope",
    ):
        assert not hasattr(ConversationRuntime, name), name


def test_selected_agent_ids_read_only_mention_scopes():
    mention = SimpleNamespace(
        extend_info={"agent_scope": {"source": "mention", "agent_ids": [" a ", "", 3]}}
    )
    room_default = SimpleNamespace(extend_info={"agent_scope": {"source": "room"}})

    assert selected_agent_ids_from_request(mention) == ["a"]
    assert selected_agent_ids_from_request(room_default) is None
    assert orchestration_request_info(SimpleNamespace(extend_info=None)) == {}


@pytest.mark.asyncio
async def test_sanitize_excludes_agents_with_open_capability_issues():
    agent = SimpleNamespace(agent_id="a1")
    store = SimpleNamespace(get_agent_by_agent_id=AsyncMock(return_value=agent))
    issues = SimpleNamespace(get_excluded_agent_ids=AsyncMock(return_value=["a1"]))
    resolver = TargetScopeResolver(store=store, capability_issue_reader=issues)

    agents, invalid = await resolver.sanitize(["a1"], sender_user_id="u1")

    assert agents == []
    assert invalid == ["a1"]
