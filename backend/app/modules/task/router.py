"""阶段任务模块(roadmap P1.1): 按学年发布任务, 学生首页展示待办/逾期/提交状态.

任务范围(学年/学院/专业/班级)与服务端材料状态匹配; 修改版本化留痕。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, require_student, require_teacher_or_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import FinalDefense, GraduationCycle, GraduationMaterial, MidtermReport, OpeningReport
from app.models.defense import StageTask, StageTaskVersion
from app.models.enums import ProcessStage
from app.models.user import StudentProfile
from app.shared import state_machine as sm
from app.shared.events import now
from app.shared.query import paginate
from app.shared.workflow import GraduationWorkflowService

router = APIRouter(tags=["StageTask"])

STAGE_CN = {s.value: v for s, v in {
    ProcessStage.SELECTION: "选题",
    ProcessStage.OPENING: "开题报告",
    ProcessStage.MIDTERM: "中期材料",
    ProcessStage.FINAL: "终期材料",
    ProcessStage.MATERIAL: "毕业材料",
}.items()}

# 阶段 → 该阶段流程材料模型(用于服务端匹配提交/审核状态)
_STAGE_MODEL = {
    "OPENING": OpeningReport,
    "MIDTERM": MidtermReport,
    "FINAL": FinalDefense,
    "MATERIAL": GraduationMaterial,
}
_TASK_FIELDS = ["academic_year", "stage", "title", "description", "requirements_json",
                "opens_at", "due_at", "late_policy", "college_id", "major_id", "class_id", "status"]


class TaskCreate(BaseModel):
    academic_year: str = Field(min_length=4, max_length=32)
    stage: str = Field(pattern="^(SELECTION|OPENING|MIDTERM|FINAL|MATERIAL)$")
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None
    requirements: list[str] = Field(default_factory=list, description="所需材料清单")
    opens_at: str | None = None
    due_at: str | None = None
    late_policy: str = Field(default="ALLOW", pattern="^(ALLOW|DENY)$")
    college_id: int | None = None
    major_id: int | None = None
    class_id: int | None = None
    # D59: status 不再是"请求体想赋什么就赋什么"的目标状态, 而是创建动作语义:
    # 缺省/DRAFT = 建为草稿; PUBLISHED = 建为草稿后立刻经 state_machine.transition 发布。
    # 之后的发布/关闭只能走 POST /stage-tasks/{id}/publish|close。
    status: str | None = Field(default=None, pattern="^(DRAFT|PUBLISHED)$",
                               description="兼容字段: PUBLISHED=创建后立即发布(内部仍走状态机)")


class TaskPatch(TaskCreate):
    pass


def _parse_dt(v: str | None):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        raise BusinessException("时间格式错误, 应为 ISO 格式(2026-09-30T18:00:00)")


def _task_item(t: StageTask) -> dict:
    return {
        "id": t.id, "academic_year": t.academic_year, "stage": t.stage, "stage_cn": STAGE_CN.get(t.stage, t.stage),
        "title": t.title, "description": t.description,
        "requirements": json.loads(t.requirements_json) if t.requirements_json else [],
        "opens_at": t.opens_at.isoformat() if t.opens_at else None,
        "due_at": t.due_at.isoformat() if t.due_at else None,
        "late_policy": t.late_policy,
        "college_id": t.college_id, "major_id": t.major_id, "class_id": t.class_id,
        "status": t.status, "current_version": t.current_version,
        "allowed_actions": sm.allowed_actions(sm.STAGE_TASK, t.status),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/stage-tasks")
def list_tasks(academic_year: str | None = None, stage: str | None = None, status: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               user: CurrentUser = Depends(require_teacher_or_admin), db: Session = Depends(get_db)):
    """教师与管理员查看任务配置; require_teacher 放行 TEACHER/ADMIN."""
    conds = []
    if academic_year:
        conds.append(StageTask.academic_year == academic_year)
    if stage:
        conds.append(StageTask.stage == stage)
    if status:
        conds.append(StageTask.status == status)
    total = db.execute(select(func.count(StageTask.id)).where(*conds)).scalar()
    rows = db.execute(
        select(StageTask).where(*conds).order_by(StageTask.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return ResponseUtil.page([_task_item(t) for t in rows], total, page, page_size)


def _ensure_task_writable(t: StageTask, user: CurrentUser) -> None:
    """D58: 阶段任务的对象级权限——教师只能改自己创建的任务, 管理员不受限."""
    if user.role == "ADMIN":
        return
    if t.created_by is None or t.created_by != user.id:
        raise PermissionDeniedException("无权修改该任务(仅创建人与管理员可操作)")


def _load_task(db: Session, task_id: int, user: CurrentUser) -> StageTask:
    t = db.get(StageTask, task_id)
    if t is None:
        raise ResourceNotFoundException("任务不存在")
    _ensure_task_writable(t, user)
    return t


@router.post("/stage-tasks")
def create_task(body: TaskCreate, user: CurrentUser = Depends(require_teacher_or_admin), db: Session = Depends(get_db)):
    t = StageTask(
        academic_year=body.academic_year, stage=body.stage, title=body.title,
        description=body.description,
        requirements_json=json.dumps(body.requirements, ensure_ascii=False) if body.requirements else None,
        opens_at=_parse_dt(body.opens_at), due_at=_parse_dt(body.due_at),
        late_policy=body.late_policy, college_id=body.college_id, major_id=body.major_id,
        class_id=body.class_id, status="DRAFT", created_by=user.id, current_version=1,
    )
    db.add(t)
    db.commit()
    if body.status == "PUBLISHED":
        # 兼容旧请求体: 发布只能经状态机, 不接受直接赋值 status
        sm.transition(db, sm.STAGE_TASK, t, "publish", user)
        db.commit()
    return ResponseUtil.success(_task_item(t),
                                message="任务已发布" if t.status == "PUBLISHED" else "任务已创建")


@router.put("/stage-tasks/{task_id}")
def update_task(task_id: int, body: TaskPatch, user: CurrentUser = Depends(require_teacher_or_admin),
                db: Session = Depends(get_db)):
    """修改任务: 版本化保存修改前快照(docs §4 stage_task 版本化修改).

    D58: 只有创建人与管理员可改(教师互相改写是越权);
    D59: 本接口不动 status, 发布/关闭走 /publish、/close 两个动作端点。
    """
    t = _load_task(db, task_id, user)
    snapshot = {f: getattr(t, f).isoformat() if isinstance(getattr(t, f), datetime) else getattr(t, f)
                for f in _TASK_FIELDS}
    db.add(StageTaskVersion(task_id=t.id, version_no=t.current_version,
                            snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
                            changed_by=user.id))
    t.title = body.title
    t.description = body.description
    t.requirements_json = json.dumps(body.requirements, ensure_ascii=False) if body.requirements else None
    t.opens_at = _parse_dt(body.opens_at)
    t.due_at = _parse_dt(body.due_at)
    t.late_policy = body.late_policy
    t.college_id = body.college_id
    t.major_id = body.major_id
    t.class_id = body.class_id
    t.current_version += 1
    db.commit()
    return ResponseUtil.success(_task_item(t), message="任务已更新并留痕")


@router.get("/stage-tasks/{task_id}/versions")
def task_versions(task_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  user: CurrentUser = Depends(require_teacher_or_admin), db: Session = Depends(get_db)):
    """D60: 版本历史分页(先 SQL 层 count 再取页, 口径与 items 一致)."""
    rows, total = paginate(
        db,
        select(StageTaskVersion).where(StageTaskVersion.task_id == task_id)
        .order_by(StageTaskVersion.id.desc()),
        page=page, size=page_size,
    )
    return ResponseUtil.page([{
        "version_no": v.version_no, "snapshot": json.loads(v.snapshot_json),
        "created_at": v.created_at.isoformat() if v.created_at else None,
    } for v in rows], total, page, page_size)


@router.post("/stage-tasks/{task_id}/publish")
def publish_task(task_id: int, user: CurrentUser = Depends(require_teacher_or_admin),
                 db: Session = Depends(get_db)):
    """D59: DRAFT → PUBLISHED(发布只经 state_machine.transition)."""
    t = _load_task(db, task_id, user)
    sm.transition(db, sm.STAGE_TASK, t, "publish", user)
    db.commit()
    return ResponseUtil.success(_task_item(t), message="任务已发布")


@router.post("/stage-tasks/{task_id}/close")
def close_task(task_id: int, user: CurrentUser = Depends(require_teacher_or_admin),
               db: Session = Depends(get_db)):
    """D59: PUBLISHED → CLOSED(截止/撤回入口; 关闭后学生待办不再展示, 历史留痕保留)."""
    t = _load_task(db, task_id, user)
    sm.transition(db, sm.STAGE_TASK, t, "close", user)
    db.commit()
    return ResponseUtil.success(_task_item(t), message="任务已关闭")


@router.get("/stage-tasks/mine")
def my_tasks(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """学生待办: 匹配学年与适用范围, 携带该阶段材料的服务端提交/审核状态与逾期标记."""
    cycle = db.execute(
        select(GraduationCycle).where(GraduationCycle.student_id == user.profile_id,
                                      GraduationCycle.status == "ACTIVE")
        .order_by(GraduationCycle.id.desc()).limit(1)
    ).scalar_one_or_none()
    if cycle is None:
        return ResponseUtil.success({"academic_year": None, "tasks": []})

    sp = db.get(StudentProfile, user.profile_id)
    rows = db.execute(
        select(StageTask).where(
            StageTask.academic_year == cycle.academic_year,
            StageTask.status == "PUBLISHED",
        ).order_by(StageTask.due_at)
    ).scalars().all()

    # 服务端一次取全阶段材料状态
    snap = GraduationWorkflowService.get_stage_snapshot(db, user.profile_id)
    material_of_stage = {"OPENING": snap["opening"], "MIDTERM": snap["midterm"],
                         "FINAL": snap["final"], "MATERIAL": snap["material"]}
    sel = snap["selection"]

    current = now()
    tasks = []
    for t in rows:
        if t.college_id is not None and t.college_id != sp.college_id:
            continue
        if t.major_id is not None and t.major_id != sp.major_id:
            continue
        if t.class_id is not None and t.class_id != sp.class_id:
            continue

        if t.stage == "SELECTION":
            m_status = sel.status if sel else "NOT_SELECTED"
            m_submitted = sel.created_at if sel else None
            m_reviewed = sel.review_time if sel else None
            m_comment = sel.review_comment if sel else None
            done = bool(sel and sel.status == "APPROVED")
        else:
            m = material_of_stage[t.stage]
            m_status = m.status if m else "NOT_SUBMITTED"
            m_submitted = m.submitted_at if m else None
            m_reviewed = m.reviewed_at if m else None
            m_comment = m.review_comment if m else None
            done = bool(m and m.status == "APPROVED")

        overdue = bool(t.due_at and current > t.due_at and not done)
        not_opened = bool(t.opens_at and current < t.opens_at)
        tasks.append({
            "id": t.id, "stage": t.stage, "stage_cn": STAGE_CN.get(t.stage, t.stage),
            "title": t.title, "description": t.description,
            "requirements": json.loads(t.requirements_json) if t.requirements_json else [],
            "opens_at": t.opens_at.isoformat() if t.opens_at else None,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "late_policy": t.late_policy,
            "material_status": m_status,
            "material_status_cn": _status_cn(m_status),
            "submitted_at": m_submitted.isoformat() if m_submitted else None,
            "reviewed_at": m_reviewed.isoformat() if m_reviewed else None,
            "review_comment": m_comment,
            "done": done, "overdue": overdue, "not_opened": not_opened,
        })
    return ResponseUtil.success({"academic_year": cycle.academic_year, "tasks": tasks})


def _status_cn(s: str) -> str:
    return {
        "NOT_SUBMITTED": "未提交", "NOT_SELECTED": "未选题", "DRAFT": "草稿", "PENDING": "待审核",
        "SUBMITTED": "已提交待审核", "APPROVED": "已通过", "REJECTED": "已驳回", "RESUBMIT_REQUIRED": "需重新提交",
    }.get(s, s)
