"""毕业成果归档(roadmap P3): 归档包/授权/检索/下载审计/撤回公开.

归档前置校验(全部服务端强制):
1. 该流程正式结论(defense_result, FINAL)已发布且为 PASS
2. 毕业材料审核通过
3. 归档文件来自 sys_file 且记录哈希快照
公开范围默认私有; 学生授权后才可展示元数据或全文; 撤回立即失效。
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin, require_student
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import GraduationCycle, GraduationMaterial, GraduationTopic
from app.models.defense import (
    ArchiveConsent,
    ArchiveDownloadLog,
    ArchiveFile,
    ArchivePackage,
    DefenseAssignment,
    DefenseResult,
    DefenseSession,
)
from app.models.enums import ArchiveStatus, ConsentVisibility, ResultStatus
from app.models.system import OperationLog, SysFile
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared.storage import storage

router = APIRouter(tags=["Archive"])


def _active_consent(db: Session, package_id: int) -> ArchiveConsent | None:
    return db.execute(
        select(ArchiveConsent).where(
            ArchiveConsent.package_id == package_id, ArchiveConsent.revoked_at.is_(None)
        ).order_by(ArchiveConsent.id.desc())
    ).scalars().first()


def _package_item(db: Session, pkg: ArchivePackage, viewer: CurrentUser | None = None) -> dict:
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    stu = db.execute(
        select(SysUser, StudentProfile).join(StudentProfile, StudentProfile.user_id == SysUser.id)
        .where(StudentProfile.id == cycle.student_id)
    ).first()
    topic_title = db.execute(select(GraduationTopic.title).where(GraduationTopic.id == cycle.topic_id)).scalar() if cycle.topic_id else None
    teacher_name = db.execute(
        select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
        .where(TeacherProfile.id == cycle.teacher_id)
    ).scalar() if cycle.teacher_id else None
    college_name = None
    major_name = None
    if stu is not None:
        sp = stu[1]
        if sp.college_id:
            college_name = db.execute(select(College.name).where(College.id == sp.college_id)).scalar()
        if sp.major_id:
            major_name = db.execute(select(Major.name).where(Major.id == sp.major_id)).scalar()

    consent = _active_consent(db, pkg.id)
    files = db.execute(select(func.count(ArchiveFile.id)).where(ArchiveFile.package_id == pkg.id)).scalar()
    result = db.get(DefenseResult, pkg.result_id) if pkg.result_id else None

    is_owner = bool(viewer and viewer.role == "STUDENT" and viewer.profile_id == cycle.student_id)
    is_supervisor = bool(viewer and viewer.role == "TEACHER" and viewer.profile_id == cycle.teacher_id)
    full_public = bool(consent and consent.visibility == ConsentVisibility.FULL_TEXT.value)
    meta_public = bool(consent and consent.visibility in (ConsentVisibility.METADATA.value, ConsentVisibility.FULL_TEXT.value))

    item = {
        "id": pkg.id, "cycle_id": pkg.cycle_id, "academic_year": cycle.academic_year,
        "status": pkg.status, "status_cn": {"PENDING": "待归档", "ARCHIVED": "已归档", "REVOKED": "已撤回"}.get(pkg.status, pkg.status),
        "archived_at": pkg.archived_at.isoformat() if pkg.archived_at else None,
        "student_name": stu[0].real_name if stu else None,
        "student_no": stu[1].student_no if stu else None,
        "college": college_name, "major": major_name,
        "teacher_name": teacher_name,
        "topic_title": topic_title,
        "file_count": files,
        "result_score": result.score if result else None,
        "result_decision": result.decision if result else None,
        "visibility": consent.visibility if consent else "PRIVATE",
        "visibility_cn": {"METADATA": "公开元数据", "FULL_TEXT": "公开全文", "PRIVATE": "私有"}.get(
            consent.visibility if consent else "PRIVATE"),
    }
    # 摘要/关键词仅对有权者或公开元数据可见
    if meta_public or is_owner or is_supervisor or (viewer and viewer.role == "ADMIN"):
        item["abstract"] = pkg.abstract
        item["keywords"] = pkg.keywords
    return item


class ArchiveCreate(BaseModel):
    cycle_id: int
    title: str | None = Field(default=None, max_length=255)
    abstract: str | None = None
    keywords: str | None = Field(default=None, max_length=500)
    files: list[dict] = Field(min_length=1, description='[{"file_id":1,"file_role":"THESIS"}]')


def _validate_archive_eligible(db: Session, cycle_id: int) -> tuple[GraduationCycle, DefenseResult]:
    cycle = db.get(GraduationCycle, cycle_id)
    if cycle is None:
        raise ResourceNotFoundException("流程实例不存在")
    # 1. 正式结论已发布且通过
    row = db.execute(
        select(DefenseResult)
        .join(DefenseAssignment, DefenseAssignment.id == DefenseResult.assignment_id)
        .where(DefenseResult.cycle_id == cycle_id, DefenseResult.stage == "FINAL")
        .order_by(DefenseResult.id.desc())
    ).scalars().first()
    if row is None or row.status != ResultStatus.PUBLISHED.value:
        raise BusinessException("归档前置条件不满足: 该流程尚无已发布的正式终期答辩结论")
    if row.decision != "PASS":
        raise BusinessException("归档前置条件不满足: 正式答辩结论未通过")
    # 2. 毕业材料审核通过
    material = db.execute(
        select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle_id)
    ).scalar_one_or_none()
    if material is None or material.status != "APPROVED":
        raise BusinessException("归档前置条件不满足: 毕业材料尚未审核通过")
    return cycle, row


@router.post("/archive-packages")
def create_archive(body: ArchiveCreate, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    cycle, result = _validate_archive_eligible(db, body.cycle_id)
    exists = db.execute(select(ArchivePackage).where(ArchivePackage.cycle_id == body.cycle_id)).scalar_one_or_none()
    if exists is not None and exists.status == ArchiveStatus.ARCHIVED.value:
        raise BusinessException("该流程已存在有效归档包")

    pkg = ArchivePackage(
        cycle_id=body.cycle_id, result_id=result.id, title=body.title, abstract=body.abstract,
        keywords=body.keywords, status=ArchiveStatus.ARCHIVED.value, archived_at=datetime.now(), created_by=user.id,
    )
    db.add(pkg)
    db.flush()
    for f in body.files:
        file_id = f.get("file_id")
        role = f.get("file_role") or "OTHER"
        sf = db.get(SysFile, file_id)
        if sf is None or sf.deleted_at is not None:
            raise ResourceNotFoundException(f"文件#{file_id}不存在")
        db.add(ArchiveFile(package_id=pkg.id, file_id=file_id, file_role=role,
                           version=1, sha256=sf.sha256))
    db.add(OperationLog(user_id=user.id, module="ARCHIVE", action="CREATE_PACKAGE",
                        detail=f"归档流程#{body.cycle_id}, 文件数{len(body.files)}"))
    db.commit()
    return ResponseUtil.success(_package_item(db, pkg), message="归档完成")


@router.get("/archive-packages")
def search_packages(academic_year: str | None = None, college_id: int | None = None, major_id: int | None = None,
                    teacher_id: int | None = None, keyword: str | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """检索归档: 先按身份过滤可见范围, 再按条件筛选(权限过滤先于返回, docs §1.2/§5)."""
    base = select(ArchivePackage, GraduationCycle).join(GraduationCycle, GraduationCycle.id == ArchivePackage.cycle_id)
    conds = []
    if user.role == "ADMIN":
        pass
    elif user.role == "STUDENT":
        # 本人全部 + 已公开(任意明)他人
        public_ids = select(ArchiveConsent.package_id).where(ArchiveConsent.revoked_at.is_(None))
        own_ids = select(GraduationCycle.id).where(GraduationCycle.student_id == user.profile_id)
        conds.append(ArchivePackage.cycle_id.in_(own_ids) | ArchivePackage.id.in_(public_ids))
    else:  # TEACHER: 本人指导学生 + 已公开
        public_ids = select(ArchiveConsent.package_id).where(ArchiveConsent.revoked_at.is_(None))
        own_ids = select(GraduationCycle.id).where(GraduationCycle.teacher_id == user.profile_id)
        conds.append(ArchivePackage.cycle_id.in_(own_ids) | ArchivePackage.id.in_(public_ids))

    if academic_year:
        conds.append(GraduationCycle.academic_year == academic_year)
    if college_id:
        conds.append(GraduationCycle.id.in_(
            select(GraduationCycle.id)
            .join(StudentProfile, StudentProfile.id == GraduationCycle.student_id)
            .where(StudentProfile.college_id == college_id)
        ))
    if major_id:
        conds.append(GraduationCycle.id.in_(
            select(GraduationCycle.id)
            .join(StudentProfile, StudentProfile.id == GraduationCycle.student_id)
            .where(StudentProfile.major_id == major_id)
        ))
    if teacher_id:
        conds.append(GraduationCycle.teacher_id == teacher_id)

    total = db.execute(select(func.count(ArchivePackage.id)).join(
        GraduationCycle, GraduationCycle.id == ArchivePackage.cycle_id).where(*conds)).scalar()
    rows = db.execute(
        base.where(*conds).order_by(ArchivePackage.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [_package_item(db, pkg, viewer=user) for pkg, _cycle in rows]
    if keyword:
        items = [i for i in items if keyword in (i.get("topic_title") or "")
                 or keyword in (i.get("student_name") or "") or keyword in (i.get("student_no") or "")]
        total = len(items)
    return ResponseUtil.page(items, total, page, page_size)


@router.get("/archive-packages/{package_id}")
def package_detail(package_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    item = _package_item(db, pkg, viewer=user)
    files = db.execute(
        select(ArchiveFile, SysFile).join(SysFile, SysFile.id == ArchiveFile.file_id)
        .where(ArchiveFile.package_id == package_id)
    ).all()
    item["files"] = [{
        "id": af.id, "file_id": f.id, "file_role": af.file_role, "sha256": af.sha256,
        "original_name": f.original_name, "size": f.size,
        "url": f"/api/v1/archive-files/{af.id}/download",
    } for af, f in files]

    allowed_detail = user.role == "ADMIN"
    if not allowed_detail and user.role == "STUDENT":
        allowed_detail = cycle.student_id == user.profile_id
    if not allowed_detail and user.role == "TEACHER":
        allowed_detail = cycle.teacher_id == user.profile_id
    if not allowed_detail:
        consent = _active_consent(db, package_id)
        allowed_detail = bool(consent and consent.visibility == ConsentVisibility.FULL_TEXT.value)
    if not allowed_detail:
        item["files"] = []  # 无权者不返回文件清单
    return ResponseUtil.success(item)


class ConsentRequest(BaseModel):
    visibility: str = Field(pattern="^(METADATA|FULL_TEXT)$")


@router.post("/archive-packages/{package_id}/consent")
def grant_consent(package_id: int, body: ConsentRequest, user: CurrentUser = Depends(require_student),
                  db: Session = Depends(get_db)):
    """学生本人授权公开; 重新授权时旧授权自动失效."""
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    if cycle.student_id != user.profile_id:
        raise PermissionDeniedException("仅成果作者本人可设置公开范围")
    old = _active_consent(db, package_id)
    if old is not None:
        old.revoked_at = datetime.now()
    db.add(ArchiveConsent(package_id=package_id, student_user_id=user.id,
                          visibility=body.visibility, consented_at=datetime.now()))
    db.add(OperationLog(user_id=user.id, username=user.username, module="ARCHIVE", action="GRANT_CONSENT",
                        detail=f"归档包#{package_id}公开范围：{body.visibility}"))
    db.commit()
    return ResponseUtil.success({"visibility": body.visibility}, message="公开授权已生效")


@router.delete("/archive-packages/{package_id}/consent")
def revoke_consent(package_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """学生撤回公开: 检索与下载立即失效(docs P3 验收)."""
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    if cycle.student_id != user.profile_id:
        raise PermissionDeniedException("仅成果作者本人可撤回公开")
    consent = _active_consent(db, package_id)
    if consent is None:
        raise BusinessException("当前没有生效的公开授权")
    consent.revoked_at = datetime.now()
    db.add(OperationLog(user_id=user.id, username=user.username, module="ARCHIVE", action="REVOKE_CONSENT",
                        detail=f"撤回归档包#{package_id}公开授权"))
    db.commit()
    return ResponseUtil.success(message="公开授权已撤回")


@router.get("/archive-files/{archive_file_id}/download")
def download_archive_file(archive_file_id: int, user: CurrentUser = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    af = db.get(ArchiveFile, archive_file_id)
    if af is None:
        raise ResourceNotFoundException("归档文件不存在")
    pkg = db.get(ArchivePackage, af.package_id)
    sf = db.get(SysFile, af.file_id)

    allowed = False
    if pkg.status == ArchiveStatus.ARCHIVED.value and sf is not None:
        if user.role == "ADMIN":
            allowed = True
        else:
            cycle = db.get(GraduationCycle, pkg.cycle_id)
            if user.role == "STUDENT" and cycle.student_id == user.profile_id:
                allowed = True
            elif user.role == "TEACHER" and cycle.teacher_id == user.profile_id:
                allowed = True
            else:
                consent = _active_consent(db, pkg.id)
                allowed = bool(consent and consent.visibility == ConsentVisibility.FULL_TEXT.value)

    db.add(ArchiveDownloadLog(package_id=pkg.id, file_id=af.file_id, user_id=user.id, allowed=1 if allowed else 0))
    db.commit()
    if not allowed:
        raise PermissionDeniedException("无权下载该归档文件(需作者或全文明授权)")
    path = storage.abs_path(sf.storage_path)
    if not path.exists():
        raise ResourceNotFoundException("文件已丢失")
    return FileResponse(path, filename=sf.original_name)


@router.post("/archive-packages/{package_id}/revoke")
def revoke_package(package_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """管理员撤回归档: 归档记录保留, 撤回公开授权(roadmap P3: 保留下载审计和撤回公开入口)."""
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    if pkg.status != ArchiveStatus.ARCHIVED.value:
        raise BusinessException("仅已归档状态可撤回")
    pkg.status = ArchiveStatus.REVOKED.value
    consent = _active_consent(db, package_id)
    if consent is not None:
        consent.revoked_at = datetime.now()
    db.add(OperationLog(user_id=user.id, module="ARCHIVE", action="REVOKE_PACKAGE", detail=f"撤回归档包#{package_id}"))
    db.commit()
    return ResponseUtil.success(message="归档已撤回, 公开授权同步失效")


@router.get("/archive-packages/export/list")
def export_list(user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ArchivePackage, GraduationCycle).join(GraduationCycle, GraduationCycle.id == ArchivePackage.cycle_id)
        .order_by(ArchivePackage.id.desc())
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["归档ID", "学年", "学号", "姓名", "题目", "导师", "状态", "公开范围", "归档时间"])
    for pkg, cycle in rows:
        item = _package_item(db, pkg)
        writer.writerow([pkg.id, cycle.academic_year, item["student_no"], item["student_name"],
                         item["topic_title"], item["teacher_name"], item["status_cn"],
                         item["visibility_cn"], item["archived_at"]])
    buf.seek(0)
    return StreamingResponse(
        iter(["\ufeff" + buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=archive_list.csv"},
    )
