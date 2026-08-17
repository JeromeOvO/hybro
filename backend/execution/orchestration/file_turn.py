"""Idempotent task projection for terminal file-upload turns."""

from __future__ import annotations

from execution.ports import (
    ExecutionDeliveryPort,
    RoomMessageReader,
    RoomMessageWriter,
)


async def persist_file_turn_task(
    *,
    message_writer: RoomMessageWriter,
    message_reader: RoomMessageReader,
    delivery: ExecutionDeliveryPort,
    room_id: str,
    message_id: str,
    state: str,
    message_text: str | None = None,
    task_metadata: dict[str, object] | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> None:
    await message_writer.update_task_state_on_message(
        message_id,
        state,
        message_text=message_text,
        task_metadata=task_metadata,
    )
    message = await message_reader.get_room_agent_message_by_message_id(message_id)
    content = getattr(message, "message_content", None)
    task = getattr(content, "message_task", None)
    task_state = getattr(getattr(task, "status", None), "state", None)
    task_state = str(getattr(task_state, "value", task_state)).lower().replace("_", "-")
    metadata = getattr(task, "metadata", None) or {}
    if (
        task_state != state
        or (
            message_text is not None
            and getattr(content, "message_text", None) != message_text
        )
        or any(
            metadata.get(key) != value for key, value in (task_metadata or {}).items()
        )
    ):
        raise RuntimeError(
            f"failed to persist file-turn task {message_id!r} as {state}"
        )
    await delivery.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=state,
        agent_id=agent_id,
        agent_name=agent_name,
    )
