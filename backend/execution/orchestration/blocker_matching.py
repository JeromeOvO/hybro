from __future__ import annotations

import re


def normalize_match_text(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", value.lower()))


def match_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_match_text(value).split("_")
        if token
    }


def agent_blocker_field_key(blocker_key: str) -> str | None:
    prefix, separator, remainder = blocker_key.partition(":")
    if prefix != "agent_blocker" or not separator:
        return None
    _, agent_separator, field_key = remainder.partition(":")
    if not agent_separator or not field_key:
        return None
    return field_key
