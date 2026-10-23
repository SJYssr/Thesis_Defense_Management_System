"""学年流程实例 cycle 唯一性与结论读取回归用例(§5, 验收标准 2 / 缺陷 D1·D2)."""
import logging
import unittest

from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationSchedule
from app.models.defense import DefenseResult
from app.shared.cycle import ensure_cycle, formal_defense_snapshot, get_active_cycle, list_cycles


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


class EnsureCycleTest(unittest.TestCase):
    def test_new_cycle_archives_previous_active(self):
        with new_session() as db:
            old = GraduationCycle(id=1, academic_year="2025-2026", student_id=1,
                                  topic_id=1, teacher_id=1, status="ACTIVE")
            db.add(old)
            db.flush()

            cycle = ensure_cycle(db, student_id=1, academic_year="2026-2027")
            db.flush()
            self.assertEqual(cycle.status, "ACTIVE")
            self.assertEqual(db.get(GraduationCycle, 1).status, "COMPLETED")
            self.assertEqual([c.id for c in list_cycles(db, 1)], [cycle.id, 1])

    def test_same_year_is_idempotent(self):
        with new_session() as db:
            first = ensure_cycle(db, student_id=1, academic_year="2026-2027")
            second = ensure_cycle(db, student_id=1, academic_year="2026-2027")
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(list_cycles(db, 1)), 1)

    def test_reusing_archived_year_reactivates_and_demotes_others(self):
        with new_session() as db:
            old = GraduationCycle(id=1, academic_year="2025-2026", student_id=1, status="COMPLETED")
            current = GraduationCycle(id=2, academic_year="2026-2027", student_id=1, status="ACTIVE")
            db.add_all([old, current])
            db.flush()

            cycle = ensure_cycle(db, student_id=1, academic_year="2025-2026")
            db.flush()
            self.assertEqual(cycle.id, 1)
            self.assertEqual(cycle.status, "ACTIVE")
            self.assertEqual(db.get(GraduationCycle, 2).status, "COMPLETED")

    def test_academic_year_from_active_schedule(self):
        with new_session() as db:
            db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
            db.flush()
            cycle = ensure_cycle(db, student_id=1)
            self.assertEqual(cycle.academic_year, "2026-2027")


class ActiveCycleTest(unittest.TestCase):
    def test_multiple_active_logs_error_and_takes_newest(self):
        with new_session() as db:
            db.add_all([
                GraduationCycle(id=1, academic_year="2025-2026", student_id=1, status="ACTIVE"),
                GraduationCycle(id=2, academic_year="2026-2027", student_id=1, status="ACTIVE"),
            ])
            db.flush()
            with self.assertLogs("app.cycle", level=logging.ERROR) as captured:
                active = get_active_cycle(db, 1)
            self.assertEqual(active.id, 2)
            self.assertIn("存在 2 条 ACTIVE", captured.output[0])

    def test_no_active_returns_none(self):
        with new_session() as db:
            self.assertIsNone(get_active_cycle(db, 1))


class FormalDefenseSnapshotTest(unittest.TestCase):
    def test_snapshot_reads_active_cycle_only(self):
        with new_session() as db:
            db.add_all([
                GraduationCycle(id=1, academic_year="2025-2026", student_id=1, status="COMPLETED"),
                GraduationCycle(id=2, academic_year="2026-2027", student_id=1, status="ACTIVE"),
            ])
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL",
                                 status="PUBLISHED", decision="PASS", score=88))
            db.flush()

            snapshot = formal_defense_snapshot(db, 1)
            self.assertEqual(snapshot["cycle_id"], 2)
            self.assertEqual(snapshot["academic_year"], "2026-2027")
            self.assertEqual(snapshot["status"], "NOT_SCHEDULED")
            self.assertIsNone(snapshot["decision"])

            db.add(DefenseResult(id=2, assignment_id=2, cycle_id=2, stage="FINAL",
                                 status="PUBLISHED", decision="PASS", score=91))
            db.flush()
            snapshot = formal_defense_snapshot(db, 1)
            self.assertEqual((snapshot["status"], snapshot["decision"], snapshot["score"]),
                             ("PUBLISHED", "PASS", 91))

    def test_academic_year_argument_no_longer_reroutes_query(self):
        with new_session() as db:
            db.add(GraduationCycle(id=1, academic_year="2026-2027", student_id=1, status="ACTIVE"))
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL",
                                 status="PUBLISHED", decision="PASS", score=80))
            db.flush()
            snapshot = formal_defense_snapshot(db, 1, academic_year="2019-2020")
            self.assertEqual(snapshot["cycle_id"], 1)
            self.assertEqual(snapshot["academic_year"], "2026-2027")
            self.assertEqual(snapshot["score"], 80)

    def test_list_cycles_newest_first(self):
        with new_session() as db:
            db.add_all([
                GraduationCycle(id=1, academic_year="2024-2025", student_id=1, status="COMPLETED"),
                GraduationCycle(id=2, academic_year="2025-2026", student_id=1, status="COMPLETED"),
                GraduationCycle(id=3, academic_year="2026-2027", student_id=1, status="ACTIVE"),
            ])
            db.flush()
            self.assertEqual([c.academic_year for c in list_cycles(db, 1)],
                             ["2026-2027", "2025-2026", "2024-2025"])


if __name__ == "__main__":
    unittest.main()
