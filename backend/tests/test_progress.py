"""进度唯一真源 progress.compute() 回归用例(docs/refactor-plan.md §7, 验收标准 3)."""
import unittest

from sqlalchemy import BigInteger, Integer, create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationProcess,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.defense import ArchivePackage, DefenseResult
from app.models.enums import STAGE_WEIGHTS, ProcessStage
from app.shared.progress import MAX_PERCENT_WITHOUT_DEFENSE, ProgressService, ProgressSnapshot


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


def add_cycle(db, cycle_id=1, student_id=1, year="2026-2027", status="ACTIVE"):
    cycle = GraduationCycle(id=cycle_id, academic_year=year, student_id=student_id,
                            topic_id=1, teacher_id=1, status=status)
    db.add(cycle)
    db.flush()
    return cycle


def approve_all_stages(db, cycle_id=1, student_id=1):
    db.add(TopicSelection(id=1, student_id=student_id, cycle_id=cycle_id, topic_id=1,
                          teacher_id=1, status="APPROVED", active_flag=1))
    db.add(OpeningReport(id=1, student_id=student_id, cycle_id=cycle_id, topic_id=1, teacher_id=1,
                         title="开题", status="APPROVED"))
    db.add(MidtermReport(id=1, student_id=student_id, cycle_id=cycle_id, topic_id=1, teacher_id=1,
                         status="APPROVED"))
    db.add(FinalDefense(id=1, student_id=student_id, cycle_id=cycle_id, topic_id=1, teacher_id=1,
                        status="APPROVED"))
    db.add(GraduationMaterial(id=1, student_id=student_id, cycle_id=cycle_id, topic_id=1, teacher_id=1,
                              status="APPROVED"))
    db.flush()


