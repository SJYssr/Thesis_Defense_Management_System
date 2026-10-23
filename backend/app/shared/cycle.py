"""学年流程实例服务: 全系统流程归属与正式结论读取的唯一入口.

- 每个业务记录通过 cycle_id 归属到「学年+学生」流程实例(roadmap P0.2)
- 正式结论只能从 defense_result(经人工发布)读取, 与材料审核状态严格分离(docs §1.2)
- 同一学生同时只能有一条 ACTIVE cycle(docs/refactor-plan.md §5, 缺陷 D1/D2)
"""
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.business import GraduationCycle, GraduationSchedule
from app.models.defense import DefenseResult

log = logging.getLogger("app.cycle")


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


def _demote_other_active(db: Session, student_id: int, keep_id: int | None = None) -> int:
    """把该学生其它 ACTIVE 流程实例置为 COMPLETED(仅状态, GraduationCycle 无 completed_at 列)."""
    conditions = [
        GraduationCycle.student_id == student_id,
        GraduationCycle.status == "ACTIVE",
    ]
    if keep_id is not None:
        conditions.append(GraduationCycle.id != keep_id)
    result = db.execute(
        update(GraduationCycle)
        .where(*conditions)
        .values(status="COMPLETED")
        # evaluate: 同步 session 内已加载实例, 避免调用方拿到 identity map 里的旧状态
        .execution_options(synchronize_session="evaluate")
    )
    return int(result.rowcount or 0)


def ensure_cycle(db: Session, student_id: int, topic_id: int | None = None,
                 teacher_id: int | None = None, academic_year: str | None = None) -> GraduationCycle:
    """取/建学生当前学年流程实例; 建新实例前先归档该学生其它 ACTIVE 实例."""
    year = academic_year or academic_year_for(db)
    cycle = db.execute(
        select(GraduationCycle).where(
            GraduationCycle.academic_year == year,
            GraduationCycle.student_id == student_id,
        )
    ).scalar_one_or_none()
    if cycle is None:
        # 新建前归档旧 ACTIVE, 保证"同一学生唯一 ACTIVE"(系统维护动作, 不走 CYCLE.complete:
        # 后者带"归档已批准 + 结论 PASS"的毕业判定守卫, 不适用于跨学年换届)
        archived = _demote_other_active(db, student_id)
        if archived:
            log.info("学生 %s 换届: %s 条历史流程实例置为 COMPLETED", student_id, archived)
        cycle = GraduationCycle(academic_year=year, student_id=student_id,
                                topic_id=topic_id, teacher_id=teacher_id, status="ACTIVE")
        db.add(cycle)
        db.flush()
    else:
        if topic_id is not None:
            cycle.topic_id = topic_id
        if teacher_id is not None:
            cycle.teacher_id = teacher_id
        if cycle.status != "ACTIVE":
            # 复用一个已归档学年实例时恢复为活动实例, 并让其它实例让位
            _demote_other_active(db, student_id, keep_id=cycle.id)
            cycle.status = "ACTIVE"
    return cycle


def get_active_cycle(db: Session, student_id: int) -> GraduationCycle | None:
    """学生当前活动流程实例: 取最新一条 ACTIVE; 出现多条时告警(不静默)."""
    rows = db.execute(
        select(GraduationCycle)
        .where(GraduationCycle.student_id == student_id, GraduationCycle.status == "ACTIVE")
        .order_by(GraduationCycle.id.desc())
    ).scalars().all()
    if not rows:
        return None
    if len(rows) > 1:
        log.error(
            "数据异常: 学生 %s 存在 %s 条 ACTIVE 流程实例(id=%s), 取最新一条 id=%s",
            student_id, len(rows), [c.id for c in rows], rows[0].id,
        )
    return rows[0]


def list_cycles(db: Session, student_id: int) -> list[GraduationCycle]:
    """学生全部学年流程实例(新→旧), 供学生端展示历史学年."""
    return list(db.execute(
        select(GraduationCycle)
        .where(GraduationCycle.student_id == student_id)
        .order_by(GraduationCycle.academic_year.desc(), GraduationCycle.id.desc())
    ).scalars().all())


def formal_defense_snapshot(db: Session, student_id: int, academic_year: str | None = None) -> dict:
    """学生正式终期答辩结论快照(仅 PUBLISHED 的结论对外可见分数与决定).

    academic_year 参数仅为兼容旧调用保留, 已不再参与查询: 结论归属完全由 get_active_cycle 决定
    (缺陷 D2: 学年配置切换后按学年二次查会查不到学生自己的结论)。
    """
    cycle = get_active_cycle(db, student_id)
    result = None
    if cycle is not None:
        result = db.execute(
            select(DefenseResult)
            .where(
                DefenseResult.cycle_id == cycle.id,
                DefenseResult.stage == "FINAL",
                DefenseResult.status == "PUBLISHED",
            )
            .order_by(DefenseResult.id.desc())
        ).scalars().first()
    return {
        "academic_year": cycle.academic_year if cycle is not None else (academic_year or academic_year_for(db)),
        "cycle_id": cycle.id if cycle is not None else None,
        "status": result.status if result else "NOT_SCHEDULED",
        "decision": result.decision if result else None,
        "score": result.score if result else None,
        "published_at": result.published_at.isoformat() if result and result.published_at else None,
    }
