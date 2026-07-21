"""Public projection helpers for agent-originated HITL prompts."""

from __future__ import annotations

import re

GENERIC_AGENT_INPUT_PROMPT = "The agent needs additional information."
_MAX_PUBLIC_AGENT_PROMPT_CHARS = 1200
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PRIVATE_MARKERS = (
    "PRIVATE_SENTINEL",
    "[SYSTEM]",
    "<system>",
    "BEGIN SYSTEM PROMPT",
    "END SYSTEM PROMPT",
)


def safe_agent_input_prompt(prompt: str | None) -> str | None:
    """Return a bounded user-facing question, or None when it is not public."""
    if not isinstance(prompt, str):
        return None
    normalized = " ".join(_CONTROL_CHARS.sub("", prompt).split()).strip()
    if not normalized or len(normalized) > _MAX_PUBLIC_AGENT_PROMPT_CHARS:
        return None
    upper = normalized.upper()
    if any(marker.upper() in upper for marker in _PRIVATE_MARKERS):
        return None
    return normalized


def public_agent_input_prompt(prompt: str | None) -> str:
    """Return a bounded user-facing question or the generic safe fallback."""
    return safe_agent_input_prompt(prompt) or GENERIC_AGENT_INPUT_PROMPT
