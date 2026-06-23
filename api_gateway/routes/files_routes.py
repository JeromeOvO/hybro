from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.params import Depends as DependsParam

from api_gateway.dependencies import get_file_storage, get_room_ownership_reader
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.auth import ClerkUser, get_current_user
from common.errors import FileStoragePlatformError
from common.protocols import FileStorage, RoomOwnershipReader
from models.file_upload import FileUploadResponse

router = APIRouter(prefix="/files", tags=["files"])


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    room_id: str = Form(...),
    user: ClerkUser = Depends(get_current_user),
    storage: FileStorage = Depends(get_file_storage),
    room_ownership: RoomOwnershipReader = Depends(get_room_ownership_reader),
):
    """Upload a file to S3 for attachment to a room message.

    Accepts multipart/form-data with:
    - file: The file to upload
    - room_id: The room this file belongs to

    Returns FileUploadResponse with file_id and presigned URL.
    """
    storage = _resolve_dependency(storage, get_file_storage)
    room_ownership = _resolve_dependency(room_ownership, get_room_ownership_reader)

    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    owner_id = await room_ownership.get_room_owner(room_id)
    if owner_id is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if owner_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this room",
        )

    try:
        uploaded = await storage.upload(
            file_bytes=await file.read(),
            filename=file.filename or "unnamed",
            owner_id=user.user_id,
            room_id=room_id,
            content_type=file.content_type,
        )
    except FileStoragePlatformError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    return FileUploadResponse(
        file_id=uploaded.file_id,
        file_url=uploaded.url or "",
        mime_type=uploaded.mime_type,
        file_name=uploaded.file_name,
        size_bytes=uploaded.size_bytes,
    )


_mark_declared_owner(router, __name__)
