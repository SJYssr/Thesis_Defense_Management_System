"""P9-B 孤儿文件清理 —— 唯一实现: collect(db, retention_days, dry_run).

两条执行路径共用本实现(参照 app/tasks/risk_scan.py 的"唯一实现 + 双路径"范式):
- Celery Beat 每日 02:30: app.tasks.file_gc.daily_gc (retention_days=7, dry_run=False);
- 管理员手动触发: POST /api/v1/admin/files/gc (默认 dry_run=True, 只统计不删).

判定口径(顺序即优先级, 全部下推 SQL, 不做 N+1):
  scanned            = business_type IS NULL AND deleted_at IS NULL
  skipped_recent     = 上述集合中 created_at >= now - retention_days (未到期, 一律不删)
  skipped_referenced = 上述集合中已到期但被 4 张表任一 file_id 引用
  deletable          = business_type IS NULL AND deleted_at IS NULL
                       AND created_at < now - retention_days AND 无引用
business_type 非空的记录(如 MATERIAL/ARCHIVE)任何情况都不参与扫描, 一条都不碰。

明细 items 的 reason 词表:
  - dry_run=True : "too_recent" / "referenced" / "deletable"(可删候选, 供管理员核对将删什么)
  - dry_run=False: "too_recent" / "referenced" / "deleted"(同一条只出现一次, 不再标 deletable)
deletable 计数键在 dry_run 与实删下都返回(dry_run 看规模, 实删后等于 deleted)。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.shared.storage import storage

logger = logging.getLogger("app.tasks.file_gc")

DEFAULT_RETENTION_DAYS = 7
GC_BATCH_SIZE = 200   # 每批一个事务, 单批失败不影响已提交批次
GC_MAX_ITEMS = 50     # 响应明细条数上限


def default_session_factory():
    """清理专用 Session 来源; 测试可注入替身(每批独立事务)."""
    from app.db.mysql import SessionLocal

    return SessionLocal()


def referencing_models():
    """引用 sys_file.id 的 4 张表(新增引用方必须同步加进这里)."""
    from app.models.ai import AIDocument
    from app.models.business import GraduationMaterialFile
    from app.models.defense import ArchiveFile
    from app.models.system import TopicAttachment

    return (AIDocument, GraduationMaterialFile, ArchiveFile, TopicAttachment)


def referenced_exists():
    """被任一业务表引用: 一条 SQL 内的 NOT EXISTS 相关子查询, 不逐条查(N+1)."""
    from app.models.system import SysFile

    return or_(*[
        select(1).where(model.file_id == SysFile.id).exists()
        for model in referencing_models()
    ])


def _base_condition():
    """扫描域: 只处理无业务归属且未软删的记录."""
    from app.models.system import SysFile

    return and_(SysFile.business_type.is_(None), SysFile.deleted_at.is_(None))


def _remove_physical(record) -> None:
    """删物理文件; 文件已丢失/非法路径/OSError 只记日志, 绝不影响记录清理."""
    try:
        storage.abs_path(record.storage_path).unlink(missing_ok=True)
    except FileNotFoundError:
        logger.warning("物理文件不存在, 仅清理记录 file_id=%s storage_path=%s",
                       record.id, record.storage_path)
    except Exception:  # noqa: BLE001 — 任何物理删除故障都不得中断记录清理
        logger.warning("删除物理文件失败 file_id=%s storage_path=%s",
                       record.id, record.storage_path, exc_info=True)


def _append_item(items: list[dict], record, reason: str) -> None:
    if len(items) >= GC_MAX_ITEMS:
        return
    items.append({"file_id": record.id, "original_name": record.original_name,
                  "size": int(record.size or 0), "reason": reason})


def collect(db, retention_days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = True) -> dict:
    """孤儿文件清理唯一实现(Celery 任务与管理员端点共用).

    dry_run=True 只统计不删(不写库、不碰磁盘); dry_run=False 才删记录与物理文件。
    """
    from app.models.system import SysFile

    days = max(int(retention_days), 1)
    cutoff = datetime.now() - timedelta(days=days)
    base = _base_condition()

    scanned = db.execute(select(func.count()).select_from(SysFile).where(base)).scalar() or 0
    skipped_recent = db.execute(
        select(func.count()).select_from(SysFile).where(base, SysFile.created_at >= cutoff)
    ).scalar() or 0
    skipped_referenced = db.execute(
        select(func.count()).select_from(SysFile)
        .where(base, SysFile.created_at < cutoff, referenced_exists())
    ).scalar() or 0
    # 可删条数: 让管理员不拉明细也能判断规模(dry_run 与实删都返回)
    deletable = db.execute(
        select(func.count()).select_from(SysFile)
        .where(base, SysFile.created_at < cutoff, ~referenced_exists())
    ).scalar() or 0

    summary = {
        "dry_run": bool(dry_run), "retention_days": days, "scanned": int(scanned), "deleted": 0,
        "skipped_referenced": int(skipped_referenced), "skipped_recent": int(skipped_recent),
        "deletable": int(deletable), "freed_bytes": 0, "items": [],
    }

    # 明细: 一条 SELECT 内完成三段分类(未到期 / 被引用 / 可删), 引用判定走选出的 EXISTS 列, 不做 N+1
    rows = db.execute(
        select(SysFile, referenced_exists().label("is_referenced"))
        .where(base).order_by(SysFile.id).limit(GC_MAX_ITEMS)
    ).all()

    if dry_run:
        # 试运行必须把"将要删的"也列出来(reason="deletable"), 否则 dry_run 无法核对将删什么
        for record, is_referenced in rows:
            if record.created_at >= cutoff:
                reason = "too_recent"
            elif is_referenced:
                reason = "referenced"
            else:
                reason = "deletable"
            _append_item(summary["items"], record, reason)
        logger.info("孤儿文件清理试运行: %s", {k: summary[k] for k in
                                             ("retention_days", "scanned", "deletable", "skipped_recent",
                                              "skipped_referenced")})
        return summary

    # 明细展示: 先列"不删的原因"(未到期 / 被引用); 可删的留给下面的删除循环以 "deleted" 出现,
    # 保证同一条不会既标 deletable 又标 deleted
    for record, is_referenced in rows:
        if record.created_at >= cutoff:
            _append_item(summary["items"], record, "too_recent")
        elif is_referenced:
            _append_item(summary["items"], record, "referenced")

    # 实删: 按 id keyset 分页, 每批一个事务(同 risk_scan 的分批范式)
    last_id = 0
    while True:
        batch = db.execute(
            select(SysFile)
            .where(base, SysFile.created_at < cutoff, ~referenced_exists(), SysFile.id > last_id)
            .order_by(SysFile.id).limit(GC_BATCH_SIZE)
        ).scalars().all()
        if not batch:
            break
        for record in batch:
            # 先删物理文件: 失败只记日志, 记录清理照常进行
            _remove_physical(record)
            _append_item(summary["items"], record, "deleted")
            summary["freed_bytes"] += int(record.size or 0)
            summary["deleted"] += 1
            # 写操作留痕: 每条一条 logger.info, 不进 operation_log(避免刷表)
            logger.info("孤儿文件已清理 file_id=%s original_name=%s size=%s storage_path=%s",
                        record.id, record.original_name, record.size, record.storage_path)
            db.delete(record)
        last_id = batch[-1].id
        db.commit()  # 每批一个事务: 单批失败不影响已提交批次
        if len(batch) < GC_BATCH_SIZE:
            break

    logger.info("孤儿文件清理完成: %s", {k: summary[k] for k in
                                       ("retention_days", "scanned", "deleted", "freed_bytes")})
    return summary


def run_daily_gc(retention_days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False,
                 session_factory=None) -> dict:
    """每日任务入口: 自建 Session 调同一实现(自身无时钟口径, 全部落在 collect 里)."""
    factory = session_factory or default_session_factory
    db = factory()
    try:
        return collect(db, retention_days=retention_days, dry_run=dry_run)
    finally:
        db.close()


try:
    from app.tasks.celery_app import celery_app

    if celery_app is not None:

        @celery_app.task(name="app.tasks.file_gc.daily_gc")
        def daily_gc(retention_days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
            return run_daily_gc(retention_days=retention_days, dry_run=dry_run)

    else:  # CELERY_AVAILABLE=False: 保留同名符号, 同步调用 collect 仍然可用
        daily_gc = None

except Exception:  # noqa: BLE001 — Celery 不可用时仍可同步调用
    daily_gc = None
