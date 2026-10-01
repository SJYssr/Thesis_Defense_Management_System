"""ProgressService: 确定性规则引擎计算进度/风险(roadmap 二十三/五十).

进度百分比完全由业务状态决定, AI 只负责解释风险, 不负责编造进度.
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import (
    FinalDefense,
    GraduationMaterial,
    GraduationProcess,
    GraduationSchedule,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.enums import ProcessStage, STAGE_WEIGHTS
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


def _approved(obj) -> bool:
    return obj is not None and obj.status == "APPROVED"


class ProgressService:
    @staticmethod
    def get_active_schedule(db: Session) -> GraduationSchedule | None:
        return db.execute(
            select(GraduationSchedule).where(GraduationSchedule.is_active == 1).order_by(GraduationSchedule.id.desc())
        ).scalars().first()

    @classmethod
    def compute_progress(cls, db: Session, student_id: int) -> tuple[str, int]:
        """返回 (当前阶段, 进度百分比). 全部按学生当前学年流程实例(cycle)归属."""
        from app.shared.cycle import get_active_cycle

        cycle = get_active_cycle(db, student_id)
        if cycle is None:
            return ProcessStage.SELECTION.value, 0
        sel = db.execute(
            select(TopicSelection).where(TopicSelection.cycle_id == cycle.id, TopicSelection.active_flag == 1)
        ).scalar_one_or_none()
        opening = db.execute(select(OpeningReport).where(OpeningReport.cycle_id == cycle.id)).scalar_one_or_none()
        midterm = db.execute(select(MidtermReport).where(MidtermReport.cycle_id == cycle.id)).scalar_one_or_none()
        final = db.execute(select(FinalDefense).where(FinalDefense.cycle_id == cycle.id)).scalar_one_or_none()
        material = db.execute(select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle.id)).scalar_one_or_none()

        if _approved(material):
            return ProcessStage.COMPLETED.value, 100
        if _approved(final):
            return ProcessStage.MATERIAL.value, 10 + 20 + 25 + 25
        if _approved(midterm):
            return ProcessStage.FINAL.value, 10 + 20 + 25
        if _approved(opening):
            return ProcessStage.MIDTERM.value, 10 + 20
        if _approved(sel):
            return ProcessStage.OPENING.value, STAGE_WEIGHTS[ProcessStage.SELECTION]
        return ProcessStage.SELECTION.value, 0

    @classmethod
    def compute_risk(cls, db: Session, student_id: int) -> tuple[str, str | None]:
        """规则引擎判定风险: 截止时间逾期 + 连续驳回次数."""
        from app.shared.cycle import get_active_cycle

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
        cycle = get_active_cycle(db, student_id)
        business_map = {
            ProcessStage.OPENING.value: ("OPENING_REPORT", OpeningReport),
            ProcessStage.MIDTERM.value: ("MIDTERM_REPORT", MidtermReport),
            ProcessStage.FINAL.value: ("FINAL_DEFENSE", FinalDefense),
            ProcessStage.MATERIAL.value: ("GRADUATION_MATERIAL", GraduationMaterial),
        }
        entry = business_map.get(stage)
        if entry and cycle is not None:
            bt, model = entry
            report = db.execute(select(model).where(model.cycle_id == cycle.id)).scalar_one_or_none()
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

    @classmethod
    def recompute(cls, db: Session, student_id: int, process: GraduationProcess | None = None) -> GraduationProcess:
        """重算并落库学生的进度与风险."""
        from app.shared.cycle import get_active_cycle
        from app.shared.workflow import GraduationWorkflowService

        if process is None:
            process = GraduationWorkflowService.get_process(db, student_id)
            if process is None:
                cycle = get_active_cycle(db, student_id)
                process = GraduationProcess(student_id=student_id, cycle_id=cycle.id if cycle else None,
                                            started_at=now())
                db.add(process)
        stage, percent = cls.compute_progress(db, student_id)
        risk, reason = cls.compute_risk(db, student_id)
        process.current_stage = stage
        process.progress_percent = percent
        process.risk_level = risk
        process.risk_reason = reason
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
