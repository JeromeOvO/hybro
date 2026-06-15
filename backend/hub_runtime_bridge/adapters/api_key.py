from __future__ import annotations

from typing import Any


def owner_id_from_api_key(api_key: Any) -> str:
    owner_id = getattr(api_key, "user_id", None)
    if not owner_id:
        raise PermissionError("API key has no owner")
    return owner_id


__all__ = ["owner_id_from_api_key"]
