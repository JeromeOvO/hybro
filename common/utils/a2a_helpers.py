"""Shared utilities for extracting content from A2A Task/Message objects.

These are stateless, pure functions used by both WorkflowCenter
and RoomMessageCenter.
"""

import uuid

from a2a.types import Message, Role, Task

from common.utils.logger import get_logger

logger = get_logger(__name__)


def get_text_from_a2a_response(result: Task | Message) -> str:
    """Extract text content from an A2A response (Task or Message).

    Args:
        result: A Task or Message object from A2A response

    Returns:
        Extracted text as a string, or empty string if no text found
    """
    if result.kind == "message" and hasattr(result, "parts") and result.parts:
        return get_text_from_message(result)
    elif result.kind == "task":
        message = get_message_from_task(result)
        return get_text_from_message(message) if message else ""
    return ""


def get_message_from_task(task: Task) -> Message | None:
    """Extract message from a Task object.

    Per A2A spec, task outputs should be in artifacts. We check:
    1. task.artifacts - A2A-compliant location for task outputs
    2. task.status.message - status messages (for compatibility)
    3. task.history - conversation history (fallback)
    """
    # Check task.artifacts first (A2A-compliant: task outputs go in artifacts)
    if task.artifacts:
        all_parts = []
        for artifact in task.artifacts:
            # Artifact.parts is a list of Part objects
            all_parts.extend(artifact.parts)
        if all_parts:
            logger.debug("Found %d parts in task.artifacts", len(all_parts))
            message = Message(
                role=Role.agent,
                message_id=str(uuid.uuid4()),
                task_id=task.id,
                parts=all_parts,
            )
            return message

    # Check task.status.message (for status updates, less common for final output)
    if task.status and task.status.message:
        logger.debug("Found message in task.status.message")
        return task.status.message

    # Check task.history for the last agent message (fallback)
    if task.history:
        for msg in reversed(task.history):
            if hasattr(msg, "role") and msg.role == Role.agent:
                logger.debug("Found agent message in task.history")
                return msg

    logger.warning("No message found in task %s", task.id)
    return None


def get_text_from_message(message: Message | None) -> str:
    """Extract text from a Message object.

    Args:
        message: An A2A Message, or None

    Returns:
        Concatenated text from all text parts, or empty string
    """
    if message is None:
        return ""
    return "".join(
        part.root.text if part.root and hasattr(part.root, "text") else ""
        for part in message.parts
    )


def extract_text_from_artifacts(artifacts: list) -> str | None:
    """Extract text content from A2A artifacts with robust type handling."""
    texts = []
    for artifact in artifacts:
        if not artifact.parts:
            continue
        for part in artifact.parts:
            # Handle different part type structures
            text = None
            if hasattr(part, "text") and part.text:
                text = part.text
            elif hasattr(part, "root"):
                # Discriminated union wrapper
                root = part.root
                if hasattr(root, "text") and root.text:
                    text = root.text
            if text:
                texts.append(text)
    return "".join(texts) if texts else None


def extract_error_message(task: Task) -> str | None:
    """Extract error message from task status."""
    if not task.status.message:
        return None
    if not task.status.message.parts:
        return None
    for part in task.status.message.parts:
        if hasattr(part, "text") and part.text:
            return part.text
        if hasattr(part, "root") and hasattr(part.root, "text"):
            return part.root.text
    return None


def extract_status_message(task: Task) -> str | None:
    """Extract human-readable status message."""
    return extract_error_message(task)  # Same extraction logic
