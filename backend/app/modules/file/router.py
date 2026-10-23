"""File 模块: 统一文件上传/下载, 随机存储名+日期目录防路径穿越(roadmap 八十七).

下载执行对象级权限过滤(app/shared/fileperm.py): 未授权用户无法经文件 URL 下载私有论文(P3 验收).

D70: 上传补齐两项治理能力
- 单用户每日上传字节配额(配置项 max_upload_daily_mb, 默认 200MB), 超限抛 FileException(40013);
- business_type / business_id 可选业务归属(向后兼容: 不传仍可上传), 供后续孤儿文件清理使用。
  sys_file 表当前没有这两列(models 不在本批范围), 因此只落日志与响应体, 不落库。
"""
import logging
from datetime import datetime, time

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import FileException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import SysFile
from app.shared.fileperm import ensure_file_access
from app.shared.storage import read_upload, storage

log = logging.getLogger("app.file")

router = APIRouter(prefix="/files", tags=["File"])

# D70: 每日配额超限的显式业务码(40013 -> HTTP 400, 与 40010 单文件超限区分)
UPLOAD_QUOTA_EXCEEDED = 40013


def _used_today(db: Session, user_id: int) -> int:
    """该用户今日(服务器本地 00:00 起)已成功上传的字节总数(排除已软删)."""
    start = datetime.combine(datetime.now().date(), time.min)
    total = db.execute(
        select(func.coalesce(func.sum(SysFile.size), 0)).where(
            SysFile.uploader_id == user_id,
            SysFile.deleted_at.is_(None),
            SysFile.created_at >= start,
        )
    ).scalar()
    return int(total or 0)


def _ensure_daily_quota(db: Session, user_id: int, incoming: int) -> int:
    """日配额校验: 超限立刻拒绝(在落盘之前), 返回当前已用字节数."""
    from app.shared.sysconfig import get_int

    limit_mb = get_int(db, "max_upload_daily_mb", 200)
    used = _used_today(db, user_id)
    if limit_mb > 0:
        limit_bytes = limit_mb * 1024 * 1024
        if used + incoming > limit_bytes:
            raise FileException(
                f"今日上传配额已用尽: 上限 {limit_mb}MB, 已用 {used / 1048576:.2f}MB, "
                f"本次需 {incoming / 1048576:.2f}MB",
                UPLOAD_QUOTA_EXCEEDED,
            )
    return used


@router.post("/upload")
def upload_file(file: UploadFile,
                business_type: str | None = Form(default=None, max_length=32,
                                                 description="业务归属类型(可选, 如 TOPIC/OPENING)"),
                business_id: int | None = Form(default=None, ge=1,
                                               description="业务归属主键(可选)"),
                user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """D70: 上传带日配额与业务归属; 单文件上限仍由 max_upload_size_mb 控制."""
    from app.shared.sysconfig import get_int

    content, original_name, mime, size, sha256 = read_upload(file, get_int(db, "max_upload_size_mb", 50))
    used = _ensure_daily_quota(db, user.id, size)
    stored_name, storage_path = storage.save(content, original_name)
    record = SysFile(
        original_name=original_name, stored_name=stored_name,
        extension=original_name.rsplit(".", 1)[-1].lower() if "." in original_name else None,
        mime_type=mime, size=size, storage_type="LOCAL", storage_path=storage_path,
        sha256=sha256, uploader_id=user.id,
        # 显式写应用时钟: 日配额按"本地日历日"统计, 与 created_at 必须同一时钟
        # (纯 server_default 在 sqlite/UTC 下与本地 now() 差 8 小时, 00:00-08:00 区间会漏计)
        created_at=datetime.now(),
    )
    db.add(record)
    db.commit()
    business_type = (business_type or "").strip() or None
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
