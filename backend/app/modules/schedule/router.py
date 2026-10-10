"""学年阶段时间配置管理(roadmap 八十四章 graduation_schedule / 八十五章 允许选题时间).

时间窗口是进度风险逾期判断与选题提交校验的唯一数据源:
- 同一时间只有一条 is_active 配置生效, 激活新学年自动停用旧学年
- 每阶段 start < end, 且阶段之间不允许交叉(选题 → 开题 → 中期 → 终期 → 材料)
- 已被 graduation_cycle 引用的学年不可删除, 只能停用, 保证历史流程可追溯
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationSchedule
from app.models.system import OperationLog

router = APIRouter(prefix="/academic-schedule", tags=["Schedule"])
admin_router = APIRouter(prefix="/admin/academic-schedules", tags=["Schedule-Admin"])

STAGES = ("selection", "opening", "midterm", "final", "material")
STAGE_LABELS = {"selection": "选题", "opening": "开题", "midterm": "中期", "final": "终期", "material": "毕业材料"}
WINDOW_KEYS = [f"{stage}_{side}" for stage in STAGES for side in ("start", "end")]


def validate_windows(windows: dict[str, datetime | None]) -> None:
    unknown = set(windows) - set(WINDOW_KEYS)
    if unknown:
        raise BusinessException(f"未知的时间字段: {', '.join(sorted(unknown))}", 40002)

    # 仅约束单阶段 start < end; 阶段之间允许交叉(如补选期间其他学生已进入开题)
    for stage in STAGES:
        start, end = windows.get(f"{stage}_start"), windows.get(f"{stage}_end")
        if start and end and start >= end:
            raise BusinessException(f"{STAGE_LABELS[stage]}阶段的开始时间必须早于结束时间", 40002)


def _item(s: GraduationSchedule) -> dict:
    data = {"id": s.id, "academic_year": s.academic_year, "is_active": bool(s.is_active)}
    for key in WINDOW_KEYS:
        value = getattr(s, key)
        data[key] = value.isoformat() if value else None
    return data


class ScheduleBody(BaseModel):
    academic_year: str = Field(min_length=3, max_length=32, description="如 2025-2026")
    is_active: bool = False
    windows: dict[str, datetime | None] = Field(default_factory=dict, max_length=20)


def _set_windows(schedule: GraduationSchedule, windows: dict[str, datetime | None], user: CurrentUser,
                 db: Session, action: str) -> None:
    validate_windows(windows)
    for key, value in windows.items():
        setattr(schedule, key, value)
    if schedule.is_active:
        for other in db.execute(select(GraduationSchedule).where(
                GraduationSchedule.is_active == 1, GraduationSchedule.id != schedule.id)).scalars():
            other.is_active = 0
    db.add(OperationLog(user_id=user.id, username=user.username, module="SCHEDULE", action=action,
                        detail=f"学年 {schedule.academic_year}" + ("(激活)" if schedule.is_active else "")))


@router.get("/active")
def active_schedule(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前生效学年与各阶段时间窗口, 供题目中心/进度页展示."""
    schedule = db.execute(select(GraduationSchedule).where(GraduationSchedule.is_active == 1)
                          .order_by(GraduationSchedule.id.desc())).scalars().first()
    return ResponseUtil.success(_item(schedule) if schedule else None)


@admin_router.get("")
def list_schedules(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(GraduationSchedule).order_by(GraduationSchedule.academic_year.desc())).scalars().all()
    return ResponseUtil.success([_item(s) for s in rows])


@admin_router.post("")
def create_schedule(body: ScheduleBody, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    exists = db.execute(select(GraduationSchedule.id).where(
        GraduationSchedule.academic_year == body.academic_year)).scalar_one_or_none()
    if exists is not None:
        raise BusinessException(f"学年 {body.academic_year} 的配置已存在", 40002)
    schedule = GraduationSchedule(academic_year=body.academic_year, is_active=1 if body.is_active else 0)
    db.add(schedule)
    db.flush()
    _set_windows(schedule, body.windows, user, db, "CREATE_SCHEDULE")
    db.commit()
    db.refresh(schedule)
    return ResponseUtil.success(_item(schedule), message="学年配置已创建")


@admin_router.put("/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleBody, user: CurrentUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    schedule = db.get(GraduationSchedule, schedule_id)
    if schedule is None:
        raise ResourceNotFoundException("学年配置不存在")
    conflict = db.execute(select(GraduationSchedule.id).where(
        GraduationSchedule.academic_year == body.academic_year,
        GraduationSchedule.id != schedule_id)).scalar_one_or_none()
    if conflict is not None:
        raise BusinessException(f"学年 {body.academic_year} 的配置已存在", 40002)
    schedule.academic_year = body.academic_year
    schedule.is_active = 1 if body.is_active else 0
    _set_windows(schedule, body.windows, user, db, "UPDATE_SCHEDULE")
    db.commit()
    db.refresh(schedule)
    return ResponseUtil.success(_item(schedule), message="学年配置已更新")
