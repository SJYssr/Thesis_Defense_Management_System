"""每日逾期风险扫描(roadmap 八十六章).

Celery Beat 每日 01:00 调度; 开发/演示环境可由管理员
POST /admin/progress/overdue-scan 同步触发, 无需 Worker.

逻辑: 重算全部毕业流程的进度与风险(规则引擎), 对本次新升级为
HIGH 的学生发送站内通知; 幂等, 重复执行只补差量通知.
"""
import logging

from sqlalchemy import select

from app.shared.events import push_notification

logger = logging.getLogger("app.tasks.risk_scan")


def run_overdue_risk_scan() -> dict:
    from app.db.mysql import SessionLocal
    from app.models.business import GraduationProcess
    from app.models.user import StudentProfile
    from app.shared.progress import ProgressService

    db = SessionLocal()
    summary = {"scanned": 0, "normal": 0, "warning": 0, "high": 0, "notified": 0}
    try:
        processes = db.execute(select(GraduationProcess)).scalars().all()
        summary["scanned"] = len(processes)
        for process in processes:
            before_level = process.risk_level
            ProgressService.recompute(db, process.student_id, process)
            level_key = (process.risk_level or "NORMAL").lower()
            summary[level_key] = summary.get(level_key, 0) + 1
            if process.risk_level == "HIGH" and before_level != "HIGH":
                user_id = db.execute(
                    select(StudentProfile.user_id).where(StudentProfile.id == process.student_id)
                ).scalar()
                if user_id:
                    push_notification(
                        db, user_id=user_id, type="SYSTEM", title="毕设进度高风险提醒",
                        content=process.risk_reason or "你的毕业设计进度已逾期, 请尽快推进并联系指导教师。",
                        business_type="PROGRESS", business_id=process.id,
                    )
                    summary["notified"] += 1
        db.commit()
        logger.info("逾期风险扫描完成: %s", summary)
        return summary
    finally:
        db.close()


try:
    from app.tasks.celery_app import celery_app

    if celery_app is not None:

        @celery_app.task(name="app.tasks.risk_scan.daily_scan")
        def daily_scan_task() -> dict:
            return run_overdue_risk_scan()

except Exception:  # noqa: BLE001 — Celery 不可用时仍可同步调用
    daily_scan_task = None
