"""Topic 模块: 教师发布/管理题目, 学生/管理员浏览题目."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationTopic, TopicSelection
from app.models.system import OperationLog
from app.models.user import College, Major, SysUser, TeacherProfile

router = APIRouter(prefix="/topics", tags=["Topic"])


class TopicCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None
    requirements: str | None = None
    topic_type: str | None = None
    difficulty: str | None = Field(default=None, pattern="^(EASY|MEDIUM|HARD)$")
    major_id: int | None = None
    max_students: int = Field(default=1, ge=1, le=10)


class TopicUpdateRequest(TopicCreateRequest):
    pass


def _topic_item(db: Session, t: GraduationTopic, with_counts: bool = True) -> dict:
    teacher = db.execute(
        select(SysUser.real_name, TeacherProfile.title).join(TeacherProfile, TeacherProfile.user_id == SysUser.id).where(TeacherProfile.id == t.teacher_id)
    ).first()
    major_name = None
    college_name = None
    if t.major_id:
        major = db.get(Major, t.major_id)
        if major:
            major_name = major.name
            college_name = db.execute(select(College.name).where(College.id == major.college_id)).scalar()
    approved_count = pending_count = 0
    if with_counts:
        approved_count = db.execute(
            select(func.count(TopicSelection.id)).where(TopicSelection.topic_id == t.id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
        ).scalar()
        pending_count = db.execute(
            select(func.count(TopicSelection.id)).where(TopicSelection.topic_id == t.id, TopicSelection.status == "PENDING")
        ).scalar()
    return {
        "id": t.id, "title": t.title, "description": t.description, "requirements": t.requirements,
        "topic_type": t.topic_type, "difficulty": t.difficulty, "major_id": t.major_id, "major_name": major_name,
        "college_name": college_name,
        "max_students": t.max_students, "status": t.status,
        "teacher_id": t.teacher_id,
        "teacher_name": teacher.real_name if teacher else None,
        "teacher_title": teacher.title if teacher else None,
        "approved_count": approved_count, "pending_count": pending_count,
        "publish_time": t.publish_time.isoformat() if t.publish_time else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ---------- 公共浏览(学生题目中心/管理员查看) ----------

@router.get("")
def list_topics(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                keyword: str | None = None, major_id: int | None = None, difficulty: str | None = None,
                user: CurrentUser = Depends(require_student),
                db: Session = Depends(get_db)):
    conds = [GraduationTopic.deleted_at.is_(None), GraduationTopic.status == "PUBLISHED"]
    if keyword:
        conds.append(or_(GraduationTopic.title.contains(keyword), GraduationTopic.description.contains(keyword)))
    if major_id:
        conds.append(GraduationTopic.major_id == major_id)
    if difficulty:
        conds.append(GraduationTopic.difficulty == difficulty)
    total = db.execute(select(func.count(GraduationTopic.id)).where(*conds)).scalar()
    rows = db.execute(select(GraduationTopic).where(*conds).order_by(GraduationTopic.publish_time.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_topic_item(db, t) for t in rows], total, page, page_size)


@router.get("/{topic_id}")
def get_topic(topic_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    t = db.get(GraduationTopic, topic_id)
    if t is None or t.deleted_at is not None or t.status != "PUBLISHED":
        raise ResourceNotFoundException("题目不存在或未发布")
    return ResponseUtil.success(_topic_item(db, t))


# ---------- 教师管理 ----------

teacher_router = APIRouter(prefix="/teacher/topics", tags=["Topic-Teacher"])


@teacher_router.get("")
def my_topics(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: str | None = None,
              user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    conds = [GraduationTopic.teacher_id == user.profile_id, GraduationTopic.deleted_at.is_(None)]
    if status:
        conds.append(GraduationTopic.status == status)
    total = db.execute(select(func.count(GraduationTopic.id)).where(*conds)).scalar()
    rows = db.execute(select(GraduationTopic).where(*conds).order_by(GraduationTopic.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_topic_item(db, t) for t in rows], total, page, page_size)


@teacher_router.post("")
def create_topic(body: TopicCreateRequest, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    t = GraduationTopic(teacher_id=user.profile_id, **body.model_dump())
    db.add(t)
    db.commit()
    return ResponseUtil.success({"id": t.id}, message="题目创建成功(草稿)")


@teacher_router.put("/{topic_id}")
def update_topic(topic_id: int, body: TopicUpdateRequest, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    t = db.get(GraduationTopic, topic_id)
    if t is None or t.deleted_at is not None:
        raise ResourceNotFoundException("题目不存在")
    PermissionService.ensure_teacher_owns_topic(db, user, topic_id)
    if t.status == "CLOSED":
        raise BusinessException("题目已关闭,不能修改")
    for field, value in body.model_dump().items():
        setattr(t, field, value)
    db.commit()
    return ResponseUtil.success(message="题目已更新")


@teacher_router.delete("/{topic_id}")
def delete_topic(topic_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    t = db.get(GraduationTopic, topic_id)
    if t is None or t.deleted_at is not None:
        raise ResourceNotFoundException("题目不存在")
    PermissionService.ensure_teacher_owns_topic(db, user, topic_id)
    approved = db.execute(
        select(func.count(TopicSelection.id)).where(TopicSelection.topic_id == topic_id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
    ).scalar()
    if approved:
        raise BusinessException("已有学生通过该题目选题,不能删除")
    t.deleted_at = datetime.now()
    db.commit()
    return ResponseUtil.success(message="题目已删除")


@teacher_router.post("/{topic_id}/publish")
def publish_topic(topic_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    t = db.get(GraduationTopic, topic_id)
    if t is None or t.deleted_at is not None:
        raise ResourceNotFoundException("题目不存在")
    PermissionService.ensure_teacher_owns_topic(db, user, topic_id)
    if t.status != "DRAFT":
        raise BusinessException("仅草稿状态可发布")
    t.status = "PUBLISHED"
    t.publish_time = datetime.now()
    db.commit()
    return ResponseUtil.success(message="题目已发布")


@teacher_router.post("/{topic_id}/close")
def close_topic(topic_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    t = db.get(GraduationTopic, topic_id)
    if t is None or t.deleted_at is not None:
        raise ResourceNotFoundException("题目不存在")
    PermissionService.ensure_teacher_owns_topic(db, user, topic_id)
    if t.status != "PUBLISHED":
        raise BusinessException("仅已发布状态可下架")
    t.status = "CLOSED"
    db.commit()
    return ResponseUtil.success(message="题目已下架")


# ---------- 管理员查看全部 ----------

admin_router = APIRouter(prefix="/admin/topics", tags=["Topic-Admin"])


class AdminTopicRequest(TopicCreateRequest):
    teacher_id: int


def _admin_topic_or_404(db: Session, topic_id: int) -> GraduationTopic:
    topic = db.get(GraduationTopic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise ResourceNotFoundException("题目不存在")
    return topic


def _validate_admin_topic_refs(db: Session, body: AdminTopicRequest):
    if db.get(TeacherProfile, body.teacher_id) is None:
        raise BusinessException("指导教师不存在")
    if body.major_id is not None and db.get(Major, body.major_id) is None:
        raise BusinessException("专业不存在")


@admin_router.get("/summary")
def admin_topic_summary(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    active = GraduationTopic.deleted_at.is_(None)
    total = db.execute(select(func.count(GraduationTopic.id)).where(active)).scalar_one()
    published = db.execute(select(func.count(GraduationTopic.id)).where(active, GraduationTopic.status == "PUBLISHED")).scalar_one()
    draft = db.execute(select(func.count(GraduationTopic.id)).where(active, GraduationTopic.status == "DRAFT")).scalar_one()
    teacher_count = db.execute(select(func.count(func.distinct(GraduationTopic.teacher_id))).where(active)).scalar_one()
    major_count = db.execute(select(func.count(func.distinct(GraduationTopic.major_id))).where(active)).scalar_one()
    return ResponseUtil.success({"total": total, "published": published, "draft": draft,
                                 "teacher_count": teacher_count, "major_count": major_count})


@admin_router.get("")
def admin_list_topics(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                      keyword: str | None = None, status: str | None = None, teacher_id: int | None = None,
                      major_id: int | None = None, college_id: int | None = None,
                      teacher_name: str | None = None, topic_type: str | None = None,
                      published_from: datetime | None = None, published_to: datetime | None = None,
                      _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = [GraduationTopic.deleted_at.is_(None)]
    if keyword:
        conds.append(or_(GraduationTopic.title.contains(keyword), GraduationTopic.description.contains(keyword)))
    if status:
        conds.append(GraduationTopic.status == status)
    if teacher_id:
        conds.append(GraduationTopic.teacher_id == teacher_id)
    if major_id:
        conds.append(GraduationTopic.major_id == major_id)
    if college_id:
        conds.append(GraduationTopic.major_id.in_(select(Major.id).where(Major.college_id == college_id)))
    if teacher_name:
        conds.append(GraduationTopic.teacher_id.in_(
            select(TeacherProfile.id).join(SysUser, SysUser.id == TeacherProfile.user_id)
            .where(SysUser.real_name.contains(teacher_name))))
    if topic_type:
        conds.append(GraduationTopic.topic_type == topic_type)
    if published_from:
        conds.append(GraduationTopic.publish_time >= published_from)
    if published_to:
        conds.append(GraduationTopic.publish_time < published_to)
    total = db.execute(select(func.count(GraduationTopic.id)).where(*conds)).scalar()
    rows = db.execute(select(GraduationTopic).where(*conds).order_by(GraduationTopic.id.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_topic_item(db, t) for t in rows], total, page, page_size)


@admin_router.post("")
def admin_create_topic(body: AdminTopicRequest, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    _validate_admin_topic_refs(db, body)
    topic = GraduationTopic(**body.model_dump())
    db.add(topic)
    db.flush()
    db.add(OperationLog(user_id=user.id, username=user.username, module="TOPIC", action="CREATE_TOPIC",
                        detail=f"创建题目#{topic.id}: {topic.title}"))
    db.commit()
    return ResponseUtil.success({"id": topic.id}, message="题目草稿已创建")


@admin_router.put("/{topic_id}")
def admin_update_topic(topic_id: int, body: AdminTopicRequest,
                       user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    topic = _admin_topic_or_404(db, topic_id)
    if topic.status == "CLOSED":
        raise BusinessException("已关闭题目不能修改")
    _validate_admin_topic_refs(db, body)
    approved = db.execute(select(func.count(TopicSelection.id)).where(
        TopicSelection.topic_id == topic_id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)).scalar_one()
    if approved and (body.teacher_id != topic.teacher_id or body.major_id != topic.major_id or body.max_students < approved):
        raise BusinessException("已有学生选题，不能更换教师、专业或缩小人数上限")
    for field, value in body.model_dump().items():
        setattr(topic, field, value)
    db.add(OperationLog(user_id=user.id, username=user.username, module="TOPIC", action="UPDATE_TOPIC",
                        detail=f"更新题目#{topic.id}: {topic.title}"))
    db.commit()
    return ResponseUtil.success(message="题目已更新")


@admin_router.post("/{topic_id}/publish")
def admin_publish_topic(topic_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    topic = _admin_topic_or_404(db, topic_id)
    if topic.status != "DRAFT":
        raise BusinessException("仅草稿可发布")
    topic.status = "PUBLISHED"
    topic.publish_time = datetime.now()
    db.add(OperationLog(user_id=user.id, username=user.username, module="TOPIC", action="PUBLISH_TOPIC",
                        detail=f"发布题目#{topic.id}: {topic.title}"))
    db.commit()
    return ResponseUtil.success(message="题目已发布")


@admin_router.delete("/{topic_id}")
def admin_delete_topic(topic_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    topic = _admin_topic_or_404(db, topic_id)
    selection_count = db.execute(select(func.count(TopicSelection.id)).where(
        TopicSelection.topic_id == topic_id,
        TopicSelection.status.in_(["PENDING", "APPROVED"]),
        TopicSelection.active_flag == 1)).scalar_one()
    if selection_count:
        raise BusinessException("题目已有学生申请或选中，不能删除")
    topic.deleted_at = datetime.now()
    db.add(OperationLog(user_id=user.id, username=user.username, module="TOPIC", action="DELETE_TOPIC",
                        detail=f"删除题目#{topic.id}: {topic.title}"))
    db.commit()
    return ResponseUtil.success(message="题目已删除")