class ProgressSnapshotTest(unittest.TestCase):
    def test_stage_weights_sum_to_100(self):
        self.assertEqual(sum(STAGE_WEIGHTS.values()), 100)

    def test_without_cycle_reports_selection_blocker(self):
        with new_session() as db:
            snapshot = ProgressService.compute(db, 1)
            self.assertIsInstance(snapshot, ProgressSnapshot)
            self.assertEqual((snapshot.percent, snapshot.stage, snapshot.completed), (0, "SELECTION", False))
            self.assertIn("选题未通过", snapshot.blockers)

    def test_empty_cycle_lists_every_blocker(self):
        with new_session() as db:
            cycle = add_cycle(db)
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertEqual(snapshot.percent, 0)
            self.assertEqual(snapshot.stage, ProcessStage.SELECTION.value)
            self.assertFalse(snapshot.completed)
            self.assertEqual(len(snapshot.blockers), 7)

    def test_partial_progress_follows_weights(self):
        with new_session() as db:
            cycle = add_cycle(db)
            db.add(TopicSelection(id=1, student_id=1, cycle_id=cycle.id, topic_id=1, teacher_id=1,
                                  status="APPROVED", active_flag=1))
            db.add(OpeningReport(id=1, student_id=1, cycle_id=cycle.id, topic_id=1, teacher_id=1,
                                 title="开题", status="APPROVED"))
            db.flush()
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertEqual(snapshot.percent, STAGE_WEIGHTS[ProcessStage.SELECTION]
                             + STAGE_WEIGHTS[ProcessStage.OPENING])
            self.assertEqual(snapshot.stage, ProcessStage.MIDTERM.value)
            self.assertIn("中期材料未通过", snapshot.blockers)

    def test_materials_approved_without_defense_capped_at_90(self):
        with new_session() as db:
            cycle = add_cycle(db)
            approve_all_stages(db)
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertFalse(snapshot.completed)
            self.assertEqual(snapshot.percent, MAX_PERCENT_WITHOUT_DEFENSE)
            self.assertEqual(snapshot.stage, ProcessStage.MATERIAL.value)
            self.assertIn("答辩结论未发布", snapshot.blockers)

    def test_defense_failed_capped_and_blocked(self):
        with new_session() as db:
            cycle = add_cycle(db)
            approve_all_stages(db)
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=cycle.id, stage="FINAL",
                                 status="PUBLISHED", decision="FAIL", score=51))
            db.add(ArchivePackage(id=1, cycle_id=cycle.id, result_id=1, status="ARCHIVED"))
            db.flush()
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertFalse(snapshot.completed)
            self.assertEqual(snapshot.percent, MAX_PERCENT_WITHOUT_DEFENSE)
            self.assertIn("答辩未通过", snapshot.blockers)

    def test_completed_requires_pass_and_archive(self):
        with new_session() as db:
            cycle = add_cycle(db)
            approve_all_stages(db)
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=cycle.id, stage="FINAL",
                                 status="PUBLISHED", decision="PASS", score=88))
            db.add(ArchivePackage(id=1, cycle_id=cycle.id, result_id=1, status="ARCHIVED"))
            db.flush()
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertTrue(snapshot.completed)
            self.assertEqual(snapshot.percent, 100)
            self.assertEqual(snapshot.stage, ProcessStage.COMPLETED.value)
            self.assertEqual(snapshot.blockers, [])

    def test_unpublished_result_does_not_count(self):
        with new_session() as db:
            cycle = add_cycle(db)
            approve_all_stages(db)
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=cycle.id, stage="FINAL",
                                 status="UNDER_REVIEW", decision="PASS", score=88))
            db.add(ArchivePackage(id=1, cycle_id=cycle.id, result_id=1, status="ARCHIVED"))
            db.flush()
            snapshot = ProgressService.compute(db, 1, cycle)
            self.assertFalse(snapshot.completed)
            self.assertIn("答辩结论未发布", snapshot.blockers)

    def test_explicit_cycle_beats_active_cycle(self):
        """A3 基础: 按传入 cycle 计算, 不回退"最新 ACTIVE". """
        with new_session() as db:
            old = add_cycle(db, cycle_id=1, year="2025-2026", status="COMPLETED")
            approve_all_stages(db, cycle_id=1)
            add_cycle(db, cycle_id=2, year="2026-2027", status="ACTIVE")
            old_snapshot = ProgressService.compute(db, 1, old)
            active_snapshot = ProgressService.compute(db, 1)
            self.assertEqual(old_snapshot.stage, ProcessStage.MATERIAL.value)
            self.assertEqual(old_snapshot.percent, MAX_PERCENT_WITHOUT_DEFENSE)
            self.assertEqual(active_snapshot.percent, 0)
            self.assertEqual(active_snapshot.stage, ProcessStage.SELECTION.value)

    def test_recompute_writes_process_for_given_cycle(self):
        with new_session() as db:
            add_cycle(db, cycle_id=1, year="2025-2026", status="COMPLETED")
            approve_all_stages(db, cycle_id=1)
            active = add_cycle(db, cycle_id=2, year="2026-2027", status="ACTIVE")
            active_process = GraduationProcess(id=1, student_id=1, cycle_id=active.id,
                                              current_stage="SELECTION", progress_percent=0,
                                              risk_level="NORMAL")
            db.add(active_process)
            db.flush()

            process = ProgressService.recompute(db, 1, cycle_id=1)
            self.assertEqual(process.cycle_id, 1)
            self.assertEqual((process.current_stage, process.progress_percent),
                             (ProcessStage.MATERIAL.value, MAX_PERCENT_WITHOUT_DEFENSE))

            db.flush()
            refreshed = db.execute(
                select(GraduationProcess).where(GraduationProcess.cycle_id == 2)
            ).scalar_one()
            self.assertEqual((refreshed.current_stage, refreshed.progress_percent), ("SELECTION", 0))

    def test_compat_compute_progress_returns_tuple(self):
        with new_session() as db:
            add_cycle(db)
            stage, percent = ProgressService.compute_progress(db, 1)
            self.assertEqual((stage, percent), (ProcessStage.SELECTION.value, 0))


if __name__ == "__main__":
    unittest.main()
