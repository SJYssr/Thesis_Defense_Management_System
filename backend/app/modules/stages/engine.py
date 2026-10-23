"""阶段材料统一状态机引擎.

开题/中期/终期报告遵循完全一致的生命周期(docs/refactor-plan.md §4):

    NOT_SUBMITTED → DRAFT → SUBMITTED → APPROVED
                                   ↘ REJECTED / RESUBMIT_REQUIRED → (修改) → SUBMITTED(v+1)

本批(P1-A)改造要点:

- 状态列一律经 `shared.state_machine.transition()` 修改, 引擎内不再赋值 `report.status`(§3 原则 1);
- 审核通过不再写 `FinalDefense.score`, 分数唯一真源是 `DefenseResult.score`(§6, 缺陷 D6);
- 驳回 / 退回修改拆成 `reject` / `request_revision` 两个显式动作, 由请求参数 `decision` 选择,
  不再用"comment 是否为空"推断(缺陷 D7);
- 审核读路径加行锁 + 条件更新, 并发双审只会成功一次(A2);
- 推进流程与重算进度都带 `cycle_id`, 不会把历史学年材料写到当前学年(A3);
- 列表查询全部走 `shared.query.paginate`, 关键字过滤下推 SQL(§9, 缺陷 D10)。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BusinessException,
    PermissionDeniedException,
    ResourceNotFoundException,
    StageValidationException,
)
from app.models.enums import BusinessType, ProcessStage
from app.models.system import ReviewRecord
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.shared import state_machine as sm
from app.shared.query import paginate

log = logging.getLogger("app.stages")

# 可编辑状态集合直接由转移表派生, 避免"状态机说能改、引擎说不能改"的双真源
EDITABLE_STATUSES = frozenset().union(*(sm.TRANSITIONS[entity]["save"] for entity in sm.STAGE_ENTITIES))

STAGE_CN = {
    ProcessStage.OPENING: "开题报告",
    ProcessStage.MIDTERM: "中期报告",
    ProcessStage.FINAL: "终期答辩材料",
    ProcessStage.MATERIAL: "毕业材料",
}

# 审核动作(显式决策), 供 router 参数校验复用
REVIEW_DECISIONS = {"approve": "approve", "reject": "reject", "request_revision": "request_revision"}
# 决策 → 学生侧提示语
_DECISION_CN = {"approve": "通过", "reject": "驳回", "request_revision": "退回修改"}


@dataclass(frozen=True)
class Actor:
    """状态机要求的操作人身份: 仅角色 + 账号 id + 业务档案 id."""

    id: int | None
    role: str
    profile_id: int | None = None

    @classmethod
    def student(cls, student_id: int, user_id: int | None = None) -> "Actor":
        return cls(id=user_id, role="STUDENT", profile_id=student_id)

    @classmethod
    def reviewer(cls, user_id: int | None, role: str, teacher_profile_id: int | None = None) -> "Actor":
        return cls(id=user_id, role=role, profile_id=teacher_profile_id)


@dataclass
class ReviewView:
    """审核读视图: 导师/管理员拿到可写视图, 该场次评委拿到只读视图(§8)."""

    report: Any
    can_edit: bool
    is_examiner: bool = False


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
    # 管理员/导师列表的关键字可搜索字段(§9 下推 SQL)
    KEYWORD_FIELDS = (SysUser.real_name, SysUser.username, StudentProfile.student_no)

    def __init__(self, cfg: StageConfig):
        self.cfg = cfg

    @property
    def entity(self) -> str:
        """阶段 → 状态机实体名(OPENING/MIDTERM/FINAL/MATERIAL)."""
        return sm.entity_for_stage(self.cfg.stage)

    # ---------- 学生 ----------

    def _active_cycle(self, db: Session, student_id: int):
        from app.shared.cycle import get_active_cycle

        return get_active_cycle(db, student_id)

    def require_cycle(self, db: Session, student_id: int):
        """当前学年流程实例缺失时抛业务异常, 而不是让 cycle.id 触发 AttributeError(D8)."""
        cycle = self._active_cycle(db, student_id)
        if cycle is None:
            raise BusinessException("当前没有进行中的毕业设计流程实例, 请先完成选题")
        return cycle

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
                    "can_submit": False, "content": {},
                    "allowed_actions": sm.allowed_actions(self.entity, "NOT_SUBMITTED")}
        data = {f: getattr(report, f) for f in self.cfg.fields}
        data["id"] = report.id
        data["title"] = getattr(report, "title", None)
        editable = report.status in EDITABLE_STATUSES
        return {
            "id": report.id, "status": report.status, "status_cn": STATUS_CN.get(report.status, report.status),
            "can_edit": editable, "can_submit": editable,
            "review_comment": report.review_comment,
            "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "current_version": report.current_version,
            "content": data,
            "allowed_actions": sm.allowed_actions(self.entity, report.status),
        }

    def _ensure_editable(self, report) -> None:
        if report is not None and report.status not in EDITABLE_STATUSES:
            raise BusinessException(f"当前状态为「{STATUS_CN.get(report.status)}」, 不允许编辑")

    def student_save(self, db: Session, student_id: int, payload: dict, as_draft: bool = True):
        """保存草稿. 阶段前置校验在后端强制执行, 状态变更一律走 transition()."""
        self.cfg.require_check(db, student_id)
        cycle = self.require_cycle(db, student_id)
        report = self.get_report(db, student_id)
        self._ensure_editable(report)
        clean = {k: v for k, v in payload.items() if k in self.cfg.fields and v is not None}
        actor = Actor.student(student_id)
        if report is None:
            sel = self._selection(db, student_id)
            # A1: 新建对象必须显式给初始状态; 依赖列默认值要等 flush 之后才可见,
            # 旧代码在此判断 status in ("NOT_SUBMITTED",) 永远不成立, 草稿态不可达。
            report = self.cfg.model(student_id=student_id, cycle_id=cycle.id,
                                    topic_id=sel.topic_id, teacher_id=sel.teacher_id,
                                    status="NOT_SUBMITTED", **clean)
            db.add(report)
            db.flush()
        else:
            for k, v in clean.items():
                setattr(report, k, v)
        if as_draft:
            sm.transition(db, self.entity, report, "save", actor)
        db.commit()
        return self.student_view(db, student_id)

    def student_submit(self, db: Session, student_id: int, user_id: int):
        from app.shared.events import push_notification

        self.cfg.require_check(db, student_id)
        self.require_cycle(db, student_id)
        report = self.get_report(db, student_id)
        if report is None:
            raise StageValidationException(f"请先填写{self.cfg.label}内容")
        self._ensure_editable(report)
        missing = [f for f in self.cfg.required_fields if not (getattr(report, f) or "").strip()]
        if missing:
            raise BusinessException(f"以下必填项为空: {', '.join(missing)}")

        # 状态 / 版本号 / submitted_at / 留痕统一由状态机处理(§3 原则 1)
        sm.transition(db, self.entity, report, "submit", Actor.student(student_id, user_id))

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
        db.flush()

        teacher_user_id = db.execute(
            select(TeacherProfile.user_id).where(TeacherProfile.id == report.teacher_id)
        ).scalar()
        student_name = db.execute(
            select(SysUser.real_name).join(StudentProfile, StudentProfile.user_id == SysUser.id)
            .where(StudentProfile.id == student_id)
        ).scalar()
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
            "cycle_id": getattr(report, "cycle_id", None),
            "score": getattr(report, "score", None),
            "status": report.status,
            "status_cn": STATUS_CN.get(report.status, report.status),
            "current_version": report.current_version,
            "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
            "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
            "review_comment": report.review_comment,
            "content": content,
        }

    def _list_stmt(self):
        """列表基础语句: 学生/教师姓名用于关键字过滤与展示(§9 全部下推 SQL)."""
        return (
            select(self.cfg.model)
            .outerjoin(StudentProfile, StudentProfile.id == self.cfg.model.student_id)
            .outerjoin(SysUser, SysUser.id == StudentProfile.user_id)
            .order_by(self.cfg.model.id.desc())
        )

    def teacher_list(self, db: Session, teacher_profile_id: int, status: str | None,
                     page: int, page_size: int, keyword: str | None = None):
        filters = [self.cfg.model.teacher_id == teacher_profile_id]
        if status:
            filters.append(self.cfg.model.status == status)
        rows, total = paginate(db, self._list_stmt(), page=page, size=page_size, keyword=keyword,
                               keyword_fields=self.KEYWORD_FIELDS, filters=filters)
        return [self._list_item(db, r) for r in rows], total

    def admin_list(self, db: Session, status: str | None, page: int, page_size: int,
                   keyword: str | None = None):
        filters = [self.cfg.model.status == status] if status else []
        rows, total = paginate(db, self._list_stmt(), page=page, size=page_size, keyword=keyword,
                               keyword_fields=self.KEYWORD_FIELDS, filters=filters)
        return [self._list_item(db, r) for r in rows], total

    # ---------- 审核读/写 ----------

    def _is_examiner(self, db: Session, cycle_id: int | None, teacher_profile_id: int | None) -> bool:
        """该 cycle 存在答辩场次的评委记录 → 允许只读查看(§8 STAGE_READ)."""
        if cycle_id is None or teacher_profile_id is None:
            return False
        from app.models.defense import DefenseAssignment, DefenseExaminer

        row = db.execute(
            select(DefenseExaminer.id)
            .join(DefenseAssignment, DefenseAssignment.session_id == DefenseExaminer.session_id)
            .where(DefenseAssignment.cycle_id == cycle_id,
                   DefenseExaminer.teacher_id == teacher_profile_id)
        ).first()
        return row is not None

    def get_for_review(self, db: Session, report_id: int, teacher_profile_id: int | None,
                       *, for_update: bool = False) -> ReviewView:
        """审核读视图.

        - 导师本人 / 管理员: can_edit=True;
        - 该 cycle 场次的答辩评委(非导师): can_edit=False 的只读视图;
        - 其他人: 记录不存在(对象级权限, 不泄露存在性)。
        for_update=True 时走 SELECT ... FOR UPDATE, 用于审核写路径(A2)。
        """
        stmt = select(self.cfg.model).where(self.cfg.model.id == report_id)
        if for_update:
            stmt = stmt.with_for_update()
        report = db.execute(stmt.execution_options(populate_existing=True)).scalar_one_or_none()
        if report is None:
            raise ResourceNotFoundException(f"{self.cfg.label}记录不存在")
        own = teacher_profile_id is not None and report.teacher_id == teacher_profile_id
        examiner = (not own) and self._is_examiner(db, getattr(report, "cycle_id", None), teacher_profile_id)
        if teacher_profile_id is not None and not own and not examiner:
            # 对象级权限: 教师 A 绝不能访问教师 B 学生的材料(roadmap 场景5)
            raise ResourceNotFoundException(f"{self.cfg.label}记录不存在")
        return ReviewView(report=report, can_edit=own or teacher_profile_id is None, is_examiner=examiner)

    def _resolve_action(self, approve: bool | None, comment: str | None, decision: str | None) -> str:
        """显式决策优先; 仅为旧调用方保留 approve 布尔兼容(不再靠 comment 推断新请求)."""
        if decision is not None:
            key = str(decision).strip().lower()
            if key not in REVIEW_DECISIONS:
                raise BusinessException(f"未知审核结论「{decision}」")
            return REVIEW_DECISIONS[key]
        if approve is None:
            raise BusinessException("缺少审核结论(approve/reject/request_revision)")
        if approve:
            return "approve"
        return "request_revision" if comment else "reject"

    def review(self, db: Session, report_id: int, reviewer_user_id: int, reviewer_role: str,
               approve: bool | None = None, comment: str | None = None,
               teacher_profile_id: int | None = None, score: int | None = None,
               decision: str | None = None):
        """审核: approve / reject / request_revision 三个显式动作(D7).

        score 参数保留只为向后兼容, 本批起不再写入 FinalDefense.score(D6, §6 单轨评分)。
        """
        from app.shared.events import push_notification
        from app.shared.progress import ProgressService
        from app.shared.workflow import GraduationWorkflowService

        action = self._resolve_action(approve, comment, decision)
        view = self.get_for_review(db, report_id, teacher_profile_id, for_update=True)
        if not view.can_edit:
            raise PermissionDeniedException(f"无权审核该{self.cfg.label}")
        report = view.report
        if score is not None:
            log.debug("%s 审核忽略 score 参数(分数唯一真源 DefenseResult.score)", self.cfg.label)

        # 状态校验 + 条件更新 + reviewed_at/review_comment/留痕都在状态机内(A2)
        sm.transition(db, self.entity, report, action,
                      Actor.reviewer(reviewer_user_id, reviewer_role, teacher_profile_id),
                      comment=comment)

        if action == "approve":
            # A3: 显式传 cycle_id, 审核历史学年材料不会改写当前学年流程实例
            GraduationWorkflowService.on_stage_approved(
                db, report.student_id, self.cfg.stage, cycle_id=getattr(report, "cycle_id", None))
        ProgressService.recompute(db, report.student_id, cycle_id=getattr(report, "cycle_id", None))

        student_user_id = db.execute(
            select(StudentProfile.user_id).where(StudentProfile.id == report.student_id)
        ).scalar()
        push_notification(db, user_id=student_user_id, type="REVIEW_RESULT",
                          title=f"{self.cfg.label}审核结果",
                          content=f"您的{self.cfg.label}已{_DECISION_CN[action]}。意见: {comment or '无'}",
                          business_type=self.cfg.business_type.value, business_id=report.id)
        db.commit()
        return self._list_item(db, report)

    # ---------- 审核历史 ----------

    def review_history(self, db: Session, student_id: int) -> list[dict]:
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
                reviewer = db.get(SysUser, r.reviewer_id)
                reviewer_name = reviewer.real_name if reviewer else None
            items.append({
                "action": r.action, "comment": r.comment,
                "before_status": r.before_status, "after_status": r.after_status,
                "reviewer_name": reviewer_name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return items
