"""Authoritative evidence for a student's current defense practice."""
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import FinalDefense, GraduationMaterial, GraduationMaterialFile, MidtermReport, OpeningReport
from app.models.system import SysFile
from app.models.user import StudentProfile
from app.shared.cycle import get_active_cycle

FILE_TYPES = ("THESIS", "SOURCE_CODE", "DESIGN", "MANUAL")
REPORTS = {
    "OPENING_REPORT": (OpeningReport, ("title", "research_background", "research_significance",
                                       "research_content", "research_method", "technical_route", "schedule_plan")),
    "MIDTERM_REPORT": (MidtermReport, ("progress_description", "completed_content", "remaining_content",
                                       "existing_problems", "next_plan")),
    "FINAL_REPORT": (FinalDefense, ("summary", "innovation", "technical_solution", "test_result")),
}


_FIELD_LABELS = {
    "title": "标题", "research_background": "研究背景", "research_significance": "研究意义",
    "research_content": "研究内容", "research_method": "研究方法", "technical_route": "技术路线",
    "schedule_plan": "进度计划", "progress_description": "进展说明", "completed_content": "已完成内容",
    "remaining_content": "待完成内容", "existing_problems": "存在问题", "next_plan": "下一步计划",
    "summary": "工作总结", "innovation": "创新点", "technical_solution": "技术方案", "test_result": "测试结果",
}


def report_text(report, fields) -> str:
    text = "\n\n".join(f"## {_FIELD_LABELS[field]}\n{getattr(report, field)}" for field in fields if getattr(report, field, None))
    return text.encode("utf-8")[:65000].decode("utf-8", errors="ignore")


def material_files(db: Session, cycle) -> list:
    """Only the latest thesis is authoritative; other attachments are individually selectable."""
    rows = db.execute(
        select(GraduationMaterialFile.material_type, SysFile.id, SysFile.original_name)
        .join(SysFile, SysFile.id == GraduationMaterialFile.file_id)
        .join(GraduationMaterial, GraduationMaterial.id == GraduationMaterialFile.material_id)
        .where(GraduationMaterial.student_id == cycle.student_id, GraduationMaterial.cycle_id == cycle.id,
               GraduationMaterial.topic_id == cycle.topic_id, SysFile.deleted_at.is_(None),
               GraduationMaterialFile.material_type.in_(FILE_TYPES))
        .order_by(GraduationMaterialFile.id.desc())
    ).all()
    result, seen_thesis = [], False
    for row in rows:
        if row.material_type == "THESIS":
            if seen_thesis:
                continue
            seen_thesis = True
        result.append(row)
    return result


def practice_upload_filter(owner_user_id, cycle_id):
    """Used in both the document list and retrieval; a formal attachment cannot masquerade as a practice upload."""
    attached = select(GraduationMaterialFile.id).where(GraduationMaterialFile.file_id == SysFile.id).exists()
    return ((SysFile.business_type == "AI_PRACTICE") & (SysFile.uploader_id == owner_user_id)
            & (SysFile.business_id == cycle_id) & SysFile.deleted_at.is_(None) & ~attached)


def practice_documents(db: Session, student_id: int) -> list[AIDocument]:
    cycle = get_active_cycle(db, student_id)
    if cycle is None:
        return []
    files = {(row.id, row.material_type) for row in material_files(db, cycle)}
    owner = select(StudentProfile.user_id).where(StudentProfile.id == student_id).scalar_subquery()
    practice_files = set(db.scalars(select(SysFile.id).where(practice_upload_filter(owner, cycle.id))))
    docs = db.scalars(select(AIDocument).options(defer(AIDocument.content_text))
                      .where(AIDocument.student_id == student_id, AIDocument.scope == "STUDENT",
                             AIDocument.cycle_id == cycle.id, AIDocument.topic_id == cycle.topic_id,
                             AIDocument.index_status == "SUCCESS", AIDocument.chunk_count > 0,
                             select(AIDocumentChunk.id).where(
                                 AIDocumentChunk.document_id == AIDocument.id,
                                 AIDocumentChunk.student_id == student_id, AIDocumentChunk.scope == "STUDENT").exists())
                      .order_by(AIDocument.id)).all()
    valid = []
    for doc in docs:
        if doc.file_id:
            uploaded_for_practice = doc.document_type in ("PRACTICE_REPORT", "SOURCE_CODE") and doc.file_id in practice_files
            if uploaded_for_practice or (doc.file_id, doc.document_type) in files:
                valid.append(doc)
        elif doc.document_type in REPORTS:
            model, fields = REPORTS[doc.document_type]
            report = db.scalar(select(model).where(model.student_id == student_id, model.cycle_id == cycle.id,
                                                    model.topic_id == cycle.topic_id, model.status == "APPROVED"))
            if report is not None and doc.content_text == report_text(report, fields):
                valid.append(doc)
    return valid
