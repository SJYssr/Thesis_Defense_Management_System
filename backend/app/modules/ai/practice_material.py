"""学生从选题通过起即可上传私有练习材料。"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.exceptions import FileException
from app.core.permissions import CurrentUser, require_student
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIDocument
from app.models.enums import AIDocumentType
from app.models.system import SysFile
from app.modules.ai.router import _check_ai_rate
from app.shared.storage import pathlib_suffix, read_upload, storage
from app.shared.upload_policy import PRACTICE_EXTENSIONS, ensure_daily_quota, max_upload_size_mb
from app.shared.workflow import GraduationWorkflowService
from app.tasks.dispatcher import index_uploaded_material

router = APIRouter(prefix="/ai/defense/materials", tags=["AI-Defense"])


@router.post("")
def upload_practice_material(file: UploadFile, background_tasks: BackgroundTasks,
                             kind: Literal["REPORT", "SOURCE_CODE"] = Query(...),
                             user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    selection = GraduationWorkflowService.require_selection_approved(db, user.profile_id)
    _check_ai_rate(user, db)
    extension = pathlib_suffix(file.filename)
    if extension not in PRACTICE_EXTENSIONS[kind]:
        raise FileException("此类练习材料支持 " + "、".join(sorted(PRACTICE_EXTENSIONS[kind])))
    content, name, mime, size, digest = read_upload(file, max_upload_size_mb(db))
    if not size or len(name) > 255:
        raise FileException("请上传非空文件，文件名最多 255 个字符")
    ensure_daily_quota(db, user.id, size)
    stored_name, path = storage.save(content, name)
    try:
        record = SysFile(original_name=name, stored_name=stored_name, extension=extension.lstrip("."),
                         mime_type=mime[:128], size=size, storage_type="LOCAL", storage_path=path,
                         sha256=digest, uploader_id=user.id, business_type="AI_PRACTICE",
                         business_id=selection.cycle_id, created_at=datetime.now())
        db.add(record)
        db.flush()
        doc = AIDocument(student_id=user.profile_id, topic_id=selection.topic_id, cycle_id=selection.cycle_id,
                         document_type=(AIDocumentType.PRACTICE_REPORT if kind == "REPORT" else AIDocumentType.SOURCE_CODE).value,
                         document_name=name, file_id=record.id, scope="STUDENT", index_status="WAITING", version=1)
        db.add(doc)
        db.flush()
        document_id = doc.id
        db.commit()
    except Exception:
        db.rollback()
        storage.abs_path(path).unlink(missing_ok=True)
        raise
    background_tasks.add_task(index_uploaded_material, document_id)
    return ResponseUtil.success({"id": document_id, "index_status": "WAITING", "document_name": name})
