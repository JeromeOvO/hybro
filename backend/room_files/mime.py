from __future__ import annotations

import re

DEFAULT_MIME_TYPE = "application/octet-stream"
_MIME_TYPE_PATTERN = re.compile(r"^[a-z0-9!#$%&'*+.^_`|~-]+/[a-z0-9!#$%&'*+.^_`|~-]+$")


def normalize_mime_type(value: str | None) -> str:
    candidate = (value or "").split(";", 1)[0].strip().lower()
    if not _MIME_TYPE_PATTERN.fullmatch(candidate):
        return DEFAULT_MIME_TYPE
    return candidate
