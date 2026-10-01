"""GraduationMaterial 模块: 毕业论文/设计/源码等多类型材料提交与审核.

论文(THESIS)提交后自动创建 AI 知识文档记录并触发 RAG 入库(roadmap 三十二章).
"""
import json
from datetime import date, datetime as dt, time, timedelta

from fastapi import APIRouter, Depends, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIDocument
from app.models.business import GraduationCycle, GraduationMaterial, GraduationMaterialFile
from app.models.enums import AIDocumentType, BusinessType, MaterialType, ProcessStage
from app.models.system import ReviewRecord, SysFile
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared.events import add_review_record, push_notification
from app.shared.progress import ProgressService
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


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


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
        m.status = "DRAFT"

    content, original_name, mime, size, sha256 = read_upload(file)
    stored_name, storage_path = storage.save(content, original_name)
    f = SysFile(original_name=original_name, stored_name=stored_name,
                extension=original_name.rsplit(".", 1)[-1].lower() if "." in original_name else None,
                mime_type=mime, size=size, storage_path=storage_path, sha256=sha256, uploader_id=user.id)
    db.add(f)
    db.flush()
    db.add(GraduationMaterialFile(material_id=m.id, file_id=f.id, material_type=material_type))

    # 论文文件 → 自动进入 AI 知识库待索引队列
    if material_type == MaterialType.THESIS.value:
        version = db.execute(
            select(func.count(GraduationMaterialFile.id)).where(GraduationMaterialFile.material_id == m.id,
                                                                GraduationMaterialFile.material_type == MaterialType.THESIS.value)
        ).scalar()
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

    before = m.status
    m.status = "SUBMITTED"
    m.submitted_at = dt.now()
    m.review_comment = None
    add_review_record(db, business_type=BusinessType.GRADUATION_MATERIAL.value, business_id=m.id,
                      student_id=m.student_id, action="SUBMIT", before_status=before, after_status="SUBMITTED")
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
def teacher_materials(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                      user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    conds = [GraduationMaterial.teacher_id == user.profile_id]
    if status:
        conds.append(GraduationMaterial.status == status)
    total = db.execute(select(func.count(GraduationMaterial.id)).where(*conds)).scalar()
    rows = db.execute(select(GraduationMaterial).where(*conds).order_by(GraduationMaterial.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_material_item(db, m) for m in rows], total, page, page_size)


@teacher_router.get("/{material_id}")
def teacher_material_detail(material_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    m = db.get(GraduationMaterial, material_id)
    if m is None or m.teacher_id != user.profile_id:
        raise ResourceNotFoundException("材料不存在")
    return ResponseUtil.success(_material_item(db, m))


def _review_material(db: Session, material_id: int, reviewer_user_id: int, reviewer_role: str,
                     teacher_profile_id: int | None, approve: bool, comment: str | None):
    m = db.get(GraduationMaterial, material_id)
    if m is None:
        raise ResourceNotFoundException("材料不存在")
    if teacher_profile_id is not None and m.teacher_id != teacher_profile_id:
        raise ResourceNotFoundException("材料不存在")
    if m.status != "SUBMITTED":
        raise BusinessException("该材料不在待审核状态")

    before = m.status
    m.status = "APPROVED" if approve else "REJECTED"
    m.review_comment = comment
    m.reviewed_at = dt.now()
    add_review_record(db, business_type=BusinessType.GRADUATION_MATERIAL.value, business_id=m.id,
                      student_id=m.student_id, reviewer_id=reviewer_user_id, reviewer_role=reviewer_role,
                      action="APPROVE" if approve else "REJECT", before_status=before, after_status=m.status, comment=comment)
    if approve:
        GraduationWorkflowService.on_stage_approved(db, m.student_id, ProcessStage.MATERIAL)
    ProgressService.recompute(db, m.student_id)
    student_user_id = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == m.student_id)).scalar()
    push_notification(db, user_id=student_user_id, type="REVIEW_RESULT",
                      title="毕业材料审核结果",
                      content=f"您的毕业材料已{'通过, 恭喜完成毕业设计!' if approve else '被驳回'}。意见: {comment or '无'}",
                      business_type=BusinessType.GRADUATION_MATERIAL.value, business_id=m.id)
    db.commit()
    return _material_item(db, m)


@teacher_router.post("/{material_id}/approve")
def approve_material(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                     db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_material(db, material_id, user.id, user.role, user.profile_id, True, body.comment))


@teacher_router.post("/{material_id}/reject")
def reject_material(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_teacher),
                    db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_material(db, material_id, user.id, user.role, user.profile_id, False, body.comment))


# ---------- 管理员 ----------

@admin_router.get("")
def admin_materials(status: str | None = None, academic_year: str | None = None,
                    college_id: int | None = None, major_id: int | None = None,
                    teacher_id: int | None = None, student: str | None = None,
                    material_type: str | None = None, from_date: date | None = None,
                    to_date: date | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
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
    total = db.execute(select(func.count(GraduationMaterial.id)).where(*conds)).scalar_one()
    rows = db.execute(select(GraduationMaterial).where(*conds).order_by(GraduationMaterial.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
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
    return ResponseUtil.success(_review_material(db, material_id, user.id, user.role, None, True, body.comment))


@admin_router.post("/{material_id}/reject")
def admin_reject(material_id: int, body: ReviewRequest, user: CurrentUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    return ResponseUtil.success(_review_material(db, material_id, user.id, user.role, None, False, body.comment))
