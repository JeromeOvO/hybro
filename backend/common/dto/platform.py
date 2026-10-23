from common.dto.base import FrozenDTO


class FileInfo(FrozenDTO):
    file_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    url: str | None = None


__all__ = [
    "FileInfo",
]
