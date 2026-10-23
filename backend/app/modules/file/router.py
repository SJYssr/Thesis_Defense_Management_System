"""File 模块: 统一文件上传/下载, 随机存储名+日期目录防路径穿越(roadmap 八十七).

下载执行对象级权限过滤(app/shared/fileperm.py): 未授权用户无法经文件 URL 下载私有论文(P3 验收).

D70: 上传补齐两项治理能力
- 单用户每日上传字节配额(配置项 max_upload_daily_mb, 默认 200MB), 超限抛 FileException(40013);
- business_type / business_id 可选业务归属(向后兼容: 不传仍可上传)。
  这两列已落库(迁移 0003, 见 models/system.py), 供孤儿文件清理判定业务归属;
  business_type 为空的记录才是清理候选, 非空(如 MATERIAL/ARCHIVE)一条都不碰,
  清理实现见 app/tasks/file_gc.py, 管理员手动入口 POST /api/v1/admin/files/gc。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import OperationLog, SysFile
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

# 前缀下沉到各路由路径: 本模块要同时承载 /files/* 与 /admin/files/gc, 而 admin 端点
# 不能挂在 /files 前缀下(那会变成 /files/admin/files/gc)。写法与 archive/task/sysconfig/
# defense 等模块一致, 既有 URL 逐字不变(smoke_test 仍打 /files/upload-policy 等)。
router = APIRouter(tags=["File"])

@router.get("/files/upload-policy")
def get_upload_policy(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """上传策略(扩展名白名单 + 单文件上限 + 日配额), 前端据此生成 accept 与上传前校验.

    单一来源: 白名单来自 shared/storage.ALLOWED_EXTENSIONS, 上限来自系统配置。
    前端不再自行维护扩展名清单, 避免"accept 里有 .mp4 但后端拒收"这类错配。
    """
    return ResponseUtil.success(upload_policy(db))


@router.post("/files/upload")
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


@router.get("/files/{file_id}/download")
def download_file(file_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.get(SysFile, file_id)
    if record is None or record.deleted_at is not None:
        raise ResourceNotFoundException("文件不存在")
    ensure_file_access(db, user, record)
    path = storage.abs_path(record.storage_path)
    if not path.exists():
        raise ResourceNotFoundException("文件已丢失")
    return FileResponse(path, filename=record.original_name)


@router.post("/admin/files/gc")
def files_gc(retention_days: int = Query(7, ge=1, le=365, description="保留期(天): 早于该期限且无引用的孤儿文件才清理"),
             dry_run: bool = Query(True, description="true 只统计不删(默认), false 才删记录与物理文件"),
             user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """P9-B 孤儿文件清理(管理员手动触发), 与每日 02:30 Celery 任务共用同一实现.

    单一实现: app/tasks/file_gc.collect(db, retention_days, dry_run); 本端点只负责鉴权、
    留痕与响应包装。dry_run=true 只统计; dry_run=false 才删记录与物理文件。
    """
    from app.tasks.file_gc import collect

    data = collect(db, retention_days=retention_days, dry_run=dry_run)
    # 手动清理是写操作: 留一条 operation_log; 逐文件的留痕在 tasks/file_gc 里走 logger.info(避免刷表)
    db.add(OperationLog(user_id=user.id, username=user.username, module="FILE", action="GC",
                        detail=f"retention_days={retention_days} dry_run={dry_run} deleted={data['deleted']}"))
    db.commit()
    return ResponseUtil.success(data, message="孤儿文件清理试运行完成" if dry_run else "孤儿文件清理完成")
