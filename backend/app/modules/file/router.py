"""File 模块: 统一文件上传/下载, 随机存储名+日期目录防路径穿越(roadmap 八十七)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import SysFile
from app.shared.storage import read_upload, storage

router = APIRouter(prefix="/files", tags=["File"])


@router.post("/upload")
def upload_file(file: UploadFile, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    content, original_name, mime, size, sha256 = read_upload(file)
    stored_name, storage_path = storage.save(content, original_name)
    record = SysFile(
        original_name=original_name, stored_name=stored_name,
        extension=original_name.rsplit(".", 1)[-1].lower() if "." in original_name else None,
        mime_type=mime, size=size, storage_type="LOCAL", storage_path=storage_path,
        sha256=sha256, uploader_id=user.id,
    )
    db.add(record)
    db.commit()
    return ResponseUtil.success({
        "id": record.id, "original_name": record.original_name, "size": record.size,
        "extension": record.extension, "url": f"/api/v1/files/{record.id}/download",
    })


@router.get("/{file_id}/download")
def download_file(file_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.get(SysFile, file_id)
    if record is None or record.deleted_at is not None:
        raise ResourceNotFoundException("文件不存在")
    path = storage.abs_path(record.storage_path)
    if not path.exists():
        raise ResourceNotFoundException("文件已丢失")
    return FileResponse(path, filename=record.original_name)
