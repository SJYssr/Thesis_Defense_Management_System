"""ProgressService: 确定性规则引擎计算进度/风险(roadmap 二十三/五十).

进度百分比与"是否完成"只有一个真源: `ProgressService.compute()`(docs/refactor-plan.md §7).
AI 只负责解释风险, 不负责编造进度。

完成判定(D3): 材料 APPROVED 且正式结论 PUBLISHED+decision=PASS 且归档 APPROVED;
材料齐全但答辩未过时 percent 封顶 90 且 blockers 含"答辩未通过"。
"""
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationProcess,
    GraduationSchedule,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.defense import ArchivePackage, DefenseResult
from app.models.enums import (
    STAGE_WEIGHTS,
    ArchiveStatus,
    ProcessStage,
)
from app.models.system import ReviewRecord
from app.shared.events import now

STAGE_LABELS = {
    ProcessStage.SELECTION: "选题",
    ProcessStage.OPENING: "开题报告",
    ProcessStage.MIDTERM: "中期材料",
    ProcessStage.FINAL: "终期材料",
    ProcessStage.MATERIAL: "毕业材料",
    ProcessStage.COMPLETED: "材料审核完成",
}

# 材料齐全但答辩未过/未归档时的进度上限(§7)
MAX_PERCENT_WITHOUT_DEFENSE = 90

BLOCKER_SELECTION = "选题未通过"
BLOCKER_OPENING = "开题报告未通过"
BLOCKER_MIDTERM = "中期材料未通过"
BLOCKER_FINAL = "终期材料未通过"
BLOCKER_MATERIAL = "毕业材料未通过"
BLOCKER_RESULT_MISSING = "答辩结论未发布"
BLOCKER_DEFENSE_FAILED = "答辩未通过"
BLOCKER_ARCHIVE = "归档材料未批准"

# 归档"已批准"的唯一真源: ArchiveStatus 枚举只有 PENDING/ARCHIVED/REVOKED, 从来没有 APPROVED。
# 归档包落库终态就是 ARCHIVED(模型默认值/seed/dashboard/统计全部按 ARCHIVED 统计),
# 故这里只认枚举值, 不再接受状态机 ARCHIVE 块里那个不存在的 "APPROVED" 字面值(P2-B 验收4)。
ARCHIVE_APPROVED_STATUSES = {ArchiveStatus.ARCHIVED.value}


@dataclass
class ProgressSnapshot:
    """一次计算的结果快照: 进度/阶段/是否毕业/未达成项."""

    percent: int = 0
    stage: str = ProcessStage.SELECTION.value
    completed: bool = False
    blockers: list[str] = field(default_factory=list)


def _approved(obj) -> bool:
    return obj is not None and obj.status == "APPROVED"


