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
_INTERNAL_CONTRACT_FORMAT = re.compile(
    r"\b(?:json(?:\s+object)?|application/json|data\s*part|"
    r"structured(?:\s+data)?\s+payload|schema(?:-conformant)?\s+payload|"
    r"machine-readable\s+payload)\b",
    re.I,
)
_INTERNAL_CONTRACT_FIELD = re.compile(
    r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b",
    re.I,
)
_INTERNAL_CONTRACT_IMPERATIVE = re.compile(
    r"\b(?:send|provide|include|containing|return|supply|pass)\b",
    re.I,
)
_FILE_UPLOAD_VERB = re.compile(
    r"\b(?:upload(?:s|ing)?|attach(?:es|ing)?)\b",
    re.I,
)
_FILE_UPLOAD_NOUN = re.compile(r"\b(?:pdfs?|files?|documents?)\b", re.I)
_NON_FILE_ALTERNATIVE = re.compile(
    r"\b(?:paste|type|enter|describe|"
    r"provide\s+(?:the\s+)?(?:details?|information|text)|"
    r"share\s+(?:the\s+)?(?:details?|information|text))\b",
    re.I,
)
_FILE_UPLOAD_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|cannot|can't|unable\s+to|not\s+able\s+to)\s+"
    r"(?:upload|attach)\b",
    re.I,
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


def is_internal_agent_contract_prompt(prompt: str | None) -> bool:
    """Return whether an Agent is requesting a private transport/schema payload."""
    safe_prompt = safe_agent_input_prompt(prompt)
    if safe_prompt is None:
        return False
    if _INTERNAL_CONTRACT_FORMAT.search(safe_prompt):
        return True
    dotted_fields = _INTERNAL_CONTRACT_FIELD.findall(safe_prompt)
    return bool(dotted_fields and _INTERNAL_CONTRACT_IMPERATIVE.search(safe_prompt))


def concrete_agent_input_prompt(prompt: str | None) -> str | None:
    """Return a concrete public question, rejecting private contract requests."""
    safe_prompt = safe_agent_input_prompt(prompt)
    if safe_prompt is None or is_internal_agent_contract_prompt(safe_prompt):
        return None
    if safe_prompt.casefold() == GENERIC_AGENT_INPUT_PROMPT.casefold():
        return None
    return safe_prompt


def is_file_upload_request(
    prompt: str | None,
    *,
    prompt_type: str | None = None,
) -> bool:
    """Return whether a prompt exclusively blocks on a file upload.

    Typed ``file`` prompts are authoritative. Untyped agent prompts use a
    deliberately conservative classifier: both an upload/attach verb and a
    file/PDF/document noun are required, and prompts offering a text-based
    alternative remain ordinary HITL questions.
    """
    if isinstance(prompt_type, str) and prompt_type.strip().casefold() == "file":
        return True
    safe_prompt = safe_agent_input_prompt(prompt)
    if safe_prompt is None:
        return False
    if _FILE_UPLOAD_NEGATION.search(safe_prompt):
        return False
    if re.search(r"\bor\b", safe_prompt, re.I) and _NON_FILE_ALTERNATIVE.search(
        safe_prompt
    ):
        return False
    return bool(
        _FILE_UPLOAD_VERB.search(safe_prompt) and _FILE_UPLOAD_NOUN.search(safe_prompt)
    )


def public_agent_input_prompt(prompt: str | None) -> str:
    """Return a bounded public question or a non-actionable safe fallback."""
    return concrete_agent_input_prompt(prompt) or GENERIC_AGENT_INPUT_PROMPT
