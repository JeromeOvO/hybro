"""C8 step 4b-1: mention parsing lives in conversation.mentions."""

from __future__ import annotations

from types import SimpleNamespace

from conversation.mentions import canonical_mentions_or_error, parse_agent_mentions


def test_parse_keeps_room_members_in_text_order():
    mentions = parse_agent_mentions(
        "hi <@b|Bee> and <@a|Ay> and <@x|Ghost>", {"a": "Alpha", "b": "Beta"}
    )

    assert [(m["agent_id"], m["agent_name"]) for m in mentions] == [
        ("b", "Beta"),
        ("a", "Alpha"),
    ]
    assert mentions[0]["mention_text"] == "<@b|Bee>"


def _agent(agent_id: str, name: str):
    return SimpleNamespace(agent_id=agent_id, agent_card=SimpleNamespace(name=name))


def test_canonical_mentions_use_the_registry_name():
    result = canonical_mentions_or_error([_agent("a1", "Alpha")], [])

    assert result == [
        {"agent_id": "a1", "agent_name": "Alpha", "mention_text": "<@a1|Alpha>"}
    ]


def test_invalid_mention_targets_produce_a_scope_error_response():
    result = canonical_mentions_or_error([_agent("a1", "Alpha")], ["bad", "worse"])

    assert result.success is False
    assert result.status_code == 400
    assert result.scope_resolution_error.code == "unauthorized_mention"
    assert "bad, worse" in result.error


def test_runtime_no_longer_parses_mentions_itself():
    from conversation.runtime import ConversationRuntime

    assert not hasattr(ConversationRuntime, "parse_agent_mentions")
