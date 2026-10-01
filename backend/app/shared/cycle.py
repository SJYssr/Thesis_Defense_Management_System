"""学年流程实例服务: 全系统流程归属与正式结论读取的唯一入口.

- 每个业务记录通过 cycle_id 归属到「学年+学生」流程实例(roadmap P0.2)
- 正式结论只能从 defense_result(经人工发布)读取, 与材料审核状态严格分离(docs §1.2)
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import GraduationCycle, GraduationSchedule
from app.models.defense import DefenseAssignment, DefenseResult


def academic_year_for(db: Session, at: datetime | None = None) -> str:
    """当前生效学年: 优先取学年配置, 否则按 9 月切年推算."""
    schedule = db.execute(
        select(GraduationSchedule.academic_year)
        .where(GraduationSchedule.is_active == 1)
        .order_by(GraduationSchedule.id.desc())
    ).scalars().first()
    if schedule:
        return schedule
    at = at or datetime.now()
    start = at.year if at.month >= 9 else at.year - 1
    return f"{start}-{start + 1}"


def ensure_cycle(db: Session, student_id: int, topic_id: int | None = None,
                 teacher_id: int | None = None, academic_year: str | None = None) -> GraduationCycle:
    """取/建学生当前学年流程实例; 已存在时回填题目与教师."""
    year = academic_year or academic_year_for(db)
    cycle = db.execute(
        select(GraduationCycle).where(
            GraduationCycle.academic_year == year,
            GraduationCycle.student_id == student_id,
        )
    ).scalar_one_or_none()
    if cycle is None:
        cycle = GraduationCycle(academic_year=year, student_id=student_id,
                                topic_id=topic_id, teacher_id=teacher_id)
        db.add(cycle)
        db.flush()
    else:
        if topic_id is not None:
            cycle.topic_id = topic_id
        if teacher_id is not None:
            cycle.teacher_id = teacher_id
    return cycle


def get_active_cycle(db: Session, student_id: int) -> GraduationCycle | None:
    """学生当前活动流程实例: 取最新一条 ACTIVE 记录."""
    return db.execute(
        select(GraduationCycle)
        .where(GraduationCycle.student_id == student_id, GraduationCycle.status == "ACTIVE")
        .order_by(GraduationCycle.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def formal_defense_snapshot(db: Session, student_id: int, academic_year: str | None = None) -> dict:
    """学生正式终期答辩结论快照(仅 PUBLISHED 的结论对外可见分数与决定)."""
    year = academic_year or academic_year_for(db)
    cycle = db.execute(
        select(GraduationCycle).where(
            GraduationCycle.academic_year == year,
            GraduationCycle.student_id == student_id,
        )
    ).scalar_one_or_none()
    result = None
    if cycle:
        result = db.execute(
            select(DefenseResult)
            .join(DefenseAssignment, DefenseAssignment.id == DefenseResult.assignment_id)
            .where(
                DefenseResult.cycle_id == cycle.id,
                DefenseResult.stage == "FINAL",
                DefenseResult.status == "PUBLISHED",
            )
            .order_by(DefenseResult.id.desc())
        ).scalars().first()
    return {
        "academic_year": year,
        "cycle_id": cycle.id if cycle else None,
        "status": result.status if result else "NOT_SCHEDULED",
        "decision": result.decision if result else None,
        "score": result.score if result else None,
        "published_at": result.published_at.isoformat() if result and result.published_at else None,
    }
