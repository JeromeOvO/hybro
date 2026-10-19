from __future__ import annotations

import inspect

from conversation.facade import ConversationFacade


def test_room_facade_exposes_hub_publish_support_protocol_methods() -> None:
    assert inspect.iscoroutinefunction(ConversationFacade.get_turn_completion_kind)
