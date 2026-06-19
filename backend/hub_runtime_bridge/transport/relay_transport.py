from __future__ import annotations

from common.dto import HubCancelCommand, HubDispatchCommand, HubReplyCommand


def dispatch_command_to_event(command: HubDispatchCommand) -> dict:
    return {
        "type": "user_message",
        "room_id": command.room_id,
        "user_message_id": command.user_message_id,
        "agent_message_id": command.agent_message_id,
        "agent_id": command.agent_id,
        "local_agent_id": command.local_agent_id,
        "message": command.payload,
        "task_id": command.task_id,
    }


def cancel_command_to_event(command: HubCancelCommand) -> dict:
    return {
        "type": "cancel_task",
        "agent_message_id": command.agent_message_id,
        "local_agent_id": command.local_agent_id,
        "task_id": command.task_id,
    }


def reply_command_to_event(command: HubReplyCommand) -> dict:
    return {
        "type": "user_reply",
        "room_id": command.room_id,
        "agent_message_id": command.agent_message_id,
        "local_agent_id": command.local_agent_id,
        "reply_text": command.reply_text,
        "task_id": command.task_id,
        "context_id": command.context_id,
    }


__all__ = [
    "cancel_command_to_event",
    "dispatch_command_to_event",
    "reply_command_to_event",
]
