"""正式答辩 API: 排期管理(管理员) / 评审工作台(被指派教师) / 我的答辩(学生).

路径遵循 docs/project-design.md 第5节; 提交类接口在后端强校验状态与权限。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ConflictException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationCycle, GraduationTopic
from app.models.defense import (
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseScheduleChange,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
)
from app.models.enums import ResultStatus, ScoreStatus, SeatStatus, SessionStatus
from app.models.system import OperationLog
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.shared.events import push_notification
from app.modules.defense.seat_state import mark_seat
from app.modules.defense.service import (
    STAGE_CN,
    SCORE_STATUS_CN,
    SEAT_STATUS_CN,
    SESSION_STATUS_CN,
    _notify_session_members,
    assignment_item,
    calculate_result as calculate_result_flow,
    cancel_session as cancel_session_flow,
    compute_result,
    finish_session as finish_session_flow,
    get_effective_score,
    get_rubric_for,
    mark_result_under_review,
    publish_result as publish_result_flow,
    publish_session as publish_session_flow,
    start_session as start_session_flow,
    session_item,
    validate_session,
    withdraw_result as withdraw_result_flow,
)

router = APIRouter(tags=["Defense"])
rubric_router = APIRouter(prefix="/review-rubrics", tags=["Defense-Rubric"])


# ---------- 权限辅助 ----------

def _ensure_admin(user: CurrentUser) -> None:
    if user.role != "ADMIN":
        raise PermissionDeniedException("仅管理员可执行排期管理操作")


def _ensure_examiner_of(db: Session, session_id: int, user: CurrentUser) -> DefenseExaminer:
    if user.role != "TEACHER":
        raise PermissionDeniedException("仅被指派评委可执行此操作")
    ex = db.execute(
        select(DefenseExaminer).where(
            DefenseExaminer.session_id == session_id, DefenseExaminer.teacher_id == user.profile_id)
    ).scalar_one_or_none()
    if ex is None:
        raise PermissionDeniedException("您未被指派为该场次评委")
    return ex


def _get_assignment_or_404(db: Session, assignment_id: int) -> DefenseAssignment:
    a = db.get(DefenseAssignment, assignment_id)
    if a is None:
        raise ResourceNotFoundException("答辩席位不存在")
    return a


def _ensure_student_own_assignment(db: Session, a: DefenseAssignment, user: CurrentUser) -> GraduationCycle:
    if user.role != "STUDENT":
        raise PermissionDeniedException("需要学生身份")
    cycle = db.get(GraduationCycle, a.cycle_id)
    if cycle is None or cycle.student_id != user.profile_id:
        raise PermissionDeniedException("只能操作自己的答辩席位")
    return cycle


def _load_body_scores(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise BusinessException("评分数据格式错误")


# ---------- 场次: 管理员 ----------

class SessionCreate(BaseModel):
    academic_year: str = Field(min_length=4, max_length=32)
    stage: str = Field(pattern="^(OPENING|MIDTERM|FINAL)$")
    title: str | None = Field(default=None, max_length=255)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=255)
    online_link: str | None = Field(default=None, max_length=512)
    capacity: int = Field(default=10, ge=1, le=200)


class SessionPatch(BaseModel):
    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = None
    online_link: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=200)
    revision: int = Field(description="乐观锁: 客户端持有的版本号")
    change_reason: str | None = Field(default=None, max_length=500, description="已发布场次改期必填")


@router.get("/defense-sessions")
def list_sessions(academic_year: str | None = None, stage: str | None = None, status: str | None = None,
                  keyword: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if academic_year:
        conds.append(DefenseSession.academic_year == academic_year)
    if stage:
        conds.append(DefenseSession.stage == stage)
    if status:
        conds.append(DefenseSession.status == status)
    total = db.execute(select(func.count(DefenseSession.id)).where(*conds)).scalar()
    rows = db.execute(
        select(DefenseSession).where(*conds).order_by(DefenseSession.starts_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = [session_item(db, s) for s in rows]
    if keyword:
        items = [i for i in items if keyword in (i.get("title") or "") or keyword in (i.get("location") or "")]
    return ResponseUtil.page(items, total, page, page_size)


@router.post("/defense-sessions")
def create_session(body: SessionCreate, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if body.starts_at >= body.ends_at:
        raise BusinessException("开始时间必须早于结束时间")
    s = DefenseSession(academic_year=body.academic_year, stage=body.stage, title=body.title,
                       starts_at=body.starts_at, ends_at=body.ends_at, location=body.location,
                       online_link=body.online_link, capacity=body.capacity,
                       status=SessionStatus.DRAFT.value, created_by=user.id)
    db.add(s)
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次草稿已创建")


@router.get("/defense-sessions/mine")
def my_sessions(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """学生: 我的答辩席位与场次; 教师: 被指派为评委的场次."""
    if user.role == "STUDENT":
        rows = db.execute(
            select(DefenseAssignment, DefenseSession, GraduationCycle)
            .join(DefenseSession, DefenseSession.id == DefenseAssignment.session_id)
            .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
            .where(GraduationCycle.student_id == user.profile_id)
            .order_by(DefenseSession.starts_at.desc())
        ).all()
        items = []
        for a, s, cycle in rows:
            item = session_item(db, s, with_members=True)
            item["assignment"] = assignment_item(db, a, cycle)
            result = db.execute(select(DefenseResult).where(DefenseResult.assignment_id == a.id)).scalar_one_or_none()
            if result and result.status == ResultStatus.PUBLISHED.value:
                item["result"] = {
                    "status": result.status,
                    "score": result.score, "decision": result.decision,
                    "published_at": result.published_at.isoformat() if result.published_at else None,
                }
            elif result:
                item["result"] = {"status": result.status,
                                  "status_cn": "复核中" if result.status == ResultStatus.WITHDRAWN.value else None}
            else:
                item["result"] = None
            items.append(item)
        return ResponseUtil.success(items)

    if user.role == "TEACHER":
        rows = db.execute(
            select(DefenseSession).join(DefenseExaminer, DefenseExaminer.session_id == DefenseSession.id)
            .where(DefenseExaminer.teacher_id == user.profile_id)
            .order_by(DefenseSession.starts_at.desc())
        ).scalars().all()
        return ResponseUtil.success([session_item(db, s) for s in rows])

    raise PermissionDeniedException("该接口面向学生与教师")


@router.get("/defense-sessions/{session_id}")
def get_session(session_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(DefenseSession, session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    if user.role == "ADMIN":
        return ResponseUtil.success(session_item(db, s))
    # 学生/教师仅可见与自己相关的场次
    if user.role == "TEACHER":
        linked = db.execute(select(DefenseExaminer.id).where(
            DefenseExaminer.session_id == session_id, DefenseExaminer.teacher_id == user.profile_id)).scalar_one_or_none()
    else:
        linked = db.execute(
            select(DefenseAssignment.id)
            .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
            .where(DefenseAssignment.session_id == session_id, GraduationCycle.student_id == user.profile_id)
        ).scalar_one_or_none()
    if linked is None:
        raise PermissionDeniedException("无权查看该场次")
    return ResponseUtil.success(session_item(db, s))


@router.post("/defense-sessions/{session_id}/validate")
def validate(session_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    s = db.get(DefenseSession, session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    conflicts = validate_session(db, s)
    return ResponseUtil.success({"session_id": s.id, "conflicts": conflicts, "pass": not conflicts})


@router.post("/defense-sessions/{session_id}/publish")
def publish_session(session_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    s = publish_session_flow(db, session_id, user)  # DRAFT -> PUBLISHED, 非法状态由状态机抛 40003
    db.add(OperationLog(user_id=user.id, module="DEFENSE", action="PUBLISH_SESSION",
                        detail=f"发布场次#{s.id} {STAGE_CN.get(s.stage, s.stage)}"))
    _notify_session_members(db, s,
                            title=f"答辩安排发布: {STAGE_CN.get(s.stage, s.stage)}",
                            content=f"场次「{s.title or s.id}」已发布, 时间 {s.starts_at:%Y-%m-%d %H:%M}, 地点: {s.location or '线上'}")
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次已发布并通知相关师生")


@router.post("/defense-sessions/{session_id}/cancel")
def cancel_session(session_id: int, body: SessionPatch | None = None, user: CurrentUser = Depends(require_admin),
                   db: Session = Depends(get_db)):
    reason = body.change_reason if body else None
    s = cancel_session_flow(db, session_id, user, reason)  # 任意活态 -> CANCELLED, 原因必填
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="CANCEL_SESSION",
                        detail=f"取消场次#{s.id}: {reason or '未说明'}"))
    _notify_session_members(db, s, title=f"答辩场次取消: {STAGE_CN.get(s.stage, s.stage)}",
                            content=f"场次「{s.title or s.id}」已取消。原因: {reason or '未说明'}")
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次已取消")


@router.post("/defense-sessions/{session_id}/start")
def start_session(session_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """开始答辩: PUBLISHED -> IN_PROGRESS; 未发布就 start 由状态机抛 40003."""
    s = start_session_flow(db, session_id, user)
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="START_SESSION",
                        detail=f"开始场次#{s.id} {STAGE_CN.get(s.stage, s.stage)}"))
    _notify_session_members(db, s, title=f"答辩已开始: {STAGE_CN.get(s.stage, s.stage)}",
                            content=f"场次「{s.title or s.id}」已开始, 请准时参加。")
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次已开始")


@router.post("/defense-sessions/{session_id}/finish")
def finish_session(session_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """结束答辩: IN_PROGRESS -> FINISHED."""
    s = finish_session_flow(db, session_id, user)
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="FINISH_SESSION",
                        detail=f"结束场次#{s.id} {STAGE_CN.get(s.stage, s.stage)}"))
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次已结束")


@router.patch("/defense-sessions/{session_id}")
def patch_session(session_id: int, body: SessionPatch, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """修改场次; 已发布场次的时间/地点变更必须留痕并通知(乐观锁防并发)."""
    s = db.get(DefenseSession, session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    if s.revision != body.revision:
        raise ConflictException("场次已被其他人修改, 请刷新后重试")
    if s.status in (SessionStatus.CANCELLED.value, SessionStatus.FINISHED.value):
        raise BusinessException("该场次已结束或取消, 不能修改")

    before = (s.starts_at, s.ends_at, s.location)
    if body.starts_at is not None:
        s.starts_at = body.starts_at
    if body.ends_at is not None:
        s.ends_at = body.ends_at
    if s.starts_at >= s.ends_at:
        raise BusinessException("开始时间必须早于结束时间")
    if body.location is not None:
        s.location = body.location
    if body.online_link is not None:
        s.online_link = body.online_link
    if body.capacity is not None:
        assigned = db.execute(select(func.count(DefenseAssignment.id)).where(
            DefenseAssignment.session_id == s.id)).scalar()
        if body.capacity < assigned:
            raise BusinessException(f"容量不能小于已排学生数({assigned})")
        s.capacity = body.capacity
    if body.title is not None:
        s.title = body.title

    published = s.status == SessionStatus.PUBLISHED.value
    if published and (before[0] != s.starts_at or before[1] != s.ends_at or before[2] != s.location):
        if not body.change_reason:
            raise BusinessException("已发布场次改期必须填写变更原因")
        db.add(DefenseScheduleChange(
            session_id=s.id, revision_before=body.revision,
            before_starts_at=before[0], before_ends_at=before[1], before_location=before[2],
            after_starts_at=s.starts_at, after_ends_at=s.ends_at, after_location=s.location,
            reason=body.change_reason, operator_id=user.id,
        ))
        db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="RESCHEDULE_SESSION",
                            detail=f"场次#{s.id}从{before[0]}改为{s.starts_at}: {body.change_reason}"))
        _notify_session_members(db, s, title=f"答辩时间变更: {STAGE_CN.get(s.stage, s.stage)}",
                                content=f"场次「{s.title or s.id}」调整为 {s.starts_at:%Y-%m-%d %H:%M}~{s.ends_at:%H:%M}, 地点: {s.location or '线上'}。原因: {body.change_reason}")
    s.revision += 1
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="场次已更新")


# ---------- 席位与评委分配 ----------

class AssignmentAdd(BaseModel):
    cycle_ids: list[int] = Field(min_length=1, description="流程实例ID列表")


class ExaminerAdd(BaseModel):
    teacher_id: int
    examiner_role: str = Field(default="EXAMINER", pattern="^(CHAIR|EXAMINER|REVIEWER)$")


@router.post("/defense-sessions/{session_id}/assignments")
def add_assignments(session_id: int, body: AssignmentAdd, user: CurrentUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    s = db.get(DefenseSession, session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    if s.status in (SessionStatus.CANCELLED.value, SessionStatus.FINISHED.value):
        raise BusinessException("该场次已取消或结束, 不能排期")
    assigned = db.execute(select(func.count(DefenseAssignment.id)).where(
        DefenseAssignment.session_id == s.id)).scalar()
    if assigned + len(body.cycle_ids) > s.capacity:
        raise BusinessException(f"超出场次容量: 已排{assigned}, 容量{s.capacity}")
    added = []
    for cycle_id in body.cycle_ids:
        cycle = db.get(GraduationCycle, cycle_id)
        if cycle is None:
            raise ResourceNotFoundException(f"流程实例#{cycle_id}不存在")
        exists = db.execute(select(DefenseAssignment.id).where(
            DefenseAssignment.session_id == s.id, DefenseAssignment.cycle_id == cycle_id)).scalar_one_or_none()
        if exists:
            continue
        # 快速失败: 学生与其他有效场次时间冲突时直接拒绝(发布前仍会整体复检)
        student_conflicts = [c for c in _student_conflicts(db, s, cycle_id)]
        if student_conflicts:
            raise ConflictException(f"学生#{cycle.student_id}存在时间冲突: {student_conflicts[0]['detail']}")
        a = DefenseAssignment(session_id=s.id, cycle_id=cycle_id, seat_status=SeatStatus.SCHEDULED.value)
        db.add(a)
        db.flush()
        added.append(a.id)
        sp_user = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == cycle.student_id)).scalar()
        if sp_user:
            push_notification(db, user_id=sp_user, type="SYSTEM",
                              title=f"答辩排期通知: {STAGE_CN.get(s.stage, s.stage)}",
                              content=f"您已安排参加 {s.starts_at:%Y-%m-%d %H:%M} 的答辩, 地点: {s.location or '线上'}")
    db.commit()
    return ResponseUtil.success({"added": added}, message=f"已排入 {len(added)} 名学生")


def _student_conflicts(db: Session, session: DefenseSession, cycle_id: int) -> list[dict]:
    rows = db.execute(
        select(DefenseSession)
        .join(DefenseAssignment, DefenseAssignment.session_id == DefenseSession.id)
        .where(
            DefenseAssignment.cycle_id == cycle_id,
            DefenseAssignment.session_id != session.id,
            DefenseAssignment.seat_status != SeatStatus.POSTPONED.value,
            DefenseSession.status != SessionStatus.CANCELLED.value,
            DefenseSession.starts_at < session.ends_at,
            DefenseSession.ends_at > session.starts_at,
        )
    ).scalars().all()
    return [{"type": "STUDENT", "detail": f"与场次「{o.title or o.id}」时间重叠"} for o in rows]


@router.delete("/defense-sessions/{session_id}/assignments/{assignment_id}")
def remove_assignment(session_id: int, assignment_id: int, user: CurrentUser = Depends(require_admin),
                      db: Session = Depends(get_db)):
    a = _get_assignment_or_404(db, assignment_id)
    if a.session_id != session_id:
        raise ResourceNotFoundException("席位不属于该场次")
    if a.seat_status == SeatStatus.ATTENDED.value:
        raise BusinessException("已到场席位不能移除")
    db.delete(a)
    db.commit()
    return ResponseUtil.success(message="已移出该场次")


class SeatPatch(BaseModel):
    seat_status: str = Field(pattern="^(SCHEDULED|ATTENDED|ABSENT|POSTPONED|RETAKE)$")
    remark: str | None = Field(default=None, max_length=500)


@router.post("/defense-assignments/{assignment_id}/seat")
def patch_seat(assignment_id: int, body: SeatPatch, user: CurrentUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    """签到/缺席/延期标记; 延期必须填写原因(remark), 关联新场次由重新排期完成."""
    a = _get_assignment_or_404(db, assignment_id)
    if body.seat_status == SeatStatus.POSTPONED.value and not body.remark:
        raise BusinessException("延期必须填写原因")
    # 席位状态只能经状态机流转(SCHEDULED -> ATTENDED/ABSENT); 其余目标态转移表未定义, 直接 40003
    mark_seat(db, a, body.seat_status, user, remark=body.remark)
    db.commit()
    cycle = db.get(GraduationCycle, a.cycle_id)
    return ResponseUtil.success(assignment_item(db, a, cycle), message="席位状态已更新")


@router.post("/defense-sessions/{session_id}/examiners")
def add_examiner(session_id: int, body: ExaminerAdd, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    s = db.get(DefenseSession, session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    tp = db.get(TeacherProfile, body.teacher_id)
    if tp is None:
        raise ResourceNotFoundException("教师不存在")
    exists = db.execute(select(DefenseExaminer.id).where(
        DefenseExaminer.session_id == s.id, DefenseExaminer.teacher_id == body.teacher_id)).scalar_one_or_none()
    if exists:
        raise BusinessException("该教师已在本场次评委名单中")
    if body.examiner_role == "CHAIR":
        chair = db.execute(select(DefenseExaminer.id).where(
            DefenseExaminer.session_id == s.id, DefenseExaminer.examiner_role == "CHAIR")).scalar_one_or_none()
        if chair:
            raise BusinessException("每场次只能有一名主席")
    db.add(DefenseExaminer(session_id=s.id, teacher_id=body.teacher_id, examiner_role=body.examiner_role))
    db.commit()
    return ResponseUtil.success(session_item(db, s), message="评委已加入")


@router.delete("/defense-sessions/{session_id}/examiners/{examiner_id}")
def remove_examiner(session_id: int, examiner_id: int, user: CurrentUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    ex = db.get(DefenseExaminer, examiner_id)
    if ex is None or ex.session_id != session_id:
        raise ResourceNotFoundException("评委记录不存在")
    if ex.examiner_role == "CHAIR":
        raise BusinessException("主席需先移交或整场调整, 不能直接移除")
    db.delete(ex)
    db.commit()
    return ResponseUtil.success(message="评委已移除")


# ---------- 评审工作台(被指派教师) ----------

@router.get("/defense-assignments/mine")
def my_review_queue(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "TEACHER":
        raise PermissionDeniedException("该接口面向评委教师")
    rows = db.execute(
        select(DefenseAssignment, DefenseSession)
        .join(DefenseSession, DefenseSession.id == DefenseAssignment.session_id)
        .join(DefenseExaminer, DefenseExaminer.session_id == DefenseSession.id)
        .where(DefenseExaminer.teacher_id == user.profile_id,
               DefenseSession.status != SessionStatus.CANCELLED.value)
        .order_by(DefenseSession.starts_at)
    ).all()
    items = []
    for a, s in rows:
        item = assignment_item(db, a)
        item["session"] = {
            "id": s.id, "title": s.title, "stage": s.stage, "stage_cn": STAGE_CN.get(s.stage, s.stage),
            "starts_at": s.starts_at.isoformat() if s.starts_at else None,
            "ends_at": s.ends_at.isoformat() if s.ends_at else None,
            "location": s.location, "status": s.status, "status_cn": SESSION_STATUS_CN.get(s.status, s.status),
        }
        eff = get_effective_score(db, a.id, user.profile_id)
        item["my_score"] = {
            "id": eff.id, "status": eff.status, "status_cn": SCORE_STATUS_CN.get(eff.status, eff.status),
            "scores": _load_body_scores(eff.scores_json), "comment": eff.comment, "version": eff.version,
        } if eff else None
        items.append(item)
    return ResponseUtil.success(items)


def _ensure_examiner_can_review(db: Session, a: DefenseAssignment, user: CurrentUser) -> DefenseSession:
    """读接口: 管理员可读任意席位, 教师须是本场次被指派评委."""
    s = db.get(DefenseSession, a.session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    if user.role == "ADMIN":
        return s
    _ensure_examiner_of(db, s.id, user)
    return s


def _ensure_scoring_owner(db: Session, a: DefenseAssignment, user: CurrentUser) -> DefenseSession:
    """写接口(暂存/提交评分): 只允许被指派的评委教师.

    D19: 管理员 profile_id 与 DefenseExaminer.teacher_id 不同域, 管理员写评分会落出没有评委
    主体的评分行(examiner_id 对不上任何评委, 历史表现为 500 / 汇总结算漏算), 故写路径一律拒绝
    管理员; 管理员改分只能通过"复核重开"把评分交回原评委。
    """
    s = db.get(DefenseSession, a.session_id)
    if s is None:
        raise ResourceNotFoundException("场次不存在")
    if user.role != "TEACHER":
        raise PermissionDeniedException("仅被指派评委教师可暂存/提交评分")
    _ensure_examiner_of(db, s.id, user)
    return s


def _authorized_materials(db: Session, a: DefenseAssignment, stage: str) -> dict:
    """评委可见的评审材料: 该场次阶段所需的流程材料(只读, 最小必要)."""
    from app.models.business import FinalDefense, GraduationMaterial, MidtermReport, OpeningReport
    from app.models.system import SysFile

    cycle = db.get(GraduationCycle, a.cycle_id)
    row = db.execute(
        select(SysUser, StudentProfile).join(StudentProfile, StudentProfile.user_id == SysUser.id)
        .where(StudentProfile.id == cycle.student_id)
    ).first()
    topic_title = db.execute(select(GraduationTopic.title).where(
        GraduationTopic.id == cycle.topic_id)).scalar() if cycle.topic_id else None

    REVIEW_FIELDS = {
        OpeningReport: ["title", "research_background", "research_content", "technical_route", "schedule_plan"],
        MidtermReport: ["progress_description", "completed_content", "remaining_content", "existing_problems", "next_plan"],
        FinalDefense: ["summary", "innovation", "technical_solution", "test_result"],
    }

    def _stage_payload(model):
        r = db.execute(select(model).where(model.cycle_id == cycle.id)).scalar_one_or_none()
        if r is None:
            return {"status": "NOT_SUBMITTED", "content": {}}
        return {"status": r.status, "content": {f: getattr(r, f) for f in REVIEW_FIELDS[model]}}

    materials = {"topic_title": topic_title, "student": {
        "name": row[0].real_name if row else None, "student_no": row[1].student_no if row else None,
    }}
    include = {"OPENING": ["opening"], "MIDTERM": ["opening", "midterm"], "FINAL": ["opening", "midterm", "final"]}[stage]
    if "opening" in include:
        materials["opening"] = _stage_payload(OpeningReport)
    if "midterm" in include:
        materials["midterm"] = _stage_payload(MidtermReport)
    if "final" in include:
        materials["final"] = _stage_payload(FinalDefense)

    gm = db.execute(select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle.id)).scalar_one_or_none()
    files = []
    if gm and stage == "FINAL":
        from app.models.business import GraduationMaterialFile

        for mf, f in db.execute(
            select(GraduationMaterialFile, SysFile).join(SysFile, SysFile.id == GraduationMaterialFile.file_id)
            .where(GraduationMaterialFile.material_id == gm.id)
        ).all():
            files.append({"file_id": f.id, "material_type": mf.material_type,
                          "original_name": f.original_name, "size": f.size,
                          "url": f"/api/v1/files/{f.id}/download"})
    materials["graduation_files"] = files
    return materials


@router.get("/defense-assignments/{assignment_id}/review")
def get_review(assignment_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _get_assignment_or_404(db, assignment_id)
    s = _ensure_examiner_can_review(db, a, user)
    eff = None
    if user.role == "TEACHER":
        eff = get_effective_score(db, a.id, user.profile_id)
    rubric = get_rubric_for(db, s, db.get(GraduationCycle, a.cycle_id))
    return ResponseUtil.success({
        "assignment": assignment_item(db, a),
        "session": {"id": s.id, "stage": s.stage, "stage_cn": STAGE_CN.get(s.stage, s.stage),
                    "starts_at": s.starts_at.isoformat() if s.starts_at else None,
                    "location": s.location, "status": s.status},
        "materials": _authorized_materials(db, a, s.stage),
        "rubric": {"id": rubric.id, "version": rubric.version, "weights": json.loads(rubric.weights_json),
                   "min_examiners": rubric.min_examiners, "pass_line": rubric.pass_line} if rubric else None,
        "my_score": {
            "id": eff.id, "status": eff.status, "status_cn": SCORE_STATUS_CN.get(eff.status, eff.status),
            "scores": _load_body_scores(eff.scores_json), "comment": eff.comment, "version": eff.version,
        } if eff else None,
    })


class ReviewSave(BaseModel):
    scores: dict[str, float] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)


@router.put("/defense-assignments/{assignment_id}/review")
def save_review(assignment_id: int, body: ReviewSave, user: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """评委暂存评审草稿. 已提交锁定的评分需管理员复核重开后方可修改."""
    a = _get_assignment_or_404(db, assignment_id)
    s = _ensure_scoring_owner(db, a, user)
    if s.status == SessionStatus.CANCELLED.value:
        raise BusinessException("场次已取消, 不能评审")
    eff = get_effective_score(db, a.id, user.profile_id)
    if eff is not None and eff.status == ScoreStatus.SUBMITTED.value:
        raise BusinessException("评分已提交锁定, 如需修改请申请管理员复核重开")
    if eff is not None and eff.status == ScoreStatus.REOPENED.value:
        # 复核重开: 原版本置为 SUPERSEDED, 建立新版本草稿
        eff.status = ScoreStatus.SUPERSEDED.value
        draft = ExaminerScore(assignment_id=a.id, examiner_id=user.profile_id, rubric_id=eff.rubric_id,
                              rubric_version=eff.rubric_version, version=eff.version + 1,
                              status=ScoreStatus.DRAFT.value)
        db.add(draft)
        eff = draft
    if eff is None:
        rubric = get_rubric_for(db, s, db.get(GraduationCycle, a.cycle_id))
        eff = ExaminerScore(assignment_id=a.id, examiner_id=user.profile_id,
                            rubric_id=rubric.id if rubric else None,
                            rubric_version=rubric.version if rubric else None,
                            version=1, status=ScoreStatus.DRAFT.value)
        db.add(eff)
    eff.scores_json = json.dumps(body.scores, ensure_ascii=False)
    eff.comment = body.comment
    db.commit()
    return ResponseUtil.success({"id": eff.id, "version": eff.version, "status": eff.status}, message="评审草稿已暂存")


@router.post("/defense-assignments/{assignment_id}/scores/submit")
def submit_score(assignment_id: int, body: ReviewSave, user: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """提交并锁定评分版本; 校验评分项与启用评分表一致, 禁止重复提交有效评分."""
    a = _get_assignment_or_404(db, assignment_id)
    s = _ensure_scoring_owner(db, a, user)
    if s.status == SessionStatus.CANCELLED.value:
        raise BusinessException("场次已取消, 不能提交评分")
    cycle = db.get(GraduationCycle, a.cycle_id)
    rubric = get_rubric_for(db, s, cycle)
    if rubric is None:
        raise BusinessException("未配置启用的评分表, 请联系管理员")
    items = json.loads(rubric.weights_json)
    names = [it["name"] for it in items]
    missing = [n for n in names if n not in body.scores]
    if missing:
        raise BusinessException(f"评分项不完整, 缺少: {', '.join(missing)}")
    extra = [k for k in body.scores if k not in names]
    if extra:
        raise BusinessException(f"存在评分表之外的评分项: {', '.join(extra)}")
    for k, v in body.scores.items():
        if not (0 <= float(v) <= 100):
            raise BusinessException(f"评分项「{k}」须在 0~100 之间")

    eff = get_effective_score(db, a.id, user.profile_id)
    if eff is not None and eff.status == ScoreStatus.SUBMITTED.value:
        raise BusinessException("您已提交有效评分, 不能重复提交")
    if eff is not None and eff.status == ScoreStatus.REOPENED.value:
        eff.status = ScoreStatus.SUPERSEDED.value
        new = ExaminerScore(assignment_id=a.id, examiner_id=user.profile_id, rubric_id=eff.rubric_id,
                            rubric_version=eff.rubric_version, version=eff.version + 1,
                            status=ScoreStatus.SUBMITTED.value)
        db.add(new)
        eff = new
    elif eff is None:
        eff = ExaminerScore(assignment_id=a.id, examiner_id=user.profile_id,
                            rubric_id=rubric.id, rubric_version=rubric.version,
                            version=1, status=ScoreStatus.DRAFT.value)
        db.add(eff)
    eff.rubric_id = rubric.id
    eff.rubric_version = rubric.version
    eff.scores_json = json.dumps(body.scores, ensure_ascii=False)
    eff.comment = body.comment
    eff.status = ScoreStatus.SUBMITTED.value
    eff.submitted_at = datetime.now()
    # 评分变化后不直接改结论状态: RESULT 的 PENDING 只能由 transition(RESULT, "compute") 进入,
    # 已 READY 的结论在校验发布时会重新复算, 因此无需(也无权)在这里回写 result.status。

    # D24: 评委评分齐备时通知学生等待汇总(此前是未使用的死变量)
    assigned = db.execute(select(func.count(DefenseExaminer.id)).where(
        DefenseExaminer.session_id == s.id)).scalar() or 0
    submitted = db.execute(select(func.count(ExaminerScore.id)).where(
        ExaminerScore.assignment_id == a.id,
        ExaminerScore.status == ScoreStatus.SUBMITTED.value)).scalar() or 0
    if assigned and submitted >= assigned:
        sp_user = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == cycle.student_id)).scalar()
        if sp_user:
            push_notification(db, user_id=sp_user, type="REVIEW_RESULT",
                              title="评委评分已齐备",
                              content=f"您的{STAGE_CN.get(s.stage, s.stage)}评分已全部提交, 等待管理员汇总发布。",
                              business_type="DEFENSE_RESULT", business_id=a.id)
    db.add(OperationLog(user_id=user.id, module="DEFENSE", action="SUBMIT_SCORE",
                        detail=f"评委#{user.profile_id}提交席位#{a.id}评分 v{eff.version}"))
    db.commit()
    return ResponseUtil.success({"id": eff.id, "version": eff.version, "status": eff.status}, message="评分已提交并锁定")


# ---------- 成绩汇总与发布(管理员) ----------

@router.get("/defense-results")
def list_results(session_id: int | None = None, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    conds = []
    if session_id:
        conds.append(DefenseAssignment.session_id == session_id)
    rows = db.execute(
        select(DefenseResult, DefenseAssignment)
        .join(DefenseAssignment, DefenseAssignment.id == DefenseResult.assignment_id)
        .where(*conds).order_by(DefenseResult.id.desc())
    ).all()
    items = []
    for r, a in rows:
        item = assignment_item(db, a)
        item["result"] = {
            "id": r.id, "status": r.status, "status_cn": {"PENDING": "待汇总", "READY": "可复核", "UNDER_REVIEW": "复核中",
                                                          "PUBLISHED": "已发布", "WITHDRAWN": "已撤回"}.get(r.status, r.status),
            "score": r.score, "decision": r.decision,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "withdraw_reason": r.withdraw_reason,
        }
        items.append(item)
    return ResponseUtil.success(items)


class ResultGenerate(BaseModel):
    assignment_id: int


@router.post("/defense-results/generate")
def generate_result(body: ResultGenerate, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """为席位生成/取回汇总记录(PENDING), 供预览与发布."""
    a = db.get(DefenseAssignment, body.assignment_id)
    if a is None:
        raise ResourceNotFoundException("答辩席位不存在")
    r = db.execute(select(DefenseResult).where(DefenseResult.assignment_id == a.id)).scalar_one_or_none()
    if r is None:
        r = DefenseResult(assignment_id=a.id, cycle_id=a.cycle_id,
                          stage=db.get(DefenseSession, a.session_id).stage, status=ResultStatus.PENDING.value)
        db.add(r)
        db.flush()  # 取到主键; 状态变更随后交给状态机
    if r.status == ResultStatus.PENDING.value:
        # PENDING -> READY(internal 系统动作); 未复算的结论不允许发布
        calculate_result_flow(db, r.id, user)
    db.commit()
    return ResponseUtil.success({"id": r.id, "status": r.status}, message="汇总记录已生成并完成复算")


@router.post("/defense-results/{result_id}/calculate")
def calculate_result(result_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """复算汇总: PENDING -> READY(重复复算由状态机抛 40003, 保证口径不可被随意改写)."""
    r, computed = calculate_result_flow(db, result_id, user)
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="CALCULATE_RESULT",
                        detail=f"复算结论#{r.id}: {computed['score']}({computed['decision']}) "
                               f"精确分 {computed['score_precise']} 样本 {computed['valid_count']}/{computed['min_required']}"))
    db.commit()
    return ResponseUtil.success({"id": r.id, "status": r.status, "score": r.score,
                                 "score_precise": computed["score_precise"], "decision": r.decision},
                                message="结论已复算")


@router.get("/defense-results/{result_id}/preview")
def preview_result(result_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """服务端复算汇总(不改状态): 原始评分与汇总结果可复算(roadmap P2 验收)."""
    r = db.get(DefenseResult, result_id)
    if r is None:
        raise ResourceNotFoundException("结论不存在")
    a = db.get(DefenseAssignment, r.assignment_id)
    s = db.get(DefenseSession, a.session_id)
    cycle = db.get(GraduationCycle, a.cycle_id)
    rubric = get_rubric_for(db, s, cycle)
    if rubric is None:
        raise BusinessException("未配置启用的评分表")
    computed = compute_result(db, a, rubric)
    computed["exists"] = True
    computed["result_id"] = r.id
    computed["status"] = r.status
    computed["published_score"] = r.score
    computed["publishable"] = (
        computed["valid_count"] >= rubric.min_examiners
        and computed["score"] is not None
        and computed["seat_attended"]
        and s.status not in (SessionStatus.DRAFT.value, SessionStatus.CANCELLED.value)
        and r.status in (ResultStatus.READY.value, ResultStatus.UNDER_REVIEW.value)
    )
    if r.score is not None:
        computed["recompute_match"] = (r.score == computed["score"])
    return ResponseUtil.success(computed)


@router.post("/defense-results/{result_id}/publish")
def publish_result(result_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """发布正式结论: 同一事务中复查评委人数/评分完整性/到场状态, 并留痕."""
    # 服务层取行锁 + 复检场次/到场/评委/完整性, 再走 READY|UNDER_REVIEW -> PUBLISHED
    r, computed = publish_result_flow(db, result_id, user)
    a = db.get(DefenseAssignment, r.assignment_id)
    s = db.get(DefenseSession, a.session_id)
    cycle = db.get(GraduationCycle, a.cycle_id)
    db.add(OperationLog(user_id=user.id, module="DEFENSE", action="PUBLISH_RESULT",
                        detail=f"发布席位#{a.id}正式结论: {computed['score']}分 {computed['decision']}"))

    decision_cn = {"PASS": "通过", "FAIL": "不通过"}.get(computed["decision"], computed["decision"])
    sp_user = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == cycle.student_id)).scalar()
    if sp_user:
        push_notification(db, user_id=sp_user, type="REVIEW_RESULT",
                          title=f"{STAGE_CN.get(s.stage, s.stage)}结论已发布",
                          content=f"您的{STAGE_CN.get(s.stage, s.stage)}结论已发布: {computed['score']}分({decision_cn})",
                          business_type="DEFENSE_RESULT", business_id=r.id)
    db.commit()
    return ResponseUtil.success({"id": r.id, "score": r.score, "decision": r.decision, "status": r.status},
                                message="正式结论已发布")


class WithdrawRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


@router.post("/defense-results/{result_id}/withdraw")
def withdraw_result(result_id: int, body: WithdrawRequest, user: CurrentUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    # PUBLISHED -> WITHDRAWN; 转移表要求 comment, 未发布/已撤回会由状态机抛 40003
    r = withdraw_result_flow(db, result_id, user, body.reason)
    db.add(OperationLog(user_id=user.id, module="DEFENSE", action="WITHDRAW_RESULT",
                        detail=f"撤回结论#{r.id}: {body.reason}"))
    a = db.get(DefenseAssignment, r.assignment_id)
    cycle = db.get(GraduationCycle, a.cycle_id)
    sp_user = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == cycle.student_id)).scalar()
    if sp_user:
        push_notification(db, user_id=sp_user, type="REVIEW_RESULT",
                          title="成绩复核通知", content="您的答辩成绩正在复核中, 复核完成后将重新发布。")
    db.commit()
    return ResponseUtil.success({"id": r.id, "status": r.status}, message="结论已撤回, 学生端显示复核中")


class ReopenRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


@router.post("/examiner-scores/{score_id}/reopen")
def reopen_score(score_id: int, body: ReopenRequest, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """复核重开: 评分进入 REOPENED, 评委产生新版本, 旧版本保留(SUPERSEDED), 全程可追溯."""
    eff = db.get(ExaminerScore, score_id)
    if eff is None:
        raise ResourceNotFoundException("评分记录不存在")
    if eff.status != ScoreStatus.SUBMITTED.value:
        raise BusinessException("仅已提交评分可重开")
    eff.status = ScoreStatus.REOPENED.value
    a = db.get(DefenseAssignment, eff.assignment_id)
    result = db.execute(select(DefenseResult).where(DefenseResult.assignment_id == a.id)).scalar_one_or_none()
    if result is not None:
        # 已发布结论转入复核中(状态机 RESULT.review, 见 shared/state_machine.py)
        mark_result_under_review(db, result, body.reason, actor=user)
    tu = db.execute(select(TeacherProfile.user_id).where(TeacherProfile.id == eff.examiner_id)).scalar()
    if tu:
        push_notification(db, user_id=tu, type="SYSTEM", title="评分复核重开通知",
                          content=f"管理员已重开您的评分(席位#{a.id}), 请修改后重新提交。原因: {body.reason}")
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="REOPEN_SCORE",
                        detail=f"重开评分#{score_id}: {body.reason}"))
    db.commit()
    return ResponseUtil.success({"id": eff.id, "status": eff.status}, message="评分已重开, 等待评委修改")


# ---------- 学生: 我的答辩 ----------

@router.post("/defense-assignments/{assignment_id}/acknowledge")
def acknowledge(assignment_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _get_assignment_or_404(db, assignment_id)
    _ensure_student_own_assignment(db, a, user)
    a.acknowledged_at = datetime.now()
    db.commit()
    return ResponseUtil.success(message="已确认")


# ---------- 评分表配置 ----------

class RubricCreate(BaseModel):
    academic_year: str = Field(min_length=4, max_length=32)
    stage: str = Field(pattern="^(OPENING|MIDTERM|FINAL)$")
    college_id: int | None = None
    min_examiners: int = Field(default=3, ge=1, le=20)
    weights: list[dict] = Field(min_length=1, description='[{"name":"报告质量","weight":40}]')
    pass_line: int = Field(default=60, ge=0, le=100)


@rubric_router.get("")
def list_rubrics(academic_year: str | None = None, stage: str | None = None,
                 user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conds = []
    if academic_year:
        conds.append(ReviewRubric.academic_year == academic_year)
    if stage:
        conds.append(ReviewRubric.stage == stage)
    rows = db.execute(select(ReviewRubric).where(*conds).order_by(ReviewRubric.id.desc())).scalars().all()
    return ResponseUtil.success([{
        "id": r.id, "academic_year": r.academic_year, "stage": r.stage, "stage_cn": STAGE_CN.get(r.stage, r.stage),
        "college_id": r.college_id, "version": r.version, "min_examiners": r.min_examiners,
        "weights": json.loads(r.weights_json), "pass_line": r.pass_line,
        "enabled": bool(r.enabled), "frozen_at": r.frozen_at.isoformat() if r.frozen_at else None,
    } for r in rows])


@rubric_router.post("")
def create_rubric(body: RubricCreate, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    _check_weights(body.weights)
    r = ReviewRubric(academic_year=body.academic_year, stage=body.stage, college_id=body.college_id,
                     min_examiners=body.min_examiners, pass_line=body.pass_line,
                     weights_json=json.dumps(body.weights, ensure_ascii=False), enabled=0, version=1)
    db.add(r)
    db.flush()
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="CREATE_RUBRIC",
                        detail=f"创建评分表#{r.id}，版本{r.version}"))
    db.commit()
    return ResponseUtil.success({"id": r.id}, message="评分表已创建(未启用)")


@rubric_router.put("/{rubric_id}")
def update_rubric(rubric_id: int, body: RubricCreate, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """修改评分表产生新版本; 已启用版本冻结不可改(roadmap P2: 修改评分可追溯)."""
    old = db.get(ReviewRubric, rubric_id)
    if old is None:
        raise ResourceNotFoundException("评分表不存在")
    if old.enabled == 1:
        raise BusinessException("已启用的评分表版本已冻结, 请创建新版本")
    _check_weights(body.weights)
    max_version = db.execute(select(func.max(ReviewRubric.version)).where(
        ReviewRubric.academic_year == old.academic_year, ReviewRubric.stage == old.stage,
        ReviewRubric.college_id == old.college_id if old.college_id is not None else ReviewRubric.college_id.is_(None),
    )).scalar() or 1
    old.enabled = 0
    new = ReviewRubric(academic_year=body.academic_year, stage=body.stage, college_id=body.college_id,
                       min_examiners=body.min_examiners, pass_line=body.pass_line,
                       weights_json=json.dumps(body.weights, ensure_ascii=False),
                       enabled=0, version=max_version + 1)
    db.add(new)
    db.flush()
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="UPDATE_RUBRIC",
                        detail=f"评分表#{rubric_id}生成新版本#{new.id}，版本{new.version}"))
    db.commit()
    return ResponseUtil.success({"id": new.id, "version": new.version}, message="已生成新版本评分表")


@rubric_router.post("/{rubric_id}/enable")
def enable_rubric(rubric_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(ReviewRubric, rubric_id)
    if r is None:
        raise ResourceNotFoundException("评分表不存在")
    others = db.execute(select(ReviewRubric).where(
        ReviewRubric.academic_year == r.academic_year, ReviewRubric.stage == r.stage,
        ReviewRubric.college_id == r.college_id if r.college_id is not None else ReviewRubric.college_id.is_(None),
        ReviewRubric.enabled == 1,
    )).scalars().all()
    for o in others:
        o.enabled = 0
    r.enabled = 1
    r.frozen_at = datetime.now()
    db.add(OperationLog(user_id=user.id, username=user.username, module="DEFENSE", action="ENABLE_RUBRIC",
                        detail=f"启用评分表#{r.id}，版本{r.version}"))
    db.commit()
    return ResponseUtil.success({"id": r.id, "version": r.version}, message="评分表已启用并冻结")


def _check_weights(weights: list[dict]) -> None:
    names = set()
    total = 0
    for it in weights:
        name = (it.get("name") or "").strip()
        w = it.get("weight")
        if not name:
            raise BusinessException("评分项名称不能为空")
        if name in names:
            raise BusinessException(f"评分项重复: {name}")
        if not isinstance(w, (int, float)) or w <= 0:
            raise BusinessException(f"评分项「{name}」权重必须大于 0")
        names.add(name)
        total += w
    if total != 100:
        raise BusinessException(f"评分项权重合计必须为 100%, 当前 {total}%")