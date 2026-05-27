from __future__ import annotations


class LocalHubOwnershipMap:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    def bind(self, owner_id: str, *aliases: str | None) -> None:
        for alias in aliases:
            if alias:
                self._owners[alias] = owner_id

    def owner_for(self, *aliases: str | None) -> str | None:
        for alias in aliases:
            if alias and alias in self._owners:
                return self._owners[alias]
        return None

    def release(self, *aliases: str | None) -> None:
        for alias in aliases:
            if alias:
                self._owners.pop(alias, None)


__all__ = ["LocalHubOwnershipMap"]
