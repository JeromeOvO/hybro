from fastapi import APIRouter, Depends, Form, UploadFile

from api.room_center import verify_room_ownership
from common.auth import ClerkUser, get_current_user
from services.file_upload_service import file_upload_service

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    room_id: str = Form(...),
    user: ClerkUser = Depends(get_current_user),
):
    """Upload a file to S3 for attachment to a room message.

    Accepts multipart/form-data with:
    - file: The file to upload
    - room_id: The room this file belongs to

    Returns FileUploadResponse with file_id and presigned URL.
    """
    await verify_room_ownership(room_id, user)

    return await file_upload_service.upload(
        file=file,
        room_id=room_id,
        user_id=user.user_id,
    )
