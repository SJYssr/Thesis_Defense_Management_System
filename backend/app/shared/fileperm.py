"""归档文件访问控制(P3 验收: 未授权用户无法经 API 或文件 URL 下载私有论文).

sys_file 下载接口统一调用 ensure_file_access; 归档文件另有独立授权链路。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.models.ai import AIDocument
from app.models.business import GraduationMaterialFile
from app.models.defense import ArchiveConsent, DefenseAssignment, DefenseExaminer
from app.models.enums import ConsentVisibility
from app.models.system import SysFile
from app.models.user import StudentProfile


def _student_profile_id(db: Session, user) -> int | None:
    if user.role != "STUDENT":
        return None
    return user.profile_id


def ensure_file_access(db: Session, user, file: SysFile) -> None:
    """对象级文件权限: 上传人/管理员/文件属主学生/指导教师/被指派评委/全文明授权."""
    if user.role == "ADMIN":
        return
    if file.uploader_id == user.id:
        return

    # 文件归属学生(材料文件 / AI 知识文档)
    owner_student_id: int | None = None

    mf = db.execute(
        select(GraduationMaterialFile).where(GraduationMaterialFile.file_id == file.id)
    ).scalars().first()
    if mf is not None:
        from app.models.business import GraduationMaterial

        material = db.get(GraduationMaterial, mf.material_id)
        if material is not None:
            owner_student_id = material.student_id
            if user.role == "STUDENT" and user.profile_id == owner_student_id:
                return
            if user.role == "TEACHER" and user.profile_id == material.teacher_id:
                return  # 指导教师

    doc = db.execute(
        select(AIDocument).where(AIDocument.file_id == file.id)
    ).scalars().first()
    if doc is not None:
        if user.role == "STUDENT" and user.profile_id == doc.student_id:
            return
        owner_student_id = owner_student_id or doc.student_id

    # 归档文件: 走归档授权(全文明授权可见)
    from app.models.defense import ArchiveFile

    af = db.execute(select(ArchiveFile).where(ArchiveFile.file_id == file.id)).scalars().first()
    if af is not None:
        from app.models.defense import ArchivePackage

        pkg = db.get(ArchivePackage, af.package_id)
        if pkg is not None:
            consent = db.execute(
                select(ArchiveConsent).where(
                    ArchiveConsent.package_id == pkg.id,
                    ArchiveConsent.revoked_at.is_(None),
                ).order_by(ArchiveConsent.id.desc())
            ).scalars().first()
            if consent is not None and consent.visibility == ConsentVisibility.FULL_TEXT.value:
                return
            if user.role == "STUDENT":
                from app.models.business import GraduationCycle

                cycle = db.get(GraduationCycle, pkg.cycle_id)
                if cycle is not None and cycle.student_id == user.profile_id:
                    return
            if user.role == "TEACHER":
                from app.models.business import GraduationCycle

                cycle = db.get(GraduationCycle, pkg.cycle_id)
                if cycle is not None and cycle.teacher_id == user.profile_id:
                    return  # 导师
                # 评委: 被指派场次包含该学生席位
                if owner_student_id is None and cycle is not None:
                    owner_student_id = cycle.student_id

    # 教师评委: 被指派场次包含该学生的席位
    if user.role == "TEACHER" and owner_student_id is not None:
        from app.models.business import GraduationCycle

        linked = db.execute(
            select(DefenseExaminer.id)
            .join(DefenseAssignment, DefenseAssignment.session_id == DefenseExaminer.session_id)
            .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
            .where(DefenseExaminer.teacher_id == user.profile_id, GraduationCycle.student_id == owner_student_id)
        ).scalars().first()
        if linked is not None:
            return

    raise PermissionDeniedException("无权下载该文件")
