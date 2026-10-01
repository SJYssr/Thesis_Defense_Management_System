"""阶段材料统一状态机引擎.

开题/中期/终期报告遵循完全一致的生命周期(roadmap 第十章):

    NOT_SUBMITTED → DRAFT → SUBMITTED → APPROVED
                                   ↘ REJECTED/RESUBMIT_REQUIRED → (修改) → SUBMITTED(v+1)

学生编辑仅允许在 NOT_SUBMITTED/DRAFT/REJECTED/RESUBMIT_REQUIRED 状态;
提交时创建版本快照(roadmap 第十七章), 审核通过推进 graduation_process.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException, StageValidationException
from app.models.enums import BusinessType, ProcessStage
from app.models.system import ReviewRecord
from app.models.user import StudentProfile, SysUser, TeacherProfile

EDITABLE_STATUSES = {"NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"}
STAGE_CN = {
    ProcessStage.OPENING: "开题报告",
    ProcessStage.MIDTERM: "中期报告",
    ProcessStage.FINAL: "终期答辩材料",
    ProcessStage.MATERIAL: "毕业材料",
}


@dataclass
class StageConfig:
    stage: ProcessStage
    business_type: BusinessType
    model: type
    version_model: type
    version_fk: str
    label: str
    fields: list[str]
    required_fields: list[str] = field(default_factory=list)

    @property
    def require_check(self):
        from app.shared.workflow import GraduationWorkflowService

        return {
            ProcessStage.OPENING: GraduationWorkflowService.require_can_submit_opening,
            ProcessStage.MIDTERM: GraduationWorkflowService.require_can_submit_midterm,
            ProcessStage.FINAL: GraduationWorkflowService.require_can_submit_final,
            ProcessStage.MATERIAL: GraduationWorkflowService.require_can_submit_material,
        }[self.stage]


STATUS_CN = {
    "NOT_SUBMITTED": "未提交", "DRAFT": "草稿", "SUBMITTED": "已提交待审核",
    "APPROVED": "已通过", "REJECTED": "已驳回", "RESUBMIT_REQUIRED": "需重新提交",
}


class StageEngine:
    def __init__(self, cfg: StageConfig):
        self.cfg = cfg

    # ---------- 学生 ----------

    def _active_cycle(self, db: Session, student_id: int):
        from app.shared.cycle import get_active_cycle

        return get_active_cycle(db, student_id)

    def get_report(self, db: Session, student_id: int):
        """按学生当前学年流程实例(cycle)取阶段材料, 支持跨学年重做."""
        cycle = self._active_cycle(db, student_id)
        if cycle is None:
            return None
        return db.execute(
            select(self.cfg.model).where(self.cfg.model.cycle_id == cycle.id)
        ).scalar_one_or_none()

    def student_view(self, db: Session, student_id: int) -> dict:
        report = self.get_report(db, student_id)
        if report is None:
            return {"status": "NOT_SUBMITTED", "status_cn": STATUS_CN["NOT_SUBMITTED"], "can_edit": True,
                    "can_submit": False, "content": {}}
        data = {f: getattr(report, f) for f in self.cfg.fields}
        data["id"] = report.id
        data["title"] = getattr(report, "title", None)
        return {
            "id": report.id, "status": report.status, "status_cn": STATUS_CN.get(report.status, report.status),
            "can_edit": report.status in EDITABLE_STATUSES, "can_submit": report.status in EDITABLE_STATUSES,
            "review_comment": report.review_comment,
            "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "current_version": report.current_version,
            "content": data,
        }

    def _ensure_editable(self, report) -> None:
        if report is not None and report.status not in EDITABLE_STATUSES:
            raise BusinessException(f"当前状态为「{STATUS_CN.get(report.status)}」, 不允许编辑")

    def student_save(self, db: Session, student_id: int, payload: dict, as_draft: bool = True):
        """保存(草稿或直接内容更新). 阶段前置校验在后端强制执行."""
        self.cfg.require_check(db, student_id)
        cycle = self._active_cycle(db, student_id)
        report = self.get_report(db, student_id)
        self._ensure_editable(report)
        clean = {k: v for k, v in payload.items() if k in self.cfg.fields and v is not None}
        if report is None:
            sel = self._selection(db, student_id)
            report = self.cfg.model(student_id=student_id, cycle_id=cycle.id,
                                    topic_id=sel.topic_id, teacher_id=sel.teacher_id, **clean)
            db.add(report)
        else:
            for k, v in clean.items():
                setattr(report, k, v)
        if as_draft and report.status in ("NOT_SUBMITTED",):
            report.status = "DRAFT"
        db.commit()
        return self.student_view(db, student_id)

    def student_submit(self, db: Session, student_id: int, user_id: int):
        self.cfg.require_check(db, student_id)
        report = self.get_report(db, student_id)
        if report is None:
            raise StageValidationException(f"请先填写{self.cfg.label}内容")
        self._ensure_editable(report)
        missing = [f for f in self.cfg.required_fields if not (getattr(report, f) or "").strip()]
        if missing:
            raise BusinessException(f"以下必填项为空: {', '.join(missing)}")

        before = report.status
        report.status = "SUBMITTED"
        report.current_version += 1
        report.submitted_at = dt.now()
        report.review_comment = None

        # 版本快照: 不覆盖历史材料
        content = {f: getattr(report, f) for f in self.cfg.fields}
        if hasattr(report, "title"):
            content["title"] = report.title
        version = self.cfg.version_model(
            **{self.cfg.version_fk: report.id},
            version_no=report.current_version,
            content_json=json.dumps(content, ensure_ascii=False),
            submitted_at=report.submitted_at,
        )
        db.add(version)

        from app.shared.events import add_review_record, push_notification

        add_review_record(db, business_type=self.cfg.business_type.value, business_id=report.id,
                          student_id=student_id, action="SUBMIT", before_status=before,
                          after_status="SUBMITTED", comment=f"提交第 {report.current_version} 版")
        teacher_user_id = db.execute(
            select(TeacherProfile.user_id).where(TeacherProfile.id == report.teacher_id)
        ).scalar()
        student_name = db.execute(select(SysUser.real_name).join(StudentProfile, StudentProfile.user_id == SysUser.id).where(StudentProfile.id == student_id)).scalar()
        push_notification(db, user_id=teacher_user_id, type="REVIEW_RESULT",
                          title=f"{self.cfg.label}待审核: {student_name}",
                          content=f"学生 {student_name} 提交了{self.cfg.label} v{report.current_version}, 请及时审核",
                          business_type=self.cfg.business_type.value, business_id=report.id)
        db.commit()
        return self.student_view(db, student_id)

    def student_versions(self, db: Session, student_id: int) -> list[dict]:
        report = self.get_report(db, student_id)
        if report is None:
            return []
        rows = db.execute(
            select(self.cfg.version_model).where(getattr(self.cfg.version_model, self.cfg.version_fk) == report.id)
            .order_by(self.cfg.version_model.version_no.desc())
        ).scalars().all()
        return [
            {"id": v.id, "version_no": v.version_no, "content": json.loads(v.content_json),
             "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None}
            for v in rows
        ]

    def _selection(self, db: Session, student_id: int):
        from app.shared.workflow import GraduationWorkflowService

        return GraduationWorkflowService.require_selection_approved(db, student_id)

    # ---------- 教师/管理员 ----------

    def _list_item(self, db: Session, report) -> dict:
        stu = db.execute(
            select(StudentProfile, SysUser).join(SysUser, SysUser.id == StudentProfile.user_id)
            .where(StudentProfile.id == report.student_id)
        ).first()
        teacher_name = None
        if report.teacher_id:
            from app.models.user import TeacherProfile

            trow = db.execute(
                select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
                .where(TeacherProfile.id == report.teacher_id)
            ).first()
            teacher_name = trow[0] if trow else None
        content = {f: getattr(report, f) for f in self.cfg.fields}
        if hasattr(report, "title"):
            content["title"] = report.title
        return {
            "id": report.id, "student_id": report.student_id,
            "student_name": stu[1].real_name if stu else None,
            "student_no": stu[0].student_no if stu else None,
            "teacher_id": report.teacher_id, "teacher_name": teacher_name,
            "status": report.status,
            "status_cn": STATUS_CN.get(report.status, report.status),
            "current_version": report.current_version,
            "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "review_comment": report.review_comment,
            "content": content,
        }

    def teacher_list(self, db: Session, teacher_profile_id: int, status: str | None, page: int, page_size: int):
        conds = [self.cfg.model.teacher_id == teacher_profile_id]
        if status:
            conds.append(self.cfg.model.status == status)
        total = db.execute(select(func.count(self.cfg.model.id)).where(*conds)).scalar()
        rows = db.execute(
            select(self.cfg.model).where(*conds).order_by(self.cfg.model.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()
        return [self._list_item(db, r) for r in rows], total

    def admin_list(self, db: Session, status: str | None, page: int, page_size: int):
        conds = []
        if status:
            conds.append(self.cfg.model.status == status)
        total = db.execute(select(func.count(self.cfg.model.id)).where(*conds)).scalar()
        rows = db.execute(
            select(self.cfg.model).where(*conds).order_by(self.cfg.model.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()
        return [self._list_item(db, r) for r in rows], total

    def get_for_review(self, db: Session, report_id: int, teacher_profile_id: int | None):
        report = db.get(self.cfg.model, report_id)
        if report is None:
            raise ResourceNotFoundException(f"{self.cfg.label}记录不存在")
        if teacher_profile_id is not None and report.teacher_id != teacher_profile_id:
            # 对象级权限: 教师 A 绝不能访问教师 B 学生的材料(roadmap 场景5)
            raise ResourceNotFoundException(f"{self.cfg.label}记录不存在")
        return report

    def review(self, db: Session, report_id: int, reviewer_user_id: int, reviewer_role: str,
               approve: bool, comment: str | None, teacher_profile_id: int | None = None, score: int | None = None):
        from app.shared.events import add_review_record, push_notification
        from app.shared.progress import ProgressService
        from app.shared.workflow import GraduationWorkflowService

        report = self.get_for_review(db, report_id, teacher_profile_id)
        if report.status != "SUBMITTED":
            raise BusinessException(f"该{self.cfg.label}不在待审核状态")

        before = report.status
        if approve:
            report.status = "APPROVED"
            if score is not None and hasattr(report, "score"):
                report.score = score
            action = "APPROVE"
        else:
            report.status = "RESUBMIT_REQUIRED" if comment else "REJECTED"
            action = "RETURN" if comment else "REJECT"
        report.review_comment = comment
        report.reviewed_at = dt.now()

        add_review_record(db, business_type=self.cfg.business_type.value, business_id=report.id,
                          student_id=report.student_id, reviewer_id=reviewer_user_id, reviewer_role=reviewer_role,
                          action=action, before_status=before, after_status=report.status, comment=comment)
        if approve:
            GraduationWorkflowService.on_stage_approved(db, report.student_id, self.cfg.stage)
        ProgressService.recompute(db, report.student_id)

        student_user_id = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == report.student_id)).scalar()
        push_notification(db, user_id=student_user_id, type="REVIEW_RESULT",
                          title=f"{self.cfg.label}审核结果",
                          content=f"您的{self.cfg.label}已{'通过' if approve else '被驳回'}。意见: {comment or '无'}",
                          business_type=self.cfg.business_type.value, business_id=report.id)
        db.commit()
        return self._list_item(db, report)

    # ---------- 审核历史 ----------

    def review_history(self, db: Session, student_id: int) -> list[dict]:
        from app.models.system import ReviewRecord

        report = self.get_report(db, student_id)
        if report is None:
            return []
        rows = db.execute(
            select(ReviewRecord).where(
                ReviewRecord.business_type == self.cfg.business_type.value,
                ReviewRecord.business_id == report.id,
            ).order_by(ReviewRecord.id)
        ).scalars().all()
        items = []
        for r in rows:
            reviewer_name = None
            if r.reviewer_id:
                reviewer_name = db.get(SysUser, r.reviewer_id).real_name if db.get(SysUser, r.reviewer_id) else None
            items.append({
                "action": r.action, "comment": r.comment,
                "before_status": r.before_status, "after_status": r.after_status,
                "reviewer_name": reviewer_name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return items