class ProgressService:
    @staticmethod
    def get_active_schedule(db: Session) -> GraduationSchedule | None:
        return db.execute(
            select(GraduationSchedule).where(GraduationSchedule.is_active == 1).order_by(GraduationSchedule.id.desc())
        ).scalars().first()

    @classmethod
    def ensure_selection_window_open(cls, db: Session) -> None:
        """选题时间窗校验(roadmap 八十五章"允许选题时间"): 当前激活学年的选题窗口外禁止提交."""
        from app.core.exceptions import BusinessException

        schedule = cls.get_active_schedule(db)
        if schedule is None:
            return
        current = now()
        if schedule.selection_start and current < schedule.selection_start:
            raise BusinessException(
                f"选题尚未开始, 开始时间 {schedule.selection_start:%Y-%m-%d %H:%M}", 40003)
        if schedule.selection_end and current > schedule.selection_end:
            raise BusinessException(
                f"选题已截止, 截止时间 {schedule.selection_end:%Y-%m-%d %H:%M}", 40003)

    # ---------- 唯一真源 ----------

    @staticmethod
    def resolve_cycle(db: Session, student_id: int, cycle: GraduationCycle | int | None = None) -> GraduationCycle | None:
        """接受 cycle 对象 / cycle_id / None(回退当前 ACTIVE), 便于显式指定学年(A3)."""
        from app.shared.cycle import get_active_cycle

        if isinstance(cycle, GraduationCycle):
            return cycle
        if cycle is not None:
            return db.get(GraduationCycle, int(cycle))
        return get_active_cycle(db, student_id)

    @classmethod
    def compute(cls, db: Session, student_id: int,
                cycle: GraduationCycle | int | None = None) -> ProgressSnapshot:
        """按 cycle 归属计算进度快照(不读"最新 ACTIVE", 避免写错学年实例, 缺陷 D3/A3)."""
        cycle_obj = cls.resolve_cycle(db, student_id, cycle)
        if cycle_obj is None:
            return ProgressSnapshot(percent=0, stage=ProcessStage.SELECTION.value,
                                    completed=False, blockers=[BLOCKER_SELECTION])

        cycle_id = cycle_obj.id
        sel = db.execute(
            select(TopicSelection).where(TopicSelection.cycle_id == cycle_id, TopicSelection.active_flag == 1)
        ).scalar_one_or_none()
        opening = db.execute(select(OpeningReport).where(OpeningReport.cycle_id == cycle_id)).scalar_one_or_none()
        midterm = db.execute(select(MidtermReport).where(MidtermReport.cycle_id == cycle_id)).scalar_one_or_none()
        final = db.execute(select(FinalDefense).where(FinalDefense.cycle_id == cycle_id)).scalar_one_or_none()
        material = db.execute(select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle_id)).scalar_one_or_none()

        # 正式结论: 只认人工发布(PUBLISHED)的 FINAL 结论
        result = db.execute(
            select(DefenseResult)
            .where(DefenseResult.cycle_id == cycle_id, DefenseResult.stage == "FINAL",
                   DefenseResult.status == "PUBLISHED")
            .order_by(DefenseResult.id.desc())
        ).scalars().first()
        archive = db.execute(
            select(ArchivePackage).where(ArchivePackage.cycle_id == cycle_id).order_by(ArchivePackage.id.desc())
        ).scalars().first()

        steps = [
            (ProcessStage.SELECTION, sel, BLOCKER_SELECTION),
            (ProcessStage.OPENING, opening, BLOCKER_OPENING),
            (ProcessStage.MIDTERM, midterm, BLOCKER_MIDTERM),
            (ProcessStage.FINAL, final, BLOCKER_FINAL),
            (ProcessStage.MATERIAL, material, BLOCKER_MATERIAL),
        ]
        percent = 0
        blockers: list[str] = []
        stage = ProcessStage.SELECTION.value
        current_open = False
        for step_stage, obj, blocker in steps:
            if _approved(obj):
                percent += STAGE_WEIGHTS[step_stage]
                continue
            blockers.append(blocker)
            if not current_open:
                stage = step_stage.value
                current_open = True

        defense_passed = result is not None and result.decision == "PASS"
        if result is None:
            blockers.append(BLOCKER_RESULT_MISSING)
        elif not defense_passed:
            blockers.append(BLOCKER_DEFENSE_FAILED)
        archive_approved = archive is not None and archive.status in ARCHIVE_APPROVED_STATUSES
        if not archive_approved:
            blockers.append(BLOCKER_ARCHIVE)

        completed = _approved(material) and defense_passed and archive_approved
        if completed:
            stage = ProcessStage.COMPLETED.value
            percent = 100
        else:
            # 未完成(含"材料齐全但答辩未过"): 进度不被判满
            percent = min(percent, MAX_PERCENT_WITHOUT_DEFENSE)
            if not current_open:
                # 材料已全部通过, 但毕业判定未达成: 停留在材料阶段
                stage = ProcessStage.MATERIAL.value
        return ProgressSnapshot(percent=percent, stage=stage, completed=completed, blockers=blockers)

    @classmethod
    def compute_progress(cls, db: Session, student_id: int) -> tuple[str, int]:
        """兼容旧调用: (当前阶段, 进度百分比)."""
        snapshot = cls.compute(db, student_id)
        return snapshot.stage, snapshot.percent

    # ---------- 风险 ----------

    @classmethod
    def compute_risk(cls, db: Session, student_id: int,
                     cycle: GraduationCycle | int | None = None) -> tuple[str, str | None]:
        """规则引擎判定风险: 截止时间逾期 + 连续驳回次数."""
        from app.shared.cycle import get_active_cycle

        cycle_obj = cls.resolve_cycle(db, student_id, cycle)
        stage, _ = cls.compute_progress(db, student_id)
        schedule = cls.get_active_schedule(db)
        reasons: list[str] = []
        level = "NORMAL"

        if schedule is not None and stage != ProcessStage.COMPLETED.value:
            deadline_map = {
                ProcessStage.SELECTION.value: schedule.selection_end,
                ProcessStage.OPENING.value: schedule.opening_end,
                ProcessStage.MIDTERM.value: schedule.midterm_end,
                ProcessStage.FINAL.value: schedule.final_end,
                ProcessStage.MATERIAL.value: schedule.material_end,
            }
            deadline = deadline_map.get(stage)
            if deadline is not None:
                overdue_days = (now() - deadline).days
                if overdue_days >= 7:
                    level = "HIGH"
                    reasons.append(f"{STAGE_LABELS[ProcessStage(stage)]}已逾期{overdue_days}天")
                elif overdue_days >= 1:
                    if level != "HIGH":
                        level = "WARNING"
                    reasons.append(f"{STAGE_LABELS[ProcessStage(stage)]}已逾期{overdue_days}天")

        # 同一阶段被驳回 >= 3 次 → 警告(仅统计当前学年流程实例的记录)
        business_map = {
            ProcessStage.OPENING.value: ("OPENING_REPORT", OpeningReport),
            ProcessStage.MIDTERM.value: ("MIDTERM_REPORT", MidtermReport),
            ProcessStage.FINAL.value: ("FINAL_DEFENSE", FinalDefense),
            ProcessStage.MATERIAL.value: ("GRADUATION_MATERIAL", GraduationMaterial),
        }
        entry = business_map.get(stage)
        if entry and cycle_obj is not None:
            bt, model = entry
            report = db.execute(select(model).where(model.cycle_id == cycle_obj.id)).scalar_one_or_none()
            if report is not None:
                reject_count = db.execute(
                    select(func.count(ReviewRecord.id)).where(
                        ReviewRecord.business_type == bt,
                        ReviewRecord.business_id == report.id,
                        ReviewRecord.action.in_(["REJECT", "RETURN"]),
                    )
                ).scalar() or 0
                if reject_count >= 3 and level == "NORMAL":
                    level = "WARNING"
                    reasons.append(f"{STAGE_LABELS[ProcessStage(stage)]}已被驳回{reject_count}次")

        return level, "; ".join(reasons) if reasons else None

    # ---------- 落库 ----------

    @classmethod
    def recompute(cls, db: Session, student_id: int, process: GraduationProcess | None = None,
                  cycle_id: int | None = None) -> GraduationProcess:
        """重算并落库学生的进度与风险.

        cycle_id 显式给出时以它为准(A3: 审核历史学年材料不得改写当前学年进度);
        未给出时回退到 process.cycle_id / 当前 ACTIVE 流程实例。
        """
        target_cycle = cls.resolve_cycle(db, student_id, cycle_id if cycle_id is not None else process.cycle_id
                                         if process is not None else None)
        target_cycle_id = target_cycle.id if target_cycle is not None else None

        if process is None:
            if target_cycle_id is not None:
                process = db.execute(
                    select(GraduationProcess).where(GraduationProcess.cycle_id == target_cycle_id)
                ).scalar_one_or_none()
            if process is None:
                process = GraduationProcess(student_id=student_id, cycle_id=target_cycle_id,
                                            started_at=now())
                db.add(process)
        elif target_cycle_id is not None and process.cycle_id != target_cycle_id:
            process.cycle_id = target_cycle_id

        snapshot = cls.compute(db, student_id, target_cycle)
        risk, reason = cls.compute_risk(db, student_id, target_cycle)
        process.current_stage = snapshot.stage
        process.progress_percent = snapshot.percent
        process.risk_level = risk
        process.risk_reason = reason
        if snapshot.completed:
            if not process.completed_at:
                process.completed_at = now()
        else:
            process.completed_at = None
        return process

    @staticmethod
    def stage_deadline_info(db: Session, stage: str) -> dict:
        """当前阶段截止信息(距截止天数等), 供首页/AI分析使用."""
        schedule = ProgressService.get_active_schedule(db)
        if schedule is None:
            return {"has_schedule": False}
        deadline_map = {
            ProcessStage.SELECTION.value: schedule.selection_end,
            ProcessStage.OPENING.value: schedule.opening_end,
            ProcessStage.MIDTERM.value: schedule.midterm_end,
            ProcessStage.FINAL.value: schedule.final_end,
            ProcessStage.MATERIAL.value: schedule.material_end,
        }
        deadline = deadline_map.get(stage)
        if deadline is None:
            return {"has_schedule": True, "deadline": None, "days_left": None}
        days_left = (deadline - now()).days
        return {"has_schedule": True, "deadline": deadline.isoformat(), "days_left": days_left}
