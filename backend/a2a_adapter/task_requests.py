"""SDK request construction.

Every outbound A2A request object is built here, so no other module has to know
the SDK's request shapes. A2A 1.0 renamed the JSON-RPC operations, but callers
keep expressing intent (send / get / cancel) and the SDK transport maps it to
the wire method for the interface's protocol version.
"""

from __future__ import annotations

from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    SendMessageRequest,
    TaskPushNotificationConfig,
)


def build_send_message_request(
    message: object,
    *,
    accepted_output_modes: list[str] | None = None,
    push_notification_config: dict[str, object] | None = None,
    blocking: bool = True,
) -> SendMessageRequest:
    from a2a.types import Message

    if not isinstance(message, Message):
        raise TypeError("build_send_message_request requires an SDK Message")
    request = SendMessageRequest(message=message)
    if accepted_output_modes:
        request.configuration.accepted_output_modes.extend(accepted_output_modes)
    push_config = build_push_notification_config(push_notification_config)
    if push_config is not None:
        request.configuration.task_push_notification_config.CopyFrom(push_config)
    if not blocking:
        request.configuration.return_immediately = True
    return request


def build_push_notification_config(
    push_notification_config: dict[str, object] | None,
) -> TaskPushNotificationConfig | None:
    if not push_notification_config:
        return None
    config = TaskPushNotificationConfig()
    url = push_notification_config.get("url")
    if url:
        config.url = str(url)
    token = push_notification_config.get("token")
    if token:
        config.token = str(token)
    return config


def build_get_task_request(task_id: str) -> GetTaskRequest:
    return GetTaskRequest(id=task_id)


def build_cancel_task_request(task_id: str) -> CancelTaskRequest:
    return CancelTaskRequest(id=task_id)


__all__ = [
    "build_cancel_task_request",
    "build_get_task_request",
    "build_push_notification_config",
    "build_send_message_request",
]
