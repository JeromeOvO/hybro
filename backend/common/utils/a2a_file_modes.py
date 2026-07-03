from __future__ import annotations

from typing import Any

FILE_CAPABLE_EXACT = frozenset({"file", "*/*"})
FILE_CAPABLE_PREFIXES = frozenset({"image/", "audio/", "video/"})
FILE_CAPABLE_MIMES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
        "application/zip",
        "application/x-tar",
        "application/gzip",
    }
)


def agent_input_modes(agent_card: Any) -> set[str]:
    if isinstance(agent_card, dict):
        raw = (
            agent_card.get("default_input_modes")
            or agent_card.get("defaultInputModes")
            or ["text"]
        )
    else:
        raw = getattr(agent_card, "default_input_modes", None)
        if raw is None:
            raw = getattr(agent_card, "defaultInputModes", None)
        if raw is None:
            raw = ["text"]
    return {str(mode).strip().lower() for mode in raw if str(mode).strip()}


def mime_type_is_accepted(
    mime_type: str,
    modes: set[str] | list[str] | tuple[str, ...],
) -> bool:
    mime = str(mime_type or "application/octet-stream").strip().lower()
    normalized_modes = {
        str(mode).strip().lower() for mode in modes if str(mode).strip()
    }
    if FILE_CAPABLE_EXACT & normalized_modes:
        return True
    if mime in normalized_modes:
        return True
    top_level = mime.split("/", 1)[0] if "/" in mime else ""
    if not top_level:
        return False
    return f"{top_level}/*" in normalized_modes or f"{top_level}/" in normalized_modes


def agent_accepts_required_input_modes(
    agent_card: Any,
    required_input_modes: list[str] | None,
) -> bool:
    if required_input_modes is None:
        return True
    if not required_input_modes:
        return agent_supports_any_file(agent_card)
    modes = agent_input_modes(agent_card)
    return all(
        mime_type_is_accepted(mime_type, modes) for mime_type in required_input_modes
    )


def _mode_is_mime_like_file_capability(mode: str) -> bool:
    if "/" not in mode or "://" in mode:
        return False
    top_level, subtype = mode.split("/", 1)
    return bool(top_level and (subtype or mode.endswith("/")))


def agent_supports_any_file(agent_card: Any) -> bool:
    modes = agent_input_modes(agent_card)
    if FILE_CAPABLE_EXACT & modes:
        return True
    if modes & FILE_CAPABLE_MIMES:
        return True
    return any(
        any(mode.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
        or _mode_is_mime_like_file_capability(mode)
        for mode in modes
    )
