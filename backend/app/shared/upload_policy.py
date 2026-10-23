"""上传策略单源: 扩展名白名单、单文件上限、日配额、业务归属.

此前 /files/upload 与 /student/material/files 各自实现, 导致:
- 材料上传用 `read_upload(file)` 的默认值, 无视后台系统设置里的 max_upload_size_mb;
- 材料上传完全不校验日配额, 成为绕过配额的口子;
- 两处 SysFile 写入字段不一致(storage_type / created_at)。

现在两处共用本模块, 口径由系统配置(shared/sysconfig)决定。
"""
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import FileException
from app.models.system import SysFile
from app.shared.storage import (
    ALLOWED_EXTENSIONS,
    ARCHIVE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
)

# 日配额超限的显式业务码(40013 -> HTTP 400, 与 40010 单文件超限区分)
UPLOAD_QUOTA_EXCEEDED = 40013

DEFAULT_MAX_FILE_MB = 50
DEFAULT_DAILY_MB = 200


def max_upload_size_mb(db: Session) -> int:
    """单文件上限(MB): 读系统配置, 缺省回退 DefaultFileMB."""
    from app.shared.sysconfig import get_int

    return get_int(db, "max_upload_size_mb", DEFAULT_MAX_FILE_MB)


def daily_limit_mb(db: Session) -> int:
    """单用户每日上传总量上限(MB), <=0 表示不限制."""
    from app.shared.sysconfig import get_int

    return get_int(db, "max_upload_daily_mb", DEFAULT_DAILY_MB)


def used_today(db: Session, user_id: int) -> int:
    """该用户今日(服务器本地 00:00 起)已成功上传的字节总数(排除已软删).

    依赖 SysFile.created_at 用应用时钟写入: 若走数据库 server_default,
    在 sqlite/UTC 部署下会与本地 now() 相差时区, 00:00-08:00 区间漏计。
    """
    start = datetime.combine(datetime.now().date(), time.min)
    total = db.execute(
        select(func.coalesce(func.sum(SysFile.size), 0)).where(
            SysFile.uploader_id == user_id,
            SysFile.deleted_at.is_(None),
            SysFile.created_at >= start,
        )
    ).scalar()
    return int(total or 0)


def ensure_daily_quota(db: Session, user_id: int, incoming: int) -> int:
    """日配额校验: 超限立刻拒绝(在落盘之前), 返回当前已用字节数."""
    limit_mb = daily_limit_mb(db)
    used = used_today(db, user_id)
    if limit_mb > 0 and used + incoming > limit_mb * 1024 * 1024:
        raise FileException(
            f"今日上传配额已用尽: 上限 {limit_mb}MB, 已用 {used / 1048576:.2f}MB, "
            f"本次需 {incoming / 1048576:.2f}MB",
            UPLOAD_QUOTA_EXCEEDED,
        )
    return used


def normalize_business(business_type: str | None, business_id: int | None) -> tuple[str | None, int | None]:
    """规整业务归属: 类型去空格并限长, 空串视为未提供."""
    cleaned = (business_type or "").strip()[:32] or None
    return cleaned, business_id


def upload_policy(db: Session) -> dict:
    """暴露给前端的上传策略(前端据此生成 accept 与上传前大小校验)."""
    return {
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        "by_kind": {
            "DOCUMENT": sorted(DOCUMENT_EXTENSIONS),
            "ARCHIVE": sorted(ARCHIVE_EXTENSIONS),
            "IMAGE": sorted(IMAGE_EXTENSIONS),
            "MEDIA": sorted(MEDIA_EXTENSIONS),
        },
        "max_upload_size_mb": max_upload_size_mb(db),
        "max_upload_daily_mb": daily_limit_mb(db),
    }
