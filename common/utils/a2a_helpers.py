"""Shared utilities for extracting content from A2A Task/Message objects.

These are stateless, pure functions used by both WorkflowCenter
and RoomMessageCenter.
"""

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol

from common.utils.logger import get_logger

logger = get_logger(__name__)


class A2AArtifactStorage(Protocol):
    async def convert_inline_bytes_to_s3(
        self,
        parts: list[dict],
        room_id: str,
        message_id: str,
        *,
        converted_so_far: int = 0,
    ) -> int: ...

    async def convert_pydantic_artifacts_to_s3(
        self,
        artifacts: list,
        room_id: str,
        message_id: str,
        *,
        converted_so_far: int = 0,
    ) -> int: ...


a2a_artifact_storage: A2AArtifactStorage | None = None


def bind_a2a_artifact_storage(storage: A2AArtifactStorage) -> None:
    global a2a_artifact_storage

    a2a_artifact_storage = storage


def _require_a2a_artifact_storage() -> A2AArtifactStorage:
    if a2a_artifact_storage is None:
        raise RuntimeError("A2A artifact storage dependency has not been bound")
    return a2a_artifact_storage


def _parts_need_artifact_storage(parts: list[dict]) -> bool:
    for part in parts:
        if part.get("kind") != "file":
            continue
        file_info = part.get("file")
        if not isinstance(file_info, dict):
            continue
        if file_info.get("bytes") or file_info.get("uri"):
            return True
    return False


def _artifacts_need_artifact_storage(artifacts: list) -> bool:
    for artifact in artifacts:
        for part in getattr(artifact, "parts", []) or []:
            root = getattr(part, "root", part)
            if getattr(root, "kind", None) != "file":
                continue
            file_info = getattr(root, "file", None)
            if file_info and (
                getattr(file_info, "bytes", None) or getattr(file_info, "uri", None)
            ):
                return True
    return False


@dataclass
class ExtractedParts:
    """Structured extraction result from A2A parts."""

    text_parts: list[str] = field(default_factory=list)
    file_parts: list[dict] = field(default_factory=list)
    data_parts: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def has_non_text(self) -> bool:
        return bool(self.file_parts or self.data_parts)


def extract_parts(parts: list) -> ExtractedParts:
    """Extract and classify all parts from an A2A parts list.

    Handles both direct part objects and discriminated union wrappers (part.root).
    """
    result = ExtractedParts()
    for part in parts:
        root = getattr(part, "root", part)
        kind = getattr(root, "kind", None)
        if not isinstance(kind, str):
            direct_kind = getattr(part, "kind", None)
            direct_text = getattr(part, "text", None)
            if isinstance(direct_kind, str) or isinstance(direct_text, str):
                root = part
                kind = direct_kind

        if kind == "text":
            text = getattr(root, "text", None)
            if isinstance(text, str) and text:
                result.text_parts.append(text)
        elif kind == "file":
            result.file_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        elif kind == "data":
            result.data_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        else:
            text = getattr(root, "text", None)
            if isinstance(text, str) and text:
                result.text_parts.append(text)
            else:
                logger.warning("Unknown part kind=%s, skipping", kind)
    return result


def extract_parts_from_artifacts(artifacts: list) -> ExtractedParts:
    """Extract parts from a list of A2A artifacts."""
    result = ExtractedParts()
    for artifact in artifacts:
        if not artifact.parts:
            continue
        artifact_parts = extract_parts(artifact.parts)
        result.text_parts.extend(artifact_parts.text_parts)
        result.file_parts.extend(artifact_parts.file_parts)
        result.data_parts.extend(artifact_parts.data_parts)
    return result


def get_text_from_a2a_response(result: Any) -> str:
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


