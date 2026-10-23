"""毕业成果归档(roadmap P3): 归档包/授权/检索/下载审计/撤回公开.

归档前置校验(全部服务端强制):
1. 该流程正式结论(defense_result, FINAL)已发布且为 PASS
2. 毕业材料审核通过
3. 归档文件来自 sys_file 且记录哈希快照
公开范围默认私有; 学生授权后才可展示元数据或全文; 撤回立即失效。
"""
import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin, require_student
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIDocument
from app.models.business import (
    GraduationCycle,
    GraduationMaterial,
    GraduationMaterialFile,
    GraduationTopic,
)
from app.models.defense import (
    ArchiveConsent,
    ArchiveDownloadLog,
    ArchiveFile,
    ArchivePackage,
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseSession,
)
from app.models.enums import ArchiveStatus, ConsentVisibility, ResultStatus
from app.models.system import OperationLog, SysFile, TopicAttachment
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared import state_machine as sm
from app.shared.cycle import get_active_cycle
from app.shared.query import paginate
from app.shared.storage import storage

router = APIRouter(tags=["Archive"])


def _active_consent(db: Session, package_id: int) -> ArchiveConsent | None:
    return db.execute(
        select(ArchiveConsent).where(
            ArchiveConsent.package_id == package_id, ArchiveConsent.revoked_at.is_(None)
        ).order_by(ArchiveConsent.id.desc())
    ).scalars().first()


@dataclass(frozen=True)
class ArchiveView:
    """归档可见性判定结果 —— 先判定, 再决定"构造哪些字段"(D26).

    判定口径(与任务书授权集合一致): 本人 / 导师 / 该流程答辩评委 / 管理员 为"有权查看";
    其余人只有作者显式授权(FULL_TEXT)时才拿到作者身份, 授权 METADATA 只放摘要与关键词。
    """

    is_admin: bool = False
    is_owner: bool = False
    is_supervisor: bool = False
    is_examiner: bool = False
    visibility: str = "PRIVATE"
    consent: ArchiveConsent | None = None

    @property
    def published(self) -> bool:
        return self.visibility == ConsentVisibility.FULL_TEXT.value

    @property
    def meta_public(self) -> bool:
        return self.visibility in (ConsentVisibility.METADATA.value, ConsentVisibility.FULL_TEXT.value)

    @property
    def detail(self) -> bool:
        """作者本人/导师/评委/管理员: 可看成绩与答辩结论."""
        return self.is_admin or self.is_owner or self.is_supervisor or self.is_examiner

    @property
    def identity(self) -> bool:
        """姓名/学号/学院/专业/导师: 有权者或作者已授权公开全文."""
        return self.detail or self.published

    @property
    def summary(self) -> bool:
        return self.detail or self.meta_public


def _is_examiner(db: Session, cycle_id: int | None, teacher_profile_id: int | None) -> bool:
    """该流程存在答辩场次的评委记录 → 视为有权查看(P1-A §8 同一口径)."""
    if cycle_id is None or teacher_profile_id is None:
        return False
    row = db.execute(
        select(DefenseExaminer.id)
        .join(DefenseAssignment, DefenseAssignment.session_id == DefenseExaminer.session_id)
        .where(DefenseAssignment.cycle_id == cycle_id, DefenseExaminer.teacher_id == teacher_profile_id)
    ).first()
    return row is not None


def _archive_view(db: Session, pkg: ArchivePackage, cycle: GraduationCycle,
                  viewer: CurrentUser | None) -> ArchiveView:
    role = getattr(viewer, "role", None)
    profile_id = getattr(viewer, "profile_id", None)
    consent = _active_consent(db, pkg.id)
    return ArchiveView(
        is_admin=role == "ADMIN",
        is_owner=bool(role == "STUDENT" and profile_id is not None and profile_id == cycle.student_id),
        is_supervisor=bool(role == "TEACHER" and profile_id is not None and profile_id == cycle.teacher_id),
        is_examiner=bool(role == "TEACHER" and _is_examiner(db, cycle.id, profile_id)),
        # 无生效授权即 PRIVATE: PRIVATE 是业务侧的"未公开"取值, 不是 ConsentVisibility 枚举成员
        visibility=consent.visibility if consent else "PRIVATE",
        consent=consent,
    )


