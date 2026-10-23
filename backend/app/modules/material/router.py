"""GraduationMaterial 模块: 毕业论文/设计/源码等多类型材料提交与审核.

论文(THESIS)提交后自动创建 AI 知识文档记录并触发 RAG 入库(roadmap 三十二章).
"""
import json
from datetime import date, datetime as dt, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import (
    GraduationCycle,
    GraduationMaterial,
    GraduationMaterialFile,
    GraduationTopic,
)
from app.models.enums import AIDocumentType, BusinessType, MaterialType, ProcessStage
from app.models.system import ReviewRecord, SysFile
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared import state_machine as sm
from app.shared.events import push_notification
from app.shared.progress import ProgressService
from app.shared.query import paginate, time_range_conditions
from app.shared.storage import read_upload, storage
from app.shared.workflow import GraduationWorkflowService

router = APIRouter(prefix="/student/material", tags=["Material-Student"])
teacher_router = APIRouter(prefix="/teacher/materials", tags=["Material-Teacher"])
admin_router = APIRouter(prefix="/admin/materials", tags=["Material-Admin"])

MATERIAL_TYPE_CN = {t.value: t.name for t in MaterialType}
STATUS_CN = {
    "NOT_SUBMITTED": "未提交", "DRAFT": "草稿", "SUBMITTED": "已提交待审核",
    "APPROVED": "已通过", "REJECTED": "已驳回", "RESUBMIT_REQUIRED": "需重新提交",
}


REVIEW_DECISION = Literal["approve", "reject", "request_revision"]


class ReviewRequest(BaseModel):
    """审核请求体.

    decision 显式表达审核结论(与 stages 模块 P1-A 对齐), 不再靠"comment 是否为空"推断;
    缺省时按端点语义取值(approve 端点=approve, reject 端点=reject), 保持旧调用兼容。
    MATERIAL 表块三个动作齐全, request_revision 直接落到 state_machine。
    """

    comment: str | None = Field(default=None, max_length=500)
    decision: REVIEW_DECISION | None = Field(
        default=None, description="审核结论: approve/reject/request_revision")


def _get_or_create(db: Session, student_id: int) -> GraduationMaterial:
    from app.shared.cycle import get_active_cycle

    cycle = get_active_cycle(db, student_id)
    m = db.execute(
        select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle.id)
    ).scalar_one_or_none() if cycle else None
    if m is None:
        sel = GraduationWorkflowService.require_selection_approved(db, student_id)
        m = GraduationMaterial(student_id=student_id, cycle_id=cycle.id,
                               topic_id=sel.topic_id, teacher_id=sel.teacher_id)
        db.add(m)
        db.flush()
    return m


def _purge_thesis_documents(db: Session, student_id: int, *, file_id: int | None = None) -> list[int]:
    """作废该学生旧版论文的 AI 知识文档(D40).

    论文每次重传都新建 AIDocument; 若不作废旧文档, RAG 会同时召回新旧两版。
    AIDocument 没有 SUPERSEDED 状态(enums.IndexStatus = WAITING/PROCESSING/SUCCESS/FAILED),
    因此采用删除语义: 旧 AIDocument + 其 AIDocumentChunk 及 Redis 向量一并清理。
    """
    from app.ai.vector.store import vector_store

    conds = [
        AIDocument.student_id == student_id,
        AIDocument.document_type == AIDocumentType.THESIS.value,
        AIDocument.scope == "STUDENT",
    ]
    if file_id is not None:
        conds.append(AIDocument.file_id == file_id)
    docs = db.execute(select(AIDocument).where(*conds)).scalars().all()
    removed: list[int] = []
    for doc in docs:
        chunk_ids = list(db.execute(
            select(AIDocumentChunk.id).where(AIDocumentChunk.document_id == doc.id)
        ).scalars().all())
        if chunk_ids:
            db.execute(delete(AIDocumentChunk).where(AIDocumentChunk.document_id == doc.id))
            vector_store.delete_chunks(chunk_ids)  # 清理 Redis 向量, 残留由 student 过滤兜底(D44)
            removed.extend(chunk_ids)
        db.delete(doc)
    if docs:
        db.flush()
    return removed


