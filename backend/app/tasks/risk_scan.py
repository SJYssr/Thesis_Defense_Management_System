"""每日逾期风险扫描(roadmap 八十六章) —— 逾期扫描的唯一实现.

Celery Beat 每日 01:00 调度; 开发/演示环境可由管理员
POST /admin/progress/overdue-scan 触发(队列可用则入队, 否则同步兜底)。

逻辑: 重算毕业流程的进度与风险(规则引擎), 对本次新升级为 HIGH 的学生
发送站内通知; 幂等, 重复执行只补差量通知.

D67: 扫描按 id keyset 分页, 每批独立提交(OVERDUE_SCAN_BATCH_SIZE=100),
不在单个事务里遍历全表。Celery 任务与接口兜底都调 scan_overdue_risk_batched(),
两边只保留"入队 / 直接跑"的差异。
"""
import logging

from sqlalchemy import select

from app.shared.events import push_notification

logger = logging.getLogger("app.tasks.risk_scan")

OVERDUE_SCAN_BATCH_SIZE = 100


def default_session_factory():
    """扫描专用 Session 来源; 测试可注入替身(每批独立事务)."""
    from app.db.mysql import SessionLocal

    return SessionLocal()


def _scan_one(db, process) -> bool:
    """重算单条流程的风险; 本次新升级为 HIGH 时发通知, 返回是否发送."""
    from app.models.user import StudentProfile
    from app.shared.progress import ProgressService

    before_level = process.risk_level
    ProgressService.recompute(db, process.student_id, process)
    if process.risk_level != "HIGH" or before_level == "HIGH":
        return False
    user_id = db.execute(
        select(StudentProfile.user_id).where(StudentProfile.id == process.student_id)
    ).scalar()
    if not user_id:
        return False
    push_notification(
        db, user_id=user_id, type="SYSTEM", title="毕设进度高风险提醒",
        content=process.risk_reason or "你的毕业设计进度已逾期, 请尽快推进并联系指导教师。",
        business_type="PROGRESS", business_id=process.id,
    )
    return True


def scan_overdue_risk_batched(session_factory=None, batch_size: int | None = None) -> dict:
    """逾期风险扫描唯一实现: 按 id keyset 分页, 每批一个事务."""
    from app.models.business import GraduationProcess

    factory = session_factory or default_session_factory
    size = max(int(batch_size or OVERDUE_SCAN_BATCH_SIZE), 1)
    summary = {"mode": "sync", "queued": False, "batch_size": size, "scanned": 0,
               "normal": 0, "warning": 0, "high": 0, "notified": 0, "batches": 0}
    db = factory()
    try:
        last_id = 0
        while True:
            batch = db.execute(
                select(GraduationProcess).where(GraduationProcess.id > last_id)
                .order_by(GraduationProcess.id).limit(size)
            ).scalars().all()
            if not batch:
                break
            for process in batch:
                if _scan_one(db, process):
                    summary["notified"] += 1
                level_key = (process.risk_level or "NORMAL").lower()
                summary[level_key] = summary.get(level_key, 0) + 1
            last_id = batch[-1].id
            summary["scanned"] += len(batch)
            summary["batches"] += 1
            db.commit()  # 每批一个事务: 单批失败不影响已提交批次
            if len(batch) < size:
                break
    finally:
        db.close()
    logger.info("逾期风险扫描完成: %s", summary)
    return summary


def run_overdue_risk_scan(session_factory=None) -> dict:
    """兼容旧入口(每日 beat / 直调): 复用同一实现, 不再有第二套规则引擎."""
    return scan_overdue_risk_batched(session_factory=session_factory)


try:
    from app.tasks.celery_app import celery_app

    if celery_app is not None:

        @celery_app.task(name="app.tasks.risk_scan.daily_scan")
        def daily_scan_task() -> dict:
            return run_overdue_risk_scan()

except Exception:  # noqa: BLE001 — Celery 不可用时仍可同步调用
    daily_scan_task = None