# 归档状态中文名唯一真源: 检索/详情/学生自助入口共用, 不允许各接口各写一份
PACKAGE_STATUS_CN = {"PENDING": "待归档", "ARCHIVED": "已归档", "REVOKED": "已撤回"}


def _package_item(db: Session, pkg: ArchivePackage, viewer: CurrentUser | None = None) -> dict:
    """归档卡片/详情公共构造器.

    D26: 敏感字段(姓名/学号/学院/专业/导师/成绩/答辩结论)**在可见性判定之后**才构造,
    无权者拿到的字典里根本不存在这些键 —— 不是构造完再置空或事后删除。
    """
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    view = _archive_view(db, pkg, cycle, viewer)
    item = {
        "id": pkg.id, "cycle_id": pkg.cycle_id, "academic_year": cycle.academic_year,
        "status": pkg.status,
        "status_cn": PACKAGE_STATUS_CN.get(pkg.status, pkg.status),
        "archived_at": pkg.archived_at.isoformat() if pkg.archived_at else None,
        "topic_title": db.execute(
            select(GraduationTopic.title).where(GraduationTopic.id == cycle.topic_id)
        ).scalar() if cycle.topic_id else None,
        "file_count": db.execute(
            select(func.count(ArchiveFile.id)).where(ArchiveFile.package_id == pkg.id)
        ).scalar(),
        "visibility": view.visibility,
        "visibility_cn": {"METADATA": "公开元数据", "FULL_TEXT": "公开全文", "PRIVATE": "私有"}.get(view.visibility),
    }
    if view.identity:
        stu = db.execute(
            select(SysUser, StudentProfile).join(StudentProfile, StudentProfile.user_id == SysUser.id)
            .where(StudentProfile.id == cycle.student_id)
        ).first()
        item["student_name"] = stu[0].real_name if stu else None
        item["student_no"] = stu[1].student_no if stu else None
        item["college"] = db.execute(
            select(College.name).where(College.id == stu[1].college_id)
        ).scalar() if stu and stu[1].college_id else None
        item["major"] = db.execute(
            select(Major.name).where(Major.id == stu[1].major_id)
        ).scalar() if stu and stu[1].major_id else None
        item["teacher_name"] = db.execute(
            select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
            .where(TeacherProfile.id == cycle.teacher_id)
        ).scalar() if cycle.teacher_id else None
    if view.detail:
        result = db.get(DefenseResult, pkg.result_id) if pkg.result_id else None
        item["result_score"] = result.score if result else None
        item["result_decision"] = result.decision if result else None
    if view.summary:
        item["abstract"] = pkg.abstract
        item["keywords"] = pkg.keywords
    return item


# ---------- 归档状态唯一写入口 ----------
# 归档状态的全部规则(源状态 / 目标态 / 角色 / 条件 UPDATE 且 rowcount 必须为 1)、
# archived_at 与 reviewed_at 写入、ReviewRecord(business_type=ARCHIVE_PACKAGE) 留痕
# (含 student_id 由 cycle 反查), 全部由 state_machine.transition(ARCHIVE) 承担;
# 本函数不再持有任何补充逻辑, 只保留签名以免调用点改名。