def _next_thesis_version(db: Session, student_id: int) -> int:
    """版本号单调递增(即便旧文档已删除), 便于审计追溯第几版论文."""
    latest = db.execute(
        select(func.max(AIDocument.version)).where(
            AIDocument.student_id == student_id,
            AIDocument.document_type == AIDocumentType.THESIS.value,
        )
    ).scalar()
    return int(latest or 0) + 1


def _material_item(db: Session, m: GraduationMaterial) -> dict:
    files = db.execute(
        select(GraduationMaterialFile, SysFile).join(SysFile, SysFile.id == GraduationMaterialFile.file_id)
        .where(GraduationMaterialFile.material_id == m.id)
    ).all()
    stu = db.execute(
        select(StudentProfile, SysUser).join(SysUser, SysUser.id == StudentProfile.user_id).where(StudentProfile.id == m.student_id)
    ).first()
    teacher = db.get(TeacherProfile, m.teacher_id) if m.teacher_id else None
    teacher_user = db.get(SysUser, teacher.user_id) if teacher else None
    cycle = db.get(GraduationCycle, m.cycle_id) if m.cycle_id else None
    college = db.get(College, stu[0].college_id) if stu and stu[0].college_id else None
    major = db.get(Major, stu[0].major_id) if stu and stu[0].major_id else None
    return {
        "id": m.id, "student_id": m.student_id,
        "student_name": stu[1].real_name if stu else None,
        "student_no": stu[0].student_no if stu else None,
        "college_name": college.name if college else None,
        "major_name": major.name if major else None,
        "academic_year": cycle.academic_year if cycle else None,
        "teacher_id": m.teacher_id, "status": m.status,
        "teacher_name": teacher_user.real_name if teacher_user else None,
        "status_cn": STATUS_CN.get(m.status, m.status),
        "review_comment": m.review_comment,
        "submitted_at": m.submitted_at.isoformat() if m.submitted_at else None,
        "reviewed_at": m.reviewed_at.isoformat() if m.reviewed_at else None,
        "can_edit": m.status in ("NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"),
        "files": [{
            "id": mf.id, "file_id": f.id, "material_type": mf.material_type,
            "material_type_cn": {"THESIS": "毕业论文", "DESIGN": "毕业设计", "SOURCE_CODE": "源代码",
                                 "MANUAL": "说明文档", "VIDEO": "演示视频", "OTHER": "其他"}.get(mf.material_type, mf.material_type),
            "original_name": f.original_name, "size": f.size, "extension": f.extension,
            "url": f"/api/v1/files/{f.id}/download",
        } for mf, f in files],
    }


# ---------- 列表过滤(§9/D10: 全量下推 SQL, 先过滤后分页) ----------

# keyword 模糊匹配三条路径: 学生姓名(real_name) / 学号(student_no) / 题目名(topic.title).
# 关联路径: graduation_material.student_id → student_profile.id → sys_user.id,
#           graduation_material.topic_id   → graduation_topic.id(最短路径, 不经 cycle).
KEYWORD_FIELDS = (SysUser.real_name, StudentProfile.student_no, GraduationTopic.title)


def _list_stmt():
    """教师/管理员列表基础语句: 学生姓名/学号/题目名既要展示也要能关键字过滤(§9)."""
    return (
        select(GraduationMaterial)
        .outerjoin(StudentProfile, StudentProfile.id == GraduationMaterial.student_id)
        .outerjoin(SysUser, SysUser.id == StudentProfile.user_id)
        .outerjoin(GraduationTopic, GraduationTopic.id == GraduationMaterial.topic_id)
        .order_by(GraduationMaterial.id.desc())
    )


# ---------- 学生 ----------

