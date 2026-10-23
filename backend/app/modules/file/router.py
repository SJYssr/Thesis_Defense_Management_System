"""File 模块: 统一文件上传/下载, 随机存储名+日期目录防路径穿越(roadmap 八十七).

下载执行对象级权限过滤(app/shared/fileperm.py): 未授权用户无法经文件 URL 下载私有论文(P3 验收).

D70: 上传补齐两项治理能力
- 单用户每日上传字节配额(配置项 max_upload_daily_mb, 默认 200MB), 超限抛 FileException(40013);
- business_type / business_id 可选业务归属(向后兼容: 不传仍可上传), 供后续孤儿文件清理使用。
  sys_file 表当前没有这两列(models 不在本批范围), 因此只落日志与响应体, 不落库。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import SysFile
from app.shared.fileperm import ensure_file_access
from app.shared.storage import read_upload, storage
from app.shared.upload_policy import (
    UPLOAD_QUOTA_EXCEEDED,  # noqa: F401  兼容既有 from ... import 引用
    ensure_daily_quota,
    max_upload_size_mb,
    normalize_business,
    upload_policy,
)

log = logging.getLogger("app.file")

router = APIRouter(prefix="/files", tags=["File"])

@router.get("/upload-policy")
def get_upload_policy(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """上传策略(扩展名白名单 + 单文件上限 + 日配额), 前端据此生成 accept 与上传前校验.

    单一来源: 白名单来自 shared/storage.ALLOWED_EXTENSIONS, 上限来自系统配置。
    前端不再自行维护扩展名清单, 避免"accept 里有 .mp4 但后端拒收"这类错配。
    """
    return ResponseUtil.success(upload_policy(db))


@router.post("/upload")
def upload_file(file: UploadFile,
                business_type: str | None = Form(default=None, max_length=32,
                                                 description="业务归属类型(可选, 如 TOPIC/OPENING)"),
                business_id: int | None = Form(default=None, ge=1,
                                               description="业务归属主键(可选)"),
                user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """D70: 上传带日配额与业务归属; 单文件上限由系统配置 max_upload_size_mb 控制."""
    content, original_name, mime, size, sha256 = read_upload(file, max_upload_size_mb(db))
    used = ensure_daily_quota(db, user.id, size)
    business_type, business_id = normalize_business(business_type, business_id)
    stored_name, storage_path = storage.save(content, original_name)
    record = SysFile(
        original_name=original_name, stored_name=stored_name,
        extension=original_name.rsplit(".", 1)[-1].lower() if "." in original_name else None,
        mime_type=mime, size=size, storage_type="LOCAL", storage_path=storage_path,
        sha256=sha256, uploader_id=user.id,
        business_type=business_type, business_id=business_id,
        # 显式写应用时钟: 日配额按"本地日历日"统计, 与 created_at 必须同一时钟
        # (纯 server_default 在 sqlite/UTC 下与本地 now() 差 8 小时, 00:00-08:00 区间会漏计)
        created_at=datetime.now(),
    )
    db.add(record)
    db.commit()
    log.info("file upload id=%s uploader=%s size=%s business_type=%s business_id=%s",
             record.id, user.id, size, business_type, business_id)
    return ResponseUtil.success({
        "id": record.id, "original_name": record.original_name, "size": record.size,
        "extension": record.extension, "url": f"/api/v1/files/{record.id}/download",
        "business_type": business_type, "business_id": business_id,
        "daily_used_bytes": used + size,
    })


@router.get("/{file_id}/download")
def download_file(file_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.get(SysFile, file_id)
    if record is None or record.deleted_at is not None:
        raise ResourceNotFoundException("文件不存在")
    ensure_file_access(db, user, record)
    path = storage.abs_path(record.storage_path)
    if not path.exists():
        raise ResourceNotFoundException("文件已丢失")
    return FileResponse(path, filename=record.original_name)
