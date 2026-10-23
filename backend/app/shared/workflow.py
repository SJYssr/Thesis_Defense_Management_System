"""GraduationWorkflowService: 毕业设计全流程状态机(roadmap 第一优先级).

所有"某学生当前能否执行某阶段操作"的判断必须走这里,
后端接口强校验, 不依赖前端按钮 disabled。

进度百分比不在这里写: 唯一真源是 `progress.compute()`, 本模块只做阶段编排后调 `recompute`
(docs/refactor-plan.md §7, 缺陷 D4/D5)。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import StageValidationException
from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationProcess,
    GraduationTopic,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.enums import ProcessStage
from app.shared.events import now

STAGE_ORDER = [
    ProcessStage.SELECTION,
    ProcessStage.OPENING,
    ProcessStage.MIDTERM,
    ProcessStage.FINAL,
    ProcessStage.MATERIAL,
]

# 阶段推进用 dict 映射而非 list.index+1: 末尾(含 COMPLETED)返回 None, 不再 ValueError(D4)
NEXT_STAGE: dict[ProcessStage, ProcessStage | None] = {
    ProcessStage.SELECTION: ProcessStage.OPENING,
    ProcessStage.OPENING: ProcessStage.MIDTERM,
    ProcessStage.MIDTERM: ProcessStage.FINAL,
    ProcessStage.FINAL: ProcessStage.MATERIAL,
    ProcessStage.MATERIAL: None,
    ProcessStage.COMPLETED: None,
}


def next_stage(stage: ProcessStage | str | None) -> ProcessStage | None:
    """下一阶段; 末尾(MATERIAL/COMPLETED/未知)返回 None, 绝不抛异常."""
    if stage is None:
        return None
    try:
        key = stage if isinstance(stage, ProcessStage) else ProcessStage(stage)
    except ValueError:
        return None
    return NEXT_STAGE.get(key)


class GraduationWorkflowService:
    # ---------- 查询(全部按学生当前学年流程实例 cycle 归属) ----------

    @staticmethod
    def get_active_cycle(db: Session, student_id: int) -> GraduationCycle | None:
        from app.shared.cycle import get_active_cycle

        return get_active_cycle(db, student_id)

    @staticmethod
    def get_active_selection(db: Session, student_id: int) -> TopicSelection | None:
        cycle = GraduationWorkflowService.get_active_cycle(db, student_id)
        if cycle is None:
            return None
        return db.execute(
            select(TopicSelection).where(
                TopicSelection.cycle_id == cycle.id,
                TopicSelection.active_flag == 1,
            )
        ).scalar_one_or_none()

    @staticmethod
    def get_process(db: Session, student_id: int) -> GraduationProcess | None:
        cycle = GraduationWorkflowService.get_active_cycle(db, student_id)
        if cycle is None:
            return None
        return db.execute(
            select(GraduationProcess).where(GraduationProcess.cycle_id == cycle.id)
        ).scalar_one_or_none()

    @classmethod
    def get_stage_snapshot(cls, db: Session, student_id: int) -> dict:
        """一次查出学生五个阶段的状态, 供首页/进度页使用."""
        cycle = cls.get_active_cycle(db, student_id)
        if cycle is None:
            return {"selection": None, "opening": None, "midterm": None, "final": None, "material": None}
        selection = db.execute(
            select(TopicSelection).where(TopicSelection.cycle_id == cycle.id, TopicSelection.active_flag == 1)
        ).scalar_one_or_none()
        opening = db.execute(select(OpeningReport).where(OpeningReport.cycle_id == cycle.id)).scalar_one_or_none()
        midterm = db.execute(select(MidtermReport).where(MidtermReport.cycle_id == cycle.id)).scalar_one_or_none()
        final = db.execute(select(FinalDefense).where(FinalDefense.cycle_id == cycle.id)).scalar_one_or_none()
        material = db.execute(select(GraduationMaterial).where(GraduationMaterial.cycle_id == cycle.id)).scalar_one_or_none()
        return {
            "selection": selection,
            "opening": opening,
            "midterm": midterm,
            "final": final,
            "material": material,
        }

    # ---------- 阶段前置校验(接口层调用, 后端强控制) ----------

    @classmethod
    def require_can_apply_selection(cls, db: Session, student_id: int) -> None:
        sel = cls.get_active_selection(db, student_id)
        if sel is not None and sel.status == "PENDING":
            raise StageValidationException("您已有一个待审核的选题申请,请等待教师审核")
        if sel is not None and sel.status == "APPROVED":
            raise StageValidationException("您已通过选题,不能重复选题")

    @classmethod
    def require_selection_approved(cls, db: Session, student_id: int) -> TopicSelection:
        sel = cls.get_active_selection(db, student_id)
        if sel is None or sel.status != "APPROVED":
            raise StageValidationException("选题尚未通过审核,无法进行此操作")
        return sel

    @classmethod
    def require_can_submit_opening(cls, db: Session, student_id: int) -> TopicSelection:
        return cls.require_selection_approved(db, student_id)

    @classmethod
    def require_can_submit_midterm(cls, db: Session, student_id: int) -> None:
        cls.require_selection_approved(db, student_id)
        cycle = cls.get_active_cycle(db, student_id)
        opening = db.execute(
            select(OpeningReport).where(OpeningReport.cycle_id == cycle.id)
        ).scalar_one_or_none() if cycle else None
        if opening is None or opening.status != "APPROVED":
            raise StageValidationException("开题报告尚未通过,无法提交中期答辩材料")

    @classmethod
    def require_can_submit_final(cls, db: Session, student_id: int) -> None:
        cls.require_selection_approved(db, student_id)
        cycle = cls.get_active_cycle(db, student_id)
        midterm = db.execute(
            select(MidtermReport).where(MidtermReport.cycle_id == cycle.id)
        ).scalar_one_or_none() if cycle else None
        if midterm is None or midterm.status != "APPROVED":
            raise StageValidationException("中期报告尚未通过,无法提交终期答辩材料")

    @classmethod
    def require_can_submit_material(cls, db: Session, student_id: int) -> None:
        cls.require_selection_approved(db, student_id)
        cycle = cls.get_active_cycle(db, student_id)
        final = db.execute(
            select(FinalDefense).where(FinalDefense.cycle_id == cycle.id)
        ).scalar_one_or_none() if cycle else None
        if final is None or final.status != "APPROVED":
            raise StageValidationException("终期答辩尚未通过,无法提交毕业材料")

    # ---------- 阶段推进(审核通过时调用) ----------

    @classmethod
    def on_selection_approved(cls, db: Session, selection: TopicSelection) -> GraduationProcess:
        """选题通过 → 建立/推进当前学年流程实例(进度交由 recompute 统一计算)."""
        from app.shared.cycle import ensure_cycle
        from app.shared.progress import ProgressService

        cycle = ensure_cycle(db, selection.student_id, selection.topic_id, selection.teacher_id)
        selection.cycle_id = cycle.id
        process = cls._get_or_create_process(db, cycle, selection.topic_id, selection.teacher_id)
        if not process.started_at:
            process.started_at = now()
        ProgressService.recompute(db, selection.student_id, process=process, cycle_id=cycle.id)
        return process

    @classmethod
    def on_stage_approved(cls, db: Session, student_id: int, stage: ProcessStage,
                          cycle_id: int | None = None) -> GraduationProcess:
        """开题/中期/终期/材料审核通过后推进流程.

        cycle_id 显式给出时以该学年实例为准(A3): 审核历史学年材料不得改写当前学年进度。
        进度/阶段/完成态全部由 progress.compute() 决定, 本方法不再手写 progress_percent(D5)。
        """
        from app.shared.cycle import ensure_cycle
        from app.shared.progress import ProgressService

        cycle = db.get(GraduationCycle, cycle_id) if cycle_id is not None else cls.get_active_cycle(db, student_id)
        if cycle is None:
            cycle = ensure_cycle(db, student_id)
        process = cls._get_or_create_process(db, cycle)
        ProgressService.recompute(db, student_id, process=process, cycle_id=cycle.id)
        return process

    @staticmethod
    def _get_or_create_process(db: Session, cycle: GraduationCycle | None,
                               topic_id: int | None = None, teacher_id: int | None = None) -> GraduationProcess:
        process = None
        if cycle is not None:
            process = db.execute(
                select(GraduationProcess).where(GraduationProcess.cycle_id == cycle.id)
            ).scalar_one_or_none()
        if process is None:
            process = GraduationProcess(
                student_id=cycle.student_id if cycle else None,
                cycle_id=cycle.id if cycle else None,
                topic_id=topic_id if topic_id is not None else (cycle.topic_id if cycle else None),
                teacher_id=teacher_id if teacher_id is not None else (cycle.teacher_id if cycle else None),
                current_stage=ProcessStage.SELECTION.value,
                started_at=now(),
            )
            db.add(process)
            db.flush()
        else:
            if topic_id is not None:
                process.topic_id = topic_id
            if teacher_id is not None:
                process.teacher_id = teacher_id
        return process

    # ---------- 教师审核权限辅助 ----------

    @staticmethod
    def ensure_reviewable_selection(db: Session, selection_id: int, teacher_profile_id: int) -> TopicSelection:
        sel = db.execute(select(TopicSelection).where(TopicSelection.id == selection_id)).scalar_one_or_none()
        if sel is None:
            from app.core.exceptions import ResourceNotFoundException

            raise ResourceNotFoundException("选题记录不存在")
        if sel.teacher_id != teacher_profile_id:
            from app.core.exceptions import PermissionDeniedException

            raise PermissionDeniedException("只能审核自己题目的选题申请")
        if sel.status != "PENDING":
            from app.core.exceptions import BusinessException

            raise BusinessException("该选题申请已被处理")
        return sel

    @staticmethod
    def get_topic_with_lock(db: Session, topic_id: int) -> GraduationTopic:
        """SELECT ... FOR UPDATE: 审核通过选题时锁定题目行, 防止超额通过(roadmap 八十三)."""
        return db.execute(
            select(GraduationTopic).where(GraduationTopic.id == topic_id).with_for_update()
        ).scalar_one()