@router.get("")
def my_material(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    from app.shared.cycle import get_active_cycle

    cycle = get_active_cycle(db, user.profile_id)
    m = db.execute(
        select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle.id)
    ).scalar_one_or_none() if cycle else None
    if m is None:
        return ResponseUtil.success({"status": "NOT_SUBMITTED", "status_cn": "未提交", "can_edit": True, "files": []})
    item = _material_item(db, m)
    records = db.execute(
        select(ReviewRecord).where(ReviewRecord.business_type == BusinessType.GRADUATION_MATERIAL.value,
                                   ReviewRecord.business_id == m.id).order_by(ReviewRecord.id)
    ).scalars().all()
    item["review_history"] = [{
        "action": r.action, "comment": r.comment,
        "before_status": r.before_status, "after_status": r.after_status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in records]
    return ResponseUtil.success(item)


@router.post("/files")
def upload_material_file(file: UploadFile, material_type: str = Query(..., description="THESIS/DESIGN/SOURCE_CODE/MANUAL/VIDEO/OTHER"),
                         user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    try:
        MaterialType(material_type)
    except ValueError:
        raise BusinessException("材料类型错误")
    GraduationWorkflowService.require_can_submit_material(db, user.profile_id)
    m = _get_or_create(db, user.profile_id)
    if m.status not in ("NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"):
        raise BusinessException("当前状态不允许修改材料")
    if m.status == "NOT_SUBMITTED":
        # 首次落草稿走 MATERIAL 转移表(save: {NOT_SUBMITTED,DRAFT,REJECTED,RESUBMIT_REQUIRED}→DRAFT)
        sm.transition(db, sm.MATERIAL, m, "save", user)

    content, original_name, mime, size, sha256 = read_upload(file)
    stored_name, storage_path = storage.save(content, original_name)
    f = SysFile(original_name=original_name, stored_name=stored_name,
                extension=original_name.rsplit(".", 1)[-1].lower() if "." in original_name else None,
                mime_type=mime, size=size, storage_path=storage_path, sha256=sha256, uploader_id=user.id)
    db.add(f)
    db.flush()
    db.add(GraduationMaterialFile(material_id=m.id, file_id=f.id, material_type=material_type))

    # 论文文件 → 自动进入 AI 知识库待索引队列; 重传时先作废旧版, 保证 RAG 只命中最新一版(D40)
    if material_type == MaterialType.THESIS.value:
        version = _next_thesis_version(db, m.student_id)
        _purge_thesis_documents(db, m.student_id)
        db.add(AIDocument(
            student_id=m.student_id, topic_id=m.topic_id, document_type=AIDocumentType.THESIS.value,
            document_name=original_name, file_id=f.id, scope="STUDENT",
            index_status="WAITING", version=version,
        ))
    db.commit()
    return ResponseUtil.success(_material_item(db, m))


@router.delete("/files/{mf_id}")
def delete_material_file(mf_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    mf = db.get(GraduationMaterialFile, mf_id)
    if mf is None:
        raise ResourceNotFoundException("材料文件不存在")
    m = db.get(GraduationMaterial, mf.material_id)
    if m is None or m.student_id != user.profile_id:
        raise PermissionDeniedException("无权操作该材料")
    if m.status not in ("NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"):
        raise BusinessException("当前状态不允许修改材料")
    db.delete(mf)
    if mf.material_type == MaterialType.THESIS.value:  # 删除论文文件同步作废其 AI 文档(D40)
        _purge_thesis_documents(db, m.student_id, file_id=mf.file_id)
    db.commit()
    return ResponseUtil.success(_material_item(db, m))


@router.post("/submit")
def submit_material(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    GraduationWorkflowService.require_can_submit_material(db, user.profile_id)
    m = _get_or_create(db, user.profile_id)
    if m.status not in ("NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"):
        raise BusinessException("当前状态不允许提交")
    has_thesis = db.execute(
        select(func.count(GraduationMaterialFile.id)).where(
            GraduationMaterialFile.material_id == m.id, GraduationMaterialFile.material_type == MaterialType.THESIS.value)
    ).scalar()
    if not has_thesis:
        raise BusinessException("请至少上传一份毕业论文(THESIS)后再提交")

    sm.transition(db, sm.MATERIAL, m, "submit", user)
    from app.models.user import TeacherProfile

    teacher_user_id = db.execute(select(TeacherProfile.user_id).where(TeacherProfile.id == m.teacher_id)).scalar()
    student_name = db.execute(select(SysUser.real_name).join(StudentProfile, StudentProfile.user_id == SysUser.id).where(StudentProfile.id == m.student_id)).scalar()
    push_notification(db, user_id=teacher_user_id, type="REVIEW_RESULT",
                      title=f"毕业材料待审核: {student_name}",
                      content=f"学生 {student_name} 提交了毕业材料, 请及时审核",
                      business_type=BusinessType.GRADUATION_MATERIAL.value, business_id=m.id)
    db.commit()
    return ResponseUtil.success(_material_item(db, m))


# ---------- 教师 ----------

@teacher_router.get("")
def teacher_materials(status: str | None = None, keyword: str | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                      user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    """教师材料审核列表.

    - keyword: 模糊匹配 学生姓名 / 学号 / 题目名(OR, 见 KEYWORD_FIELDS);
    - date_from / date_to: 按**提交时间** `graduation_material.submitted_at` 过滤
      (纯日期含当日整日, 带时刻按精确时刻比较; 非法日期 → 40002).
      该列可空: 一旦给了日期边界, 尚未提交(submitted_at 为 NULL)的草稿不会命中;
    - 归属约束始终生效: 只返回 teacher_id == 当前教师 的记录, 关键字/日期不会绕过它;
    - 过滤与分页全部在 SQL 内完成, total 是过滤后的总数(先过滤后分页).
    """
    filters = [GraduationMaterial.teacher_id == user.profile_id]
    if status:
        filters.append(GraduationMaterial.status == status)
    filters.extend(time_range_conditions(GraduationMaterial.submitted_at, date_from, date_to))
    rows, total = paginate(db, _list_stmt(), page=page, size=page_size,
                           keyword=keyword, keyword_fields=KEYWORD_FIELDS, filters=filters)
    return ResponseUtil.page([_material_item(db, m) for m in rows], total, page, page_size)


@teacher_router.get("/{material_id}")
def teacher_material_detail(material_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    m = db.get(GraduationMaterial, material_id)
    if m is None or m.teacher_id != user.profile_id:
        raise ResourceNotFoundException("材料不存在")
    return ResponseUtil.success(_material_item(db, m))


def _review_material(db: Session, material_id: int, user: CurrentUser,
                     decision: str, comment: str | None):
    """审核核心: 三值显式结论 → state_machine MATERIAL 动作.

    approve / reject / request_revision 与转移表一一对应; 不再用"comment 是否为空"推断。
    """
    if decision not in ("approve", "reject", "request_revision"):
        raise BusinessException("审核结论非法")
    m = db.get(GraduationMaterial, material_id)
    if m is None:
        raise ResourceNotFoundException("材料不存在")
    if user.role != "ADMIN" and m.teacher_id != user.profile_id:
        raise ResourceNotFoundException("材料不存在")
    if m.status != "SUBMITTED":
        raise BusinessException("该材料不在待审核状态")

    # MATERIAL 表块与模型状态 1:1, 直接走唯一入口; 留痕角色取 actor.role(D36)
    sm.transition(db, sm.MATERIAL, m, decision, user, comment=comment)
    if decision == "approve":
        # 传本记录自己的 cycle_id: 跨学年审核历史材料不得改写当前学年流程实例(D30)
        GraduationWorkflowService.on_stage_approved(db, m.student_id, ProcessStage.MATERIAL, cycle_id=m.cycle_id)
    ProgressService.recompute(db, m.student_id, cycle_id=m.cycle_id)
    student_user_id = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == m.student_id)).scalar()
    result_cn = {"approve": "通过, 恭喜完成毕业设计!", "reject": "被驳回",
                 "request_revision": "被要求修改后重新提交"}[decision]
    push_notification(db, user_id=student_user_id, type="REVIEW_RESULT",
                      title="毕业材料审核结果",
                      content=f"您的毕业材料已{result_cn}。意见: {comment or '无'}",
                      business_type=BusinessType.GRADUATION_MATERIAL.value, business_id=m.id)
    db.commit()
    return _material_item(db, m)


@teacher_router.post("/{material_id}/approve")
def approve_material(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                     db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "approve"
    return ResponseUtil.success(_review_material(db, material_id, user, decision, body.comment))


@teacher_router.post("/{material_id}/reject")
def reject_material(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                    db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "reject"
    return ResponseUtil.success(_review_material(db, material_id, user, decision, body.comment))


# ---------- 管理员 ----------

@admin_router.get("")
def admin_materials(status: str | None = None, academic_year: str | None = None,
                    college_id: int | None = None, major_id: int | None = None,
                    teacher_id: int | None = None, student: str | None = None,
                    material_type: str | None = None, from_date: date | None = None,
                    to_date: date | None = None,
                    keyword: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """管理员全局材料列表(与 teacher_materials 同口径扩展, 无归属约束).

    - keyword: 模糊匹配 学生姓名 / 学号 / 题目名(OR, 见 KEYWORD_FIELDS);
    - date_from / date_to: 按**提交时间** `graduation_material.submitted_at` 过滤(ISO 字符串);
      与既有的 from_date/to_date(date 类型, 只按整日)并存, 后者仅为旧调用兼容;
    - 过滤与分页全部在 SQL 内完成, total 是过滤后的总数(先过滤后分页).
    """
    conds = []
    if status:
        conds.append(GraduationMaterial.status.in_(status.split(",")))
    if academic_year:
        conds.append(GraduationMaterial.cycle_id.in_(select(GraduationCycle.id).where(
            GraduationCycle.academic_year == academic_year)))
    if college_id or major_id or student:
        student_query = select(StudentProfile.id).join(SysUser, SysUser.id == StudentProfile.user_id)
        if college_id:
            student_query = student_query.where(StudentProfile.college_id == college_id)
        if major_id:
            student_query = student_query.where(StudentProfile.major_id == major_id)
        if student:
            student_query = student_query.where(or_(SysUser.real_name.contains(student),
                                                   StudentProfile.student_no.contains(student)))
        conds.append(GraduationMaterial.student_id.in_(student_query))
    if teacher_id:
        conds.append(GraduationMaterial.teacher_id == teacher_id)
    if material_type:
        conds.append(GraduationMaterial.id.in_(select(GraduationMaterialFile.material_id).where(
            GraduationMaterialFile.material_type == material_type)))
    if from_date:
        conds.append(GraduationMaterial.submitted_at >= dt.combine(from_date, time.min))
    if to_date:
        conds.append(GraduationMaterial.submitted_at < dt.combine(to_date + timedelta(days=1), time.min))
    conds.extend(time_range_conditions(GraduationMaterial.submitted_at, date_from, date_to))
    rows, total = paginate(db, _list_stmt(), page=page, size=page_size,
                           keyword=keyword, keyword_fields=KEYWORD_FIELDS, filters=conds)
    return ResponseUtil.page([_material_item(db, m) for m in rows], total, page, page_size)


@admin_router.get("/stats")
def admin_material_stats(academic_year: str | None = None,
                         _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    base = [GraduationMaterial.cycle_id.in_(select(GraduationCycle.id).where(
        GraduationCycle.academic_year == academic_year))] if academic_year else []
    counts = {status: db.execute(select(func.count(GraduationMaterial.id)).where(
        *base, GraduationMaterial.status == status)).scalar_one()
              for status in ("APPROVED", "SUBMITTED", "REJECTED", "RESUBMIT_REQUIRED")}
    drafts = db.execute(select(func.count(GraduationMaterial.id)).where(
        *base, GraduationMaterial.status.in_(["NOT_SUBMITTED", "DRAFT"]))).scalar_one()
    submitted_total = sum(counts.values())
    return ResponseUtil.success({"total": submitted_total, "approved": counts["APPROVED"],
                                 "pending": counts["SUBMITTED"],
                                 "rejected": counts["REJECTED"] + counts["RESUBMIT_REQUIRED"],
                                 "not_submitted": drafts})


@admin_router.get("/filters")
def admin_material_filters(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    years = db.execute(select(GraduationCycle.academic_year).distinct().order_by(
        GraduationCycle.academic_year.desc())).scalars().all()
    colleges = db.execute(select(College.id, College.name).order_by(College.name)).all()
    majors = db.execute(select(Major.id, Major.name, Major.college_id).order_by(Major.name)).all()
    teachers = db.execute(select(TeacherProfile.id, SysUser.real_name).join(
        SysUser, SysUser.id == TeacherProfile.user_id).order_by(SysUser.real_name)).all()
    return ResponseUtil.success({"years": years,
                                 "colleges": [{"id": row.id, "name": row.name} for row in colleges],
                                 "majors": [{"id": row.id, "name": row.name, "college_id": row.college_id}
                                            for row in majors],
                                 "teachers": [{"id": row.id, "name": row.real_name} for row in teachers]})


@admin_router.post("/{material_id}/approve")
def admin_approve(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "approve"
    return ResponseUtil.success(_review_material(db, material_id, user, decision, body.comment))


@admin_router.post("/{material_id}/reject")
def admin_reject(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    decision = "request_revision" if body.decision == "request_revision" else "reject"
    return ResponseUtil.success(_review_material(db, material_id, user, decision, body.comment))