def archive_transition(db: Session, pkg: ArchivePackage, action: str, actor: CurrentUser, *,
                       comment: str | None = None) -> ArchivePackage:
    """归档状态唯一写入口 = state_machine.transition(ARCHIVE) 的薄封装(D28/§4)."""
    return sm.transition(db, sm.ARCHIVE, pkg, action, actor, comment=comment)


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
    """提交归档(幂等): 按 cycle_id 取既有记录, 按状态决定复用还是明确报错(D28).

    ArchivePackage 对 cycle_id 唯一, 撤回后再次提交必须复用同一条记录,
    不能再 INSERT(否则 IntegrityError → 500), 也不能悄悄留下两条。
    """
    cycle, result = _validate_archive_eligible(db, body.cycle_id)
    pkg = db.execute(
        select(ArchivePackage).where(ArchivePackage.cycle_id == body.cycle_id).with_for_update()
    ).scalars().first()
    if pkg is not None:
        if pkg.status == ArchiveStatus.ARCHIVED.value:
            raise BusinessException("该流程已存在有效归档包")
        if pkg.status == ArchiveStatus.PENDING.value:
            raise BusinessException("该流程归档正在处理中, 请勿重复提交")
        if pkg.status != ArchiveStatus.REVOKED.value:  # 未知状态不猜, 交给数据修复
            raise BusinessException(f"归档包状态异常({pkg.status}), 无法重新提交")
        # REVOKED → 复用同一条记录重新提交(文件快照整体替换)
        db.execute(delete(ArchiveFile).where(ArchiveFile.package_id == pkg.id))
    else:
        pkg = ArchivePackage(cycle_id=body.cycle_id, student_id=cycle.student_id,
                             teacher_id=cycle.teacher_id, status=ArchiveStatus.PENDING.value,
                             created_by=user.id)
        db.add(pkg)
        db.flush()

    pkg.result_id = result.id
    pkg.title = body.title
    pkg.abstract = body.abstract
    pkg.keywords = body.keywords
    pkg.created_by = pkg.created_by or user.id
    # P9-A: 管理端代办路径同样写归属, 否则迁移 0004 的 NOT NULL 会拒绝插入
    pkg.student_id = pkg.student_id or cycle.student_id
    pkg.teacher_id = pkg.teacher_id or cycle.teacher_id
    archive_transition(db, pkg, "submit", user)  # REVOKED → PENDING(新建对象则保持 PENDING)

    for f in body.files:
        file_id = f.get("file_id")
        role = f.get("file_role") or "OTHER"
        sf = db.get(SysFile, file_id)
        if sf is None or sf.deleted_at is not None:
            raise ResourceNotFoundException(f"文件#{file_id}不存在")
        db.add(ArchiveFile(package_id=pkg.id, file_id=file_id, file_role=role,
                           version=1, sha256=sf.sha256))
    # 归档由管理员一次性提交并批准: 接口本身即 require_admin, 且归档前置条件(结论已发布且通过、
    # 材料已通过)已在 _validate_archive_eligible 中校验, 故同一事务内完成 approve → ARCHIVED。
    archive_transition(db, pkg, "approve", user)
    db.add(OperationLog(user_id=user.id, module="ARCHIVE", action="CREATE_PACKAGE",
                        detail=f"归档流程#{body.cycle_id}, 文件数{len(body.files)}"))
    db.commit()
    return ResponseUtil.success(_package_item(db, pkg, viewer=user), message="归档完成")


# ---------- P9-A 学生自助提交归档 ----------
# 归档不再由管理员一次性 submit+approve 代办: 学生提交 → PENDING, 学院确认 → ARCHIVED。
# 状态写入口仍是 state_machine.transition(ARCHIVE), 本段只负责业务字段、归属与文件校验。

_STUDENT_FILE_LIMIT = 50


class StudentArchiveFile(BaseModel):
    file_id: int
    file_role: str = Field(default="OTHER", max_length=32)


