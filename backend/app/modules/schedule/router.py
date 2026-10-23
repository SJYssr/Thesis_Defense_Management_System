"""学年阶段时间配置管理(roadmap 八十四章 graduation_schedule / 八十五章 允许选题时间).

时间窗口是进度风险逾期判断与选题提交校验的唯一数据源:
- 同一时间只有一条 is_active 配置生效, 激活新学年自动停用旧学年
- 每阶段 start < end, 且阶段之间不允许交叉(选题 → 开题 → 中期 → 终期 → 材料)
- 已被 graduation_cycle 引用的学年不可删除, 只能停用, 保证历史流程可追溯
"""
from datetime import datetime

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationSchedule
from app.models.system import OperationLog
from app.shared.query import paginate

router = APIRouter(prefix="/academic-schedule", tags=["Schedule"])
admin_router = APIRouter(prefix="/admin/academic-schedules", tags=["Schedule-Admin"])

STAGES = ("selection", "opening", "midterm", "final", "material")
STAGE_LABELS = {"selection": "选题", "opening": "开题", "midterm": "中期", "final": "终期", "material": "毕业材料"}
WINDOW_KEYS = [f"{stage}_{side}" for stage in STAGES for side in ("start", "end")]


def to_naive(value: Any, field: str = "") -> datetime | None:
    """D62: 时间窗口一律转成 naive datetime 再入库。

    前端可能传 "2026-03-01T00:00:00+08:00" 或 "2026-03-01T00:00:00"; 若不剥离时区,
    带 tzinfo 的值会落进 DateTime(无时区) 列并把偏移吞掉, 消费方(shared/events.naive now())
    做差时 days_left 整体偏若干小时。这里统一"保留墙上时间, 丢弃偏移", 两种写法入库等价。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            raise BusinessException(
                f"时间字段 {field or 'window'} 格式错误, 应为 ISO 8601(如 2026-03-01T00:00:00)", 40002) from None
    if not isinstance(value, datetime):
        raise BusinessException(f"时间字段 {field or 'window'} 类型错误", 40002)
    return value.replace(tzinfo=None)


def normalize_windows(windows: dict[str, Any]) -> dict[str, datetime | None]:
    """把请求体里的 str/naive/aware datetime 统一规范化(键名不改)."""
    return {key: to_naive(value, key) for key, value in windows.items()}


def validate_windows(windows: dict[str, Any]) -> None:
    normalized = normalize_windows(windows)
    unknown = set(normalized) - set(WINDOW_KEYS)
    if unknown:
        raise BusinessException(f"未知的时间字段: {', '.join(sorted(unknown))}", 40002)

    # 仅约束单阶段 start < end; 阶段之间允许交叉(如补选期间其他学生已进入开题)
    for stage in STAGES:
        start, end = normalized.get(f"{stage}_start"), normalized.get(f"{stage}_end")
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
    # D62: 收 str 而非 datetime, 由 to_naive 统一剥离时区(带 +08:00 也不会被静默吞掉)
    windows: dict[str, str | None] = Field(default_factory=dict, max_length=20,
                                           description="键如 selection_start, 值 ISO 8601 字符串或 null")


def _set_windows(schedule: GraduationSchedule, windows: dict[str, Any], user: CurrentUser,
                 db: Session, action: str) -> None:
    validate_windows(windows)
    for key, value in normalize_windows(windows).items():
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
def list_schedules(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """D61: 学年配置列表分页(先 count 后取页, 不再一次性全量返回)."""
    rows, total = paginate(
        db, select(GraduationSchedule).order_by(GraduationSchedule.academic_year.desc()),
        page=page, size=page_size,
    )
    return ResponseUtil.page([_item(s) for s in rows], total, page, page_size)


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
