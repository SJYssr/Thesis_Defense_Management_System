"""Selection 模块: 学生选题 + 教师/管理员审核.

并发控制(roadmap 八十三 / D27):
1. 一个流程实例(学年)一条 active 选题 → UNIQUE(cycle_id, active_flag) 数据库约束
2. 审核通过时 SELECT ... FOR UPDATE 锁题目行, 再用**锁定读**复查已通过人数。
   只锁行不够: REPEATABLE READ 下普通 SELECT COUNT(*) 读的是事务开始时的快照,
   两个并发审核会都看到"还差一个名额"从而超额录取。锁定读(SELECT ... FOR UPDATE)
   读最新已提交版本, 与行锁配合才能保证名额不被突破。
3. 状态变更全部走 state_machine.transition(SELECTION 表块), 条件更新 rowcount!=1 即失败。
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationTopic, TopicSelection
from app.models.enums import BusinessType
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.shared import state_machine as sm
from app.shared.events import add_review_record, now, push_notification
from app.shared.progress import ProgressService
from app.shared.query import paginate, time_range_conditions
from app.shared.workflow import GraduationWorkflowService

router = APIRouter(prefix="/student/selections", tags=["Selection-Student"])
teacher_router = APIRouter(prefix="/teacher/selections", tags=["Selection-Teacher"])
admin_router = APIRouter(prefix="/admin/selections", tags=["Selection-Admin"])


class ApplyRequest(BaseModel):
    topic_id: int
    reason: str = Field(min_length=10, max_length=2000, description="选题理由")


REVIEW_DECISION = Literal["approve", "reject", "request_revision"]


class ReviewRequest(BaseModel):
    """审核请求体.

    decision 显式表达审核结论(与 stages 模块 P1-A 对齐), 不再靠"comment 是否为空"推断;
    缺省时按端点语义取值(approve 端点=approve, reject 端点=reject), 保持旧调用兼容。
    选题状态机只有 approve/reject 两个审核动作, 显式 request_revision 由业务层明确报错。
    """

    comment: str | None = Field(default=None, max_length=500)
    decision: REVIEW_DECISION | None = Field(
        default=None, description="审核结论: approve/reject; 选题不支持 request_revision")


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


# ---------- 列表过滤(§9/D10: 全量下推 SQL, 先过滤后分页) ----------

# keyword 模糊匹配三条路径: 学生姓名(real_name) / 学号(student_no) / 题目名(topic.title).
# 关联路径: topic_selection.student_id → student_profile.id → sys_user.id,
#           topic_selection.topic_id   → graduation_topic.id(最短路径, 不经 cycle).
KEYWORD_FIELDS = (SysUser.real_name, StudentProfile.student_no, GraduationTopic.title)


def _list_stmt():
    """教师/管理员列表基础语句: 学生姓名/学号/题目名既要展示也要能关键字过滤(§9)."""
    return (
        select(TopicSelection)
        .outerjoin(StudentProfile, StudentProfile.id == TopicSelection.student_id)
        .outerjoin(SysUser, SysUser.id == StudentProfile.user_id)
        .outerjoin(GraduationTopic, GraduationTopic.id == TopicSelection.topic_id)
        .order_by(TopicSelection.id.desc())
    )


def _approved_selection_ids(db: Session, topic_id: int) -> list[int]:
    """该题目已占用名额的选题 id —— **锁定读**, 读最新已提交版本(D27).

    不能用 select(func.count(...)): 聚合查询在 REPEATABLE READ 下走一致性快照,
    并发审核时两个事务都会读到"未满"; 锁定读(当前读)才能看到对方刚提交的 APPROVED 行。
    """
    return list(db.execute(
        select(TopicSelection.id).where(
            TopicSelection.topic_id == topic_id,
            TopicSelection.status == "APPROVED",
            TopicSelection.active_flag == 1,
        ).with_for_update()
    ).scalars().all())


# ---------- 学生 ----------

@router.post("")
def apply_selection(body: ApplyRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    from app.shared.cycle import ensure_cycle

    GraduationWorkflowService.require_can_apply_selection(db, user.profile_id)

    # 选题时间窗校验(roadmap 八十五章"允许选题时间"): 以当前激活学年配置为准
    ProgressService.ensure_selection_window_open(db)

    topic = db.execute(
        select(GraduationTopic).where(
            GraduationTopic.id == body.topic_id,
            GraduationTopic.status == "PUBLISHED",
            GraduationTopic.deleted_at.is_(None),
        ).with_for_update()
    ).scalar_one_or_none()
    if topic is None:
        raise ResourceNotFoundException("题目不存在或未发布")

    # 名额复查用锁定读, 不用快照聚合(D27)
    if len(_approved_selection_ids(db, topic.id)) >= topic.max_students:
        raise BusinessException("该题目名额已满")

    # 选题时即建立当前学年流程实例, 选题/材料/答辩全部归属该实例
    cycle = ensure_cycle(db, user.profile_id)

    # 将本流程实例内旧的 REJECTED/CANCELLED active 记录归档(active_flag → NULL)
    old = db.execute(
        select(TopicSelection).where(TopicSelection.cycle_id == cycle.id, TopicSelection.active_flag == 1)
    ).scalars().all()
    for o in old:
        o.active_flag = None

    # 新建对象以业务初始态落库(与 stages/engine 新建报告的先例一致), 随后由 transition 留痕
    selection = TopicSelection(
        student_id=user.profile_id, cycle_id=cycle.id, topic_id=topic.id, teacher_id=topic.teacher_id,
        status="PENDING", student_reason=body.reason, active_flag=1,
    )
    db.add(selection)
    db.flush()
    sm.transition(db, sm.SELECTION, selection, "submit", user, comment=body.reason)
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
    sm.transition(db, sm.SELECTION, s, "cancel", user, comment="学生撤销申请")
    s.active_flag = None
    # cancel 不在 state_machine._RECORD_ACTIONS 里, 撤销留痕由业务层补齐
    add_review_record(db, business_type=BusinessType.TOPIC_SELECTION.value, business_id=s.id,
                      student_id=s.student_id, reviewer_id=user.id, reviewer_role=user.role,
                      action="RETURN", before_status="PENDING", after_status="CANCELLED",
                      comment="学生撤销申请")
    db.commit()
    return ResponseUtil.success(message="已撤销选题申请")


# ---------- 教师审核 ----------

@teacher_router.get("")
def teacher_selections(status: str | None = None, keyword: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    """教师选题审核列表.

    - keyword: 模糊匹配 学生姓名 / 学号 / 题目名(OR, 见 KEYWORD_FIELDS);
    - date_from / date_to: 按**申请提交时间** `topic_selection.created_at` 过滤
      (纯日期含当日整日, 带时刻按精确时刻比较; 非法日期 → 40002);
    - 归属约束始终生效: 只返回 teacher_id == 当前教师 的记录, 关键字/日期不会绕过它;
    - 过滤与分页全部在 SQL 内完成, total 是过滤后的总数(先过滤后分页).
    """
    filters = [TopicSelection.teacher_id == user.profile_id]
    if status:
        filters.append(TopicSelection.status == status)
    filters.extend(time_range_conditions(TopicSelection.created_at, date_from, date_to))
    rows, total = paginate(db, _list_stmt(), page=page, size=page_size,
                           keyword=keyword, keyword_fields=KEYWORD_FIELDS, filters=filters)
    return ResponseUtil.page([_selection_item(db, s) for s in rows], total, page, page_size)


def _review_selection(db: Session, selection_id: int, user: CurrentUser,
                       decision: str, comment: str | None) -> dict:
    """审核核心: 行锁 + 名额锁定读复查 + state_machine 转移(允许 ADMIN 代审, 教师仅限本人题目).

    decision 为显式三值语义: approve / reject / request_revision。
    SELECTION 表块只有 approve/reject, 因此 request_revision 在这里给出明确业务错误,
    不用"猜"的方式降级(§4, D7)。
    """
    if decision == "request_revision":
        raise BusinessException("选题审核不支持 request_revision 结论, 请使用 reject(驳回后学生可重新申请)")
    if decision not in ("approve", "reject"):
        raise BusinessException("审核结论非法")
    approve = decision == "approve"
    s = db.get(TopicSelection, selection_id)
    if s is None:
        raise ResourceNotFoundException("选题记录不存在")
    if user.role != "ADMIN" and s.teacher_id != user.profile_id:
        raise BusinessException("只能审核自己题目的选题申请")
    if s.status != "PENDING":
        raise BusinessException("该申请已被处理")

    if approve:
        # 锁题目行 + 锁定读复查名额, 防止并发下超额通过(D27)
        topic = GraduationWorkflowService.get_topic_with_lock(db, s.topic_id)
        approved = len(_approved_selection_ids(db, topic.id))
        if approved >= topic.max_students:
            db.rollback()
            raise BusinessException("该题目名额已满, 无法通过")
        sm.transition(db, sm.SELECTION, s, "approve", user, comment=comment)
        s.reviewer_id = user.id
        s.review_time = now()
        GraduationWorkflowService.on_selection_approved(db, s)
        ProgressService.recompute(db, s.student_id, cycle_id=s.cycle_id)
        # 名额占满后关闭题目: 走 TOPIC 转移表(close: PUBLISHED→CLOSED, _REVIEWER), 以真实操作人身份提交;
        # sm.can 预判, 避免题目已是 CLOSED/DRAFT 时抛 40003 打断本次审核(§4: 禁直写 status)
        if topic.max_students <= approved + 1 and sm.can(sm.TOPIC, "close", topic, user):
            sm.transition(db, sm.TOPIC, topic, "close", user)
        message = "选题申请已通过"
    else:
        sm.transition(db, sm.SELECTION, s, "reject", user, comment=comment)
        s.reviewer_id = user.id
        s.review_time = now()
        s.active_flag = None  # 驳回后归档, 允许学生重新选题
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
    decision = "request_revision" if body.decision == "request_revision" else "approve"
    return ResponseUtil.success(_review_selection(db, selection_id, user, decision, body.comment))


@teacher_router.post("/{selection_id}/reject")
def reject_selection(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                     db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "reject"
    return ResponseUtil.success(_review_selection(db, selection_id, user, decision, body.comment))


# ---------- 管理员全局 ----------

@admin_router.get("")
def admin_selections(status: str | None = None, keyword: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """管理员全局选题列表(与 teacher_selections 同口径, 无归属约束).

    - keyword: 模糊匹配 学生姓名 / 学号 / 题目名(OR, 见 KEYWORD_FIELDS);
    - date_from / date_to: 按**申请提交时间** `topic_selection.created_at` 过滤;
    - 过滤与分页全部在 SQL 内完成, total 是过滤后的总数(先过滤后分页).
    """
    filters = []
    if status:
        filters.append(TopicSelection.status == status)
    filters.extend(time_range_conditions(TopicSelection.created_at, date_from, date_to))
    rows, total = paginate(db, _list_stmt(), page=page, size=page_size,
                           keyword=keyword, keyword_fields=KEYWORD_FIELDS, filters=filters)
    return ResponseUtil.page([_selection_item(db, s) for s in rows], total, page, page_size)


@admin_router.post("/{selection_id}/approve")
def admin_approve(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "approve"
    return ResponseUtil.success(_review_selection(db, selection_id, user, decision, body.comment))


@admin_router.post("/{selection_id}/reject")
def admin_reject(selection_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "reject"
    return ResponseUtil.success(_review_selection(db, selection_id, user, decision, body.comment))
