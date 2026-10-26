"""阶段任务的学年、适用范围与提交时间限制，列表和写入口共用。"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.models.defense import StageTask
from app.models.user import StudentProfile
from app.shared.cycle import academic_year_for, get_active_cycle
from app.shared.events import now


def tasks_for_student(db: Session, student_id: int, stage: str | None = None,
                      academic_year: str | None = None) -> tuple[str, list[StageTask]]:
    cycle = get_active_cycle(db, student_id)
    year = academic_year or (cycle.academic_year if cycle else academic_year_for(db))
    student = db.get(StudentProfile, student_id)
    if student is None:
        return year, []
    filters = [StageTask.academic_year == year, StageTask.status == "PUBLISHED"]
    if stage:
        filters.append(StageTask.stage == stage)
    for column in ("college_id", "major_id", "class_id"):
        field = getattr(StageTask, column)
        filters.append(or_(field.is_(None), field == getattr(student, column)))
    rows = db.execute(select(StageTask).where(*filters).order_by(StageTask.due_at, StageTask.id)).scalars().all()
    return year, list(rows)


def ensure_task_submission_open(db: Session, student_id: int, stage: str,
                                academic_year: str | None = None) -> None:
    _, tasks = tasks_for_student(db, student_id, stage, academic_year)
    current = now()
    for task in tasks:
        if task.opens_at and current < task.opens_at:
            raise BusinessException(f"任务「{task.title}」尚未开始，开放时间 {task.opens_at:%Y-%m-%d %H:%M}", 40003)
        if task.due_at and current > task.due_at and task.late_policy == "DENY":
            raise BusinessException(f"任务「{task.title}」已截止且不允许补交，请联系教师调整截止时间", 40003)
