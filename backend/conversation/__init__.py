"""Conversation container: messages, membership, timeline, and turn context."""

from conversation.candidate_scope import (
    SUPPORTED_CANDIDATE_SCOPE_SOURCES,
    candidate_scope_from_envelope,
    normalize_candidate_scope,
)

__all__ = [
    "SUPPORTED_CANDIDATE_SCOPE_SOURCES",
    "candidate_scope_from_envelope",
    "normalize_candidate_scope",
]