class StudentArchiveSubmit(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    abstract: str | None = None
    keywords: str | None = Field(default=None, max_length=500)
    files: list[StudentArchiveFile] = Field(min_length=1, description='[{"file_id":1,"file_role":"THESIS"}]')


def _unreferenced_condition():
    """"未被任何业务记录引用"的 SysFile 条件: 逐业务表取反相关 EXISTS。

    用 EXISTS 取反而不是 NOT IN: ai_document.file_id 可空, 子查询一旦出现 NULL,
    NOT IN 会让整条谓词恒为假(文件列表恒空) —— NOT IN 的经典陷阱。
    """
    referenced = [
        select(model.id).where(model.file_id == SysFile.id).exists()
        for model in (ArchiveFile, GraduationMaterialFile, TopicAttachment, AIDocument)
    ]
    return ~or_(*referenced)


def _own_unreferenced_files(db: Session, user: CurrentUser) -> list[SysFile]:
    """本人上传且尚未被任何业务记录引用的文件(可按 id desc 取前 _STUDENT_FILE_LIMIT 条)."""
    stmt = (
        select(SysFile)
        .where(SysFile.uploader_id == user.id, SysFile.deleted_at.is_(None), _unreferenced_condition())
        .order_by(SysFile.id.desc())
        .limit(_STUDENT_FILE_LIMIT)
    )
    return list(db.execute(stmt).scalars().all())


@router.get("/student/archive/eligibility")
def student_archive_eligibility(user: CurrentUser = Depends(require_student),
                               db: Session = Depends(get_db)):
    """学生自助归档入口状态: 能否提交 + 当前归档包 + 可用于归档的本人文件。

    归档前置条件复用 _validate_archive_eligible(不复制判断), 它抛出的中文业务提示直接作为
    reason 回给学生; cycle 口径复用 shared/cycle.get_active_cycle, 不另立"当前学年"判断。
    """
    cycle = get_active_cycle(db, user.profile_id) if user.profile_id is not None else None
    eligible = False
    reason: str | None = None
    pkg: ArchivePackage | None = None
    if cycle is None:
        reason = "当前没有进行中的毕业流程实例"
    else:
        pkg = db.execute(
            select(ArchivePackage).where(ArchivePackage.cycle_id == cycle.id)
        ).scalars().first()
        try:
            _validate_archive_eligible(db, cycle.id)
            eligible = True
        except BusinessException as exc:
            reason = exc.message
    files = _own_unreferenced_files(db, user)
    return ResponseUtil.success({
        "eligible": eligible,
        "reason": reason,
        "cycle_id": cycle.id if cycle is not None else None,
        "academic_year": cycle.academic_year if cycle is not None else None,
        "package_id": pkg.id if pkg is not None else None,
        "package_status": pkg.status if pkg is not None else None,
        "package_status_cn": PACKAGE_STATUS_CN.get(pkg.status) if pkg is not None else None,
        "files": [{
            "file_id": f.id,
            "original_name": f.original_name,
            "size": f.size,
            "extension": f.extension,
            "uploaded_at": f.created_at.isoformat() if f.created_at else None,
        } for f in files],
    })


@router.post("/student/archive/packages")
def submit_archive_package(body: StudentArchiveSubmit, user: CurrentUser = Depends(require_student),
                           db: Session = Depends(get_db)):
    """学生提交归档: 只 submit(→PENDING), 不自动 approve; 学院确认后才是 ARCHIVED。

    幂等口径沿用 create_archive: 按 cycle_id 取既有记录 with_for_update(), 按状态决定
    复用还是明确报错 —— 撤回后重新提交复用同一条(cycle_id 唯一), 不允许留下两条。
    校验顺序: 归属/前置条件/包状态/文件归属全部先过, 最后才迁移状态(先校验后迁移)。
    包状态先于文件归属, 避免对一个已归档流程回显他人文件是否存在。
    """
    if user.profile_id is None:
        raise PermissionDeniedException("当前账号没有学生档案, 无法提交归档")
    cycle = get_active_cycle(db, user.profile_id)
    if cycle is None:
        raise BusinessException("当前没有进行中的毕业流程实例, 无法提交归档")
    if cycle.student_id != user.profile_id:
        raise PermissionDeniedException("只能提交本人的归档")
    cycle, result = _validate_archive_eligible(db, cycle.id)

    pkg = db.execute(
        select(ArchivePackage).where(ArchivePackage.cycle_id == cycle.id).with_for_update()
    ).scalars().first()
    if pkg is not None:
        if pkg.status == ArchiveStatus.ARCHIVED.value:
            raise BusinessException("该流程已完成归档")
        if pkg.status == ArchiveStatus.PENDING.value:
            raise BusinessException("归档申请审核中, 请勿重复提交")
        if pkg.status != ArchiveStatus.REVOKED.value:  # 未知状态不猜, 交给数据修复
            raise BusinessException(f"归档包状态异常({pkg.status}), 无法重新提交")
        # REVOKED → 复用同一条记录, 文件快照整体替换
        db.execute(delete(ArchiveFile).where(ArchiveFile.package_id == pkg.id))

    resolved: list[tuple[SysFile, str]] = []
    for item in body.files:
        sf = db.get(SysFile, item.file_id)
        if sf is None or sf.deleted_at is not None:
            raise ResourceNotFoundException(f"文件#{item.file_id}不存在")
        if sf.uploader_id != user.id:
            raise PermissionDeniedException(f"文件#{item.file_id}不是本人上传, 不能用于归档")
        resolved.append((sf, item.file_role or "OTHER"))

    if pkg is None:
        # 新建推迟到文件校验之后: 否则校验失败会在 session 里留下一个空 PENDING 包
        pkg = ArchivePackage(cycle_id=cycle.id, student_id=user.profile_id,
                             teacher_id=cycle.teacher_id, status=ArchiveStatus.PENDING.value,
                             created_by=user.id)
        db.add(pkg)
        db.flush()

    pkg.result_id = result.id
    pkg.title = body.title
    pkg.abstract = body.abstract
    pkg.keywords = body.keywords
    # P9-A: 写归属, 状态机的 STUDENT 归属校验由此生效(此前无列可用)
    pkg.student_id = user.profile_id
    pkg.teacher_id = cycle.teacher_id
    pkg.created_by = pkg.created_by or user.id
    archive_transition(db, pkg, "submit", user)  # 只到 PENDING, 不 approve

    for sf, role in resolved:
        db.add(ArchiveFile(package_id=pkg.id, file_id=sf.id, file_role=role,
                           version=1, sha256=sf.sha256))
    db.add(OperationLog(user_id=user.id, username=user.username, module="ARCHIVE", action="SUBMIT_PACKAGE",
                        detail=f"归档流程#{cycle.id}, 文件数{len(resolved)}"))
    db.commit()
    return ResponseUtil.success(_package_item(db, pkg, viewer=user), message="归档申请已提交, 等待学院确认")


def _keyword_condition(keyword: str, identity_visible):
    """关键字条件(D33): 题目名恒可搜; 姓名/学号只对"能看见作者身份"的行生效(D26 同口径)."""
    pattern = f"%{keyword}%"
    title_hit = ArchivePackage.cycle_id.in_(
        select(GraduationCycle.id).where(GraduationCycle.topic_id.in_(
            select(GraduationTopic.id).where(GraduationTopic.title.like(pattern))))
    )
    name_hit = ArchivePackage.cycle_id.in_(
        select(GraduationCycle.id)
        .join(StudentProfile, StudentProfile.id == GraduationCycle.student_id)
        .join(SysUser, SysUser.id == StudentProfile.user_id)
        .where(or_(SysUser.real_name.like(pattern), SysUser.username.like(pattern),
                   StudentProfile.student_no.like(pattern)))
    )
    if identity_visible is None:  # 管理员: 不设限
        return or_(title_hit, name_hit)
    return or_(title_hit, and_(identity_visible, name_hit))


@router.get("/archive-packages")
def search_packages(academic_year: str | None = None, college_id: int | None = None, major_id: int | None = None,
                    teacher_id: int | None = None, keyword: str | None = None,
                    status: Literal["PENDING", "ARCHIVED", "REVOKED"] | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """检索归档: 权限过滤 + 条件筛选全部在 SQL 层完成, 之后才分页(docs §1.2/§5, D33).

    total 是"过滤之后"的总数, 与 items 口径一致; 不允许先取一页再在内存里过滤关键字
    (那样命中落在后面几页时会返回空列表却写着 total=N)。
    """
    conds = []
    full_text_ids = select(ArchiveConsent.package_id).where(
        ArchiveConsent.revoked_at.is_(None),
        ArchiveConsent.visibility == ConsentVisibility.FULL_TEXT.value,
    )
    identity_visible = None
    if user.role == "ADMIN":
        pass
    elif user.role == "STUDENT":
        # 本人全部 + 已公开(任意明)他人
        public_ids = select(ArchiveConsent.package_id).where(ArchiveConsent.revoked_at.is_(None))
        own_ids = select(GraduationCycle.id).where(GraduationCycle.student_id == user.profile_id)
        conds.append(ArchivePackage.cycle_id.in_(own_ids) | ArchivePackage.id.in_(public_ids))
        identity_visible = ArchivePackage.cycle_id.in_(own_ids) | ArchivePackage.id.in_(full_text_ids)
    else:  # TEACHER: 本人指导学生 + 本人任评委的场次 + 已公开
        public_ids = select(ArchiveConsent.package_id).where(ArchiveConsent.revoked_at.is_(None))
        own_ids = select(GraduationCycle.id).where(GraduationCycle.teacher_id == user.profile_id)
        examiner_ids = select(DefenseAssignment.cycle_id).join(
            DefenseExaminer, DefenseExaminer.session_id == DefenseAssignment.session_id
        ).where(DefenseExaminer.teacher_id == user.profile_id)
        conds.append(ArchivePackage.cycle_id.in_(own_ids)
                     | ArchivePackage.cycle_id.in_(examiner_ids)
                     | ArchivePackage.id.in_(public_ids))
        identity_visible = (ArchivePackage.cycle_id.in_(own_ids)
                            | ArchivePackage.cycle_id.in_(examiner_ids)
                            | ArchivePackage.id.in_(full_text_ids))

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
    if status:
        # 下推 SQL: total 与 items 同口径, 不在内存里筛状态
        conds.append(ArchivePackage.status == status)

    text = (keyword or "").strip()
    if text:
        conds.append(_keyword_condition(text, identity_visible))

    stmt = (select(ArchivePackage)
            .join(GraduationCycle, GraduationCycle.id == ArchivePackage.cycle_id)
            .order_by(ArchivePackage.id.desc()))
    rows, total = paginate(db, stmt, page=page, size=page_size, filters=conds)
    return ResponseUtil.page([_package_item(db, pkg, viewer=user) for pkg in rows], total, page, page_size)


@router.get("/archive-packages/{package_id}")
def package_detail(package_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    cycle = db.get(GraduationCycle, pkg.cycle_id)
    view = _archive_view(db, pkg, cycle, user)
    item = _package_item(db, pkg, viewer=user)
    files = db.execute(
        select(ArchiveFile, SysFile).join(SysFile, SysFile.id == ArchiveFile.file_id)
        .where(ArchiveFile.package_id == package_id)
    ).all()
    # 文件清单与敏感字段同一口径: 有权者(本人/导师/评委/管理员)或作者已授权公开全文
    item["files"] = [{
        "id": af.id, "file_id": f.id, "file_role": af.file_role, "sha256": af.sha256,
        "original_name": f.original_name, "size": f.size,
        "url": f"/api/v1/archive-files/{af.id}/download",
    } for af, f in files] if (view.detail or view.published) else []
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


logger = logging.getLogger(__name__)


def _recompute_progress(db: Session, cycle_id: int | None) -> None:
    """归档域状态变化后重算该学生进度(D74); 与 defense/service.py 同一口径."""
    if cycle_id is None:
        return
    cycle = db.get(GraduationCycle, cycle_id)
    if cycle is None:
        logger.warning("D74 重算跳过: cycle#%s 不存在", cycle_id)
        return
    from app.shared.progress import ProgressService

    ProgressService.recompute(db, cycle.student_id, cycle_id=cycle.id)


@router.post("/archive-packages/{package_id}/revoke")
def revoke_package(package_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """管理员撤回归档: 归档记录保留, 撤回公开授权(roadmap P3: 保留下载审计和撤回公开入口)."""
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    if pkg.status != ArchiveStatus.ARCHIVED.value:
        raise BusinessException("仅已归档状态可撤回")
    archive_transition(db, pkg, "revoke", user, comment="管理员撤回归档")
    consent = _active_consent(db, package_id)
    if consent is not None:
        consent.revoked_at = datetime.now()
    db.add(OperationLog(user_id=user.id, module="ARCHIVE", action="REVOKE_PACKAGE", detail=f"撤回归档包#{package_id}"))
    # D74: 归档是"是否毕业"的第三个判定输入, 撤回后必须重算(否则 completed_at 残留)
    _recompute_progress(db, pkg.cycle_id)
    db.commit()
    return ResponseUtil.success(message="归档已撤回, 公开授权同步失效")


class ArchiveApprove(BaseModel):
    comment: str | None = Field(default=None, max_length=1000, description="确认意见(纯文本)")


@router.post("/archive-packages/{package_id}/approve")
def approve_package(package_id: int, body: ArchiveApprove | None = None,
                    user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """学院确认归档: PENDING → ARCHIVED(学生自助提交后的唯一生效动作).

    非法源状态交给状态机抛 40003(不在这里另写 if); 归档是"是否毕业"的判定输入之一,
    确认后必须重算进度, 否则 GraduationProcess.completed_at 不会落值(D74)。
    """
    pkg = db.get(ArchivePackage, package_id)
    if pkg is None:
        raise ResourceNotFoundException("归档包不存在")
    archive_transition(db, pkg, "approve", user, comment=body.comment if body else None)
    _recompute_progress(db, pkg.cycle_id)
    db.add(OperationLog(user_id=user.id, username=user.username, module="ARCHIVE", action="APPROVE_PACKAGE",
                        detail=f"确认归档包#{package_id}"))
    db.commit()
    return ResponseUtil.success(_package_item(db, pkg, viewer=user), message="归档已确认")


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
        item = _package_item(db, pkg, viewer=user)
        writer.writerow([pkg.id, cycle.academic_year, item["student_no"], item["student_name"],
                         item["topic_title"], item["teacher_name"], item["status_cn"],
                         item["visibility_cn"], item["archived_at"]])
    buf.seek(0)
    return StreamingResponse(
        iter(["\ufeff" + buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=archive_list.csv"},
    )