def get_message_from_task(task: Any) -> Any | None:
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
            message = SimpleNamespace(
                kind="message",
                role="agent",
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
            role = getattr(msg, "role", None)
            if getattr(role, "value", role) == "agent":
                logger.debug("Found agent message in task.history")
                return msg

    logger.warning("No message found in task %s", task.id)
    return None


def get_text_from_message(message: Any | None) -> str:
    """Extract text from a Message object. Backward-compatible wrapper."""
    if message is None:
        return ""
    return extract_parts(message.parts).text


def extract_text_from_artifacts(artifacts: list) -> str | None:
    """Extract text content from A2A artifacts. Backward-compatible wrapper."""
    text = extract_parts_from_artifacts(artifacts).text
    return text if text else None


def extract_agent_text_from_room_message(msg: object) -> str | None:
    """Safely extract the agent's latest response text from a RoomAgentMessage.

    Checks two storage locations in priority order:
    1. Agent-role entries in ``message_content.message_task.history`` (most
       reliable for push-notification and streaming agents).
    2. ``message_content.message_text`` (populated by the DirectTransport sync
       path, which does not append to history).

    All parts of the latest agent-role message are joined so that multi-part
    responses are preserved (e.g. reasoning part + answer part).

    Returns ``None`` when no text can be found; never raises.
    """
    try:
        mc = getattr(msg, "message_content", None)
        if mc is None:
            return None

        task = getattr(mc, "message_task", None)
        if task is not None:
            history = getattr(task, "history", None)
            if history:
                for entry in reversed(history):
                    role = getattr(entry, "role", None)
                    if getattr(role, "value", role) == "agent":
                        parts = getattr(entry, "parts", None)
                        if parts:
                            text = extract_parts(parts).text
                            if text:
                                return text

        message_text = getattr(mc, "message_text", None)
        if message_text:
            return message_text

        return None
    except (AttributeError, IndexError, TypeError):
        return None


def extract_error_message(task: Any) -> str | None:
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


def extract_status_message(task: Any) -> str | None:
    """Extract human-readable status message."""
    return extract_error_message(task)  # Same extraction logic


def task_has_visible_content(task: Any) -> bool:
    """Return True when task has user-visible output (text or non-text parts)."""
    if not task.artifacts:
        return False
    extracted = extract_parts_from_artifacts(task.artifacts)
    return bool(extracted.text or extracted.has_non_text)


def sanitize_artifact_parts(parts: list[dict]) -> list[dict]:
    """Remove malformed part dicts before persisting to MongoDB.

    Each A2A Part variant requires its discriminator + payload:
      - TextPart:  kind='text' + text (str)
      - FilePart:  kind='file' + file (dict)
      - DataPart:  kind='data' + data (dict)

    ``text`` / ``file`` / ``data`` must be present and non-None; otherwise
    Pydantic rejects the whole Task on read (e.g. ``{"kind": "text"}`` or
    ``{"kind": "text", "text": null}``).

    Returns a new list with invalid entries stripped.
    """
    cleaned: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            logger.warning("Dropping non-dict artifact part: %r", p)
            continue
        root = p.get("root", p)
        if not isinstance(root, dict):
            logger.warning("Dropping artifact part with non-dict root: %r", p)
            continue
        kind = root.get("kind")
        if kind == "text":
            if "text" not in root or root.get("text") is None:
                logger.debug("Dropping malformed TextPart (missing or null 'text')")
                continue
        elif kind == "file":
            if "file" not in root or root.get("file") is None:
                logger.warning("Dropping malformed FilePart (missing or null 'file')")
                continue
        elif kind == "data":
            if "data" not in root or root.get("data") is None:
                logger.warning("Dropping malformed DataPart (missing or null 'data')")
                continue
        elif not any(k in root for k in ("text", "file", "data", "url", "raw")):
            logger.warning("Dropping unrecognizable artifact part: %r", p)
            continue
        cleaned.append(p)
    return cleaned


def append_artifact_to_task_dict(
    existing_artifacts: list[dict] | None,
    new_artifact: dict,
    append: bool = False,
) -> list[dict]:
    """Append or merge an artifact into an existing artifacts list per A2A spec.

    Implements the A2A artifact streaming semantics:
    - If append=False: Create new artifact or replace existing with same artifactId
    - If append=True: Extend parts of existing artifact with same artifactId

    Args:
        existing_artifacts: Current list of artifact dicts (may be None)
        new_artifact: The new artifact dict to add/merge
        append: If True, extend parts of existing artifact; if False, replace/create

    Returns:
        Updated list of artifact dicts
    """
    if existing_artifacts is None:
        existing_artifacts = []

    artifact_id = new_artifact.get("artifactId") or new_artifact.get("artifact_id")
    if not artifact_id:
        logger.warning("Artifact missing artifactId, appending as new artifact")
        existing_artifacts.append(new_artifact)
        return existing_artifacts

    existing_index = None
    for i, art in enumerate(existing_artifacts):
        art_id = art.get("artifactId") or art.get("artifact_id")
        if art_id == artifact_id:
            existing_index = i
            break

    if not append:
        if existing_index is not None:
            logger.debug("Replacing artifact at id %s", artifact_id)
            existing_artifacts[existing_index] = new_artifact
        else:
            logger.debug("Adding new artifact with id %s", artifact_id)
            existing_artifacts.append(new_artifact)
    elif existing_index is not None:
        logger.debug("Appending parts to artifact id %s", artifact_id)
        existing_parts = existing_artifacts[existing_index].get("parts", [])
        new_parts = new_artifact.get("parts", [])
        existing_artifacts[existing_index]["parts"] = existing_parts + new_parts
    else:
        logger.warning(
            "Received append=True for nonexistent artifact id %s. Creating new artifact.",
            artifact_id,
        )
        existing_artifacts.append(new_artifact)

    return existing_artifacts


async def convert_inline_bytes_to_s3(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    if not _parts_need_artifact_storage(parts):
        return converted_so_far

    storage = _require_a2a_artifact_storage()
    return await storage.convert_inline_bytes_to_s3(
        parts,
        room_id,
        message_id,
        converted_so_far=converted_so_far,
    )


async def convert_pydantic_artifacts_to_s3(
    artifacts: list,
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    if not _artifacts_need_artifact_storage(artifacts):
        return converted_so_far

    storage = _require_a2a_artifact_storage()
    return await storage.convert_pydantic_artifacts_to_s3(
        artifacts,
        room_id,
        message_id,
        converted_so_far=converted_so_far,
    )
