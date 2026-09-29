"""GraduationWorkflowService: 毕业设计全流程状态机(roadmap 第一优先级).

所有"某学生当前能否执行某阶段操作"的判断必须走这里,
后端接口强校验, 不依赖前端按钮 disabled.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import StageValidationException
from app.models.business import (
    FinalDefense,
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


class GraduationWorkflowService:
    # ---------- 查询 ----------

    @staticmethod
    def get_active_selection(db: Session, student_id: int) -> TopicSelection | None:
        return db.execute(
            select(TopicSelection).where(
                TopicSelection.student_id == student_id,
                TopicSelection.active_flag == 1,
            )
        ).scalar_one_or_none()

    @staticmethod
    def get_process(db: Session, student_id: int) -> GraduationProcess | None:
        return db.execute(
            select(GraduationProcess).where(GraduationProcess.student_id == student_id)
        ).scalar_one_or_none()

    @classmethod
    def get_stage_snapshot(cls, db: Session, student_id: int) -> dict:
        """一次查出学生五个阶段的状态, 供首页/进度页使用."""
        selection = cls.get_active_selection(db, student_id)
        opening = db.execute(select(OpeningReport).where(OpeningReport.student_id == student_id)).scalar_one_or_none()
        midterm = db.execute(select(MidtermReport).where(MidtermReport.student_id == student_id)).scalar_one_or_none()
        final = db.execute(select(FinalDefense).where(FinalDefense.student_id == student_id)).scalar_one_or_none()
        material = db.execute(select(GraduationMaterial).where(GraduationMaterial.student_id == student_id)).scalar_one_or_none()
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
        opening = db.execute(select(OpeningReport).where(OpeningReport.student_id == student_id)).scalar_one_or_none()
        if opening is None or opening.status != "APPROVED":
            raise StageValidationException("开题报告尚未通过,无法提交中期答辩材料")

    @classmethod
    def require_can_submit_final(cls, db: Session, student_id: int) -> None:
        cls.require_selection_approved(db, student_id)
        midterm = db.execute(select(MidtermReport).where(MidtermReport.student_id == student_id)).scalar_one_or_none()
        if midterm is None or midterm.status != "APPROVED":
            raise StageValidationException("中期报告尚未通过,无法提交终期答辩材料")

    @classmethod
    def require_can_submit_material(cls, db: Session, student_id: int) -> None:
        cls.require_selection_approved(db, student_id)
        final = db.execute(select(FinalDefense).where(FinalDefense.student_id == student_id)).scalar_one_or_none()
        if final is None or final.status != "APPROVED":
            raise StageValidationException("终期答辩尚未通过,无法提交毕业材料")

    # ---------- 阶段推进(审核通过时调用) ----------

    @classmethod
    def on_selection_approved(cls, db: Session, selection: TopicSelection) -> GraduationProcess:
        """选题通过 → 建立/推进毕业流程."""
        process = cls.get_process(db, selection.student_id)
        if process is None:
            process = GraduationProcess(
                student_id=selection.student_id,
                topic_id=selection.topic_id,
                teacher_id=selection.teacher_id,
                current_stage=ProcessStage.OPENING.value,
                progress_percent=10,
                started_at=now(),
            )
            db.add(process)
            db.flush()  # 立即可见, 防止后续 recompute 重复创建
        return process

    @classmethod
    def on_stage_approved(cls, db: Session, student_id: int, stage: ProcessStage) -> GraduationProcess:
        """开题/中期/终期/材料审核通过后推进流程: 百分比按权重累加, 阶段前移."""
        from app.shared.progress import ProgressService

        process = cls.get_process(db, student_id)
        if process is None:
            process = GraduationProcess(student_id=student_id, started_at=now())
            db.add(process)
            db.flush()
        if stage == ProcessStage.MATERIAL:
            process.current_stage = ProcessStage.COMPLETED.value
            process.completed_at = now()
        else:
            next_stage = STAGE_ORDER[STAGE_ORDER.index(stage) + 1]
            process.current_stage = next_stage.value
        ProgressService.recompute(db, student_id, process=process)
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
