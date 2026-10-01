"""Selection 模块: 学生选题 + 教师/管理员审核.

并发控制(roadmap 八十三):
1. 一个流程实例(学年)一条 active 选题 → UNIQUE(cycle_id, active_flag) 数据库约束
2. 审核通过时 SELECT ... FOR UPDATE 锁题目行 + 复查已通过人数, 防止超额
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationTopic, TopicSelection
from app.models.enums import BusinessType
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.shared.events import add_review_record, now, push_notification
from app.shared.progress import ProgressService
from app.shared.workflow import GraduationWorkflowService

router = APIRouter(prefix="/student/selections", tags=["Selection-Student"])
teacher_router = APIRouter(prefix="/teacher/selections", tags=["Selection-Teacher"])
admin_router = APIRouter(prefix="/admin/selections", tags=["Selection-Admin"])


class ApplyRequest(BaseModel):
    topic_id: int
    reason: str = Field(min_length=10, max_length=2000, description="选题理由")


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


def _selection_item(db: Session, s: TopicSelection) -> dict:
    topic = db.get(GraduationTopic, s.topic_id)
    student_user = db.execute(
        select(SysUser).join(StudentProfile, StudentProfile.user_id == SysUser.id).where(StudentProfile.id == s.student_id)
    ).scalar_one_or_none()
    teacher_name = None
    if s.teacher_id:
        teacher_name = db.execute(
            select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
            .where(TeacherProfile.id == s.teacher_id)
        ).scalar()
    reviewer = db.get(SysUser, s.reviewer_id) if s.reviewer_id else None
    return {
        "id": s.id, "topic_id": s.topic_id, "topic_title": topic.title if topic else None,
        "teacher_id": s.teacher_id, "teacher_name": teacher_name, "status": s.status,
        "student_reason": s.student_reason,
        "review_comment": s.review_comment, "review_time": s.review_time.isoformat() if s.review_time else None,
        "reviewer_name": reviewer.real_name if reviewer else None,
        "student_id": s.student_id,
        "student_name": student_user.real_name if student_user else None,
        "student_no": student_user.username if student_user else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ---------- 学生 ----------

@router.post("")
def apply_selection(body: ApplyRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    from app.shared.cycle import ensure_cycle

    GraduationWorkflowService.require_can_apply_selection(db, user.profile_id)

    topic = db.execute(
        select(GraduationTopic).where(
            GraduationTopic.id == body.topic_id,
            GraduationTopic.status == "PUBLISHED",
            GraduationTopic.deleted_at.is_(None),
        ).with_for_update()
    ).scalar_one_or_none()
    if topic is None:
        raise ResourceNotFoundException("题目不存在或未发布")

    approved = db.execute(
        select(func.count(TopicSelection.id)).where(
            TopicSelection.topic_id == topic.id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1
        )
    ).scalar()
    if approved >= topic.max_students:
        raise BusinessException("该题目名额已满")

    # 选题时即建立当前学年流程实例, 选题/材料/答辩全部归属该实例
    cycle = ensure_cycle(db, user.profile_id)

    # 将本流程实例内旧的 REJECTED/CANCELLED active 记录归档(active_flag → NULL)
    old = db.execute(
        select(TopicSelection).where(TopicSelection.cycle_id == cycle.id, TopicSelection.active_flag == 1)
    ).scalars().all()
    for o in old:
        o.active_flag = None

    selection = TopicSelection(
        student_id=user.profile_id, cycle_id=cycle.id, topic_id=topic.id, teacher_id=topic.teacher_id,
        status="PENDING", student_reason=body.reason, active_flag=1,
    )
    db.add(selection)
    db.flush()
    add_review_record(db, business_type=BusinessType.TOPIC_SELECTION.value, business_id=selection.id,
                      student_id=user.profile_id, action="SUBMIT", before_status=None, after_status="PENDING",
                      comment=body.reason)
    # 通知教师
    teacher_user_id = db.execute(select(TeacherProfile.user_id).where(TeacherProfile.id == topic.teacher_id)).scalar()
    push_notification(db, user_id=teacher_user_id, type="REVIEW_RESULT",
                      title=f"新选题申请: {topic.title}",
                      content=f"学生 {user.real_name} 申请选择您的题目, 请及时审核",
                      business_type=BusinessType.TOPIC_SELECTION.value, business_id=selection.id)
    db.commit()
    return ResponseUtil.success({"id": selection.id}, message="选题申请已提交,等待教师审核")


@router.get("")
def my_selections(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    rows = db.execute(
        select(TopicSelection).where(TopicSelection.student_id == user.profile_id).order_by(TopicSelection.id.desc())
    ).scalars().all()
    return ResponseUtil.success([_selection_item(db, s) for s in rows])


@router.post("/{selection_id}/cancel")
def cancel_selection(selection_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    s = db.get(TopicSelection, selection_id)
    if s is None or s.student_id != user.profile_id:
        raise ResourceNotFoundException("选题记录不存在")
    if s.status != "PENDING":
        raise BusinessException("仅待审核状态可撤销")
    s.status = "CANCELLED"
    s.active_flag = None
    add_review_record(db, business_type=BusinessType.TOPIC_SELECTION.value, business_id=s.id,
                      student_id=s.student_id, action="RETURN", before_status="PENDING", after_status="CANCELLED",
                      comment="学生撤销申请")
    db.commit()
    return ResponseUtil.success(message="已撤销选题申请")


# ---------- 教师审核 ----------

@teacher_router.get("")
def teacher_selections(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    conds = [TopicSelection.teacher_id == user.profile_id]
    if status:
        conds.append(TopicSelection.status == status)
    total = db.execute(select(func.count(TopicSelection.id)).where(*conds)).scalar()
    rows = db.execute(select(TopicSelection).where(*conds).order_by(TopicSelection.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_selection_item(db, s) for s in rows], total, page, page_size)


def _review_selection(db: Session, selection_id: int, teacher_profile_id: int | None, reviewer_user_id: int,
                       approve: bool, comment: str | None) -> dict:
    """审核核心: 事务 + 行锁 + 名额复查(允许 ADMIN 代审, 教师仅限本人题目)."""
    s = db.get(TopicSelection, selection_id)
    if s is None:
        raise ResourceNotFoundException("选题记录不存在")
    if teacher_profile_id is not None and s.teacher_id != teacher_profile_id:
        raise BusinessException("只能审核自己题目的选题申请")
    if s.status != "PENDING":
        raise BusinessException("该申请已被处理")

    before = s.status
    if approve:
        # 锁题目行并复查名额, 防止并发下超额通过
        topic = GraduationWorkflowService.get_topic_with_lock(db, s.topic_id)
        approved = db.execute(
            select(func.count(TopicSelection.id)).where(
                TopicSelection.topic_id == topic.id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1
            )
        ).scalar()
        if approved >= topic.max_students:
            db.rollback()
            raise BusinessException("该题目名额已满, 无法通过")

        s.status = "APPROVED"
        s.review_comment = comment
        s.reviewer_id = reviewer_user_id
        s.review_time = now()
        add_review_record(db, business_type=BusinessType.TOPIC_SELECTION.value, business_id=s.id,
                          student_id=s.student_id, reviewer_id=reviewer_user_id, reviewer_role="TEACHER",
                          action="APPROVE", before_status=before, after_status="APPROVED", comment=comment)
        GraduationWorkflowService.on_selection_approved(db, s)
        ProgressService.recompute(db, s.student_id)
        topic.status = "CLOSED" if topic.max_students <= approved + 1 else topic.status
        message = "选题申请已通过"
    else:
        s.status = "REJECTED"
        s.review_comment = comment
        s.reviewer_id = reviewer_user_id
        s.review_time = now()
        s.active_flag = None  # 驳回后归档, 允许学生重新选题
        add_review_record(db, business_type=BusinessType.TOPIC_SELECTION.value, business_id=s.id,
                          student_id=s.student_id, reviewer_id=reviewer_user_id, reviewer_role="TEACHER",
                          action="REJECT", before_status=before, after_status="REJECTED", comment=comment)
        message = "选题申请已驳回"

    student_user_id = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == s.student_id)).scalar()
    topic = db.get(GraduationTopic, s.topic_id)
    push_notification(db, user_id=student_user_id, type="REVIEW_RESULT",
                      title=f"选题审核结果: {topic.title if topic else ''}",
                      content=f"{'通过' if approve else '驳回'}。审核意见: {comment or '无'}",
                      business_type=BusinessType.TOPIC_SELECTION.value, business_id=s.id)
    db.commit()
    return {"id": s.id, "status": s.status, "message": message}


@teacher_router.post("/{selection_id}/approve")
def approve_selection(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                      db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_selection(db, selection_id, user.profile_id, user.id, True, body.comment))


@teacher_router.post("/{selection_id}/reject")
def reject_selection(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                     db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_selection(db, selection_id, user.profile_id, user.id, False, body.comment))


# ---------- 管理员全局 ----------

@admin_router.get("")
def admin_selections(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if status:
        conds.append(TopicSelection.status == status)
    total = db.execute(select(func.count(TopicSelection.id)).where(*conds)).scalar()
    rows = db.execute(select(TopicSelection).where(*conds).order_by(TopicSelection.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_selection_item(db, s) for s in rows], total, page, page_size)


@admin_router.post("/{selection_id}/approve")
def admin_approve(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_selection(db, selection_id, None, user.id, True, body.comment))


@admin_router.post("/{selection_id}/reject")
def admin_reject(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_selection(db, selection_id, None, user.id, False, body.comment))
