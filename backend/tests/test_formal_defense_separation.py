"""旧终期材料审核不得成为正式答辩结论(docs §1.1: final_defense.status/score 仅是材料数据)."""
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import FinalDefense, GraduationSchedule
from app.models.defense import DefenseAssignment, DefenseResult, DefenseSession
from app.shared.cycle import ensure_cycle, formal_defense_snapshot


class FormalDefenseSeparationTest(unittest.TestCase):
    def test_material_approval_does_not_publish_formal_result(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
            db.add(FinalDefense(id=1, student_id=1, topic_id=1, teacher_id=1,
                                status="APPROVED", score=92))
            db.flush()
            cycle = ensure_cycle(db, student_id=1, topic_id=1, teacher_id=1)
            self.assertEqual(cycle.academic_year, "2026-2027")
            self.assertEqual(ensure_cycle(db, 1).id, cycle.id)
            # 材料已通过, 但未安排场次、未发布结论 → 正式结论必须为 NOT_SCHEDULED
            self.assertEqual(formal_defense_snapshot(db, 1)["status"], "NOT_SCHEDULED")

            session = DefenseSession(id=1, academic_year="2026-2027", stage="FINAL",
                                     starts_at=datetime(2026, 9, 1, 9), ends_at=datetime(2026, 9, 1, 12),
                                     status="PUBLISHED", capacity=5)
            db.add(session)
            db.flush()
            seat = DefenseAssignment(id=1, session_id=1, cycle_id=cycle.id, seat_status="SCHEDULED")
            db.add(seat)
            db.flush()
            result = DefenseResult(id=1, assignment_id=1, cycle_id=cycle.id, stage="FINAL",
                                   status="UNDER_REVIEW", decision="PASS", score=88)
            db.add(result)
            db.flush()
            pending = formal_defense_snapshot(db, 1)
            self.assertIsNone(pending["decision"])
            self.assertIsNone(pending["score"])

            result.status = "PUBLISHED"
            result.published_at = datetime(2026, 9, 30, 10, 0)
            published = formal_defense_snapshot(db, 1)
            self.assertEqual((published["decision"], published["score"]), ("PASS", 88))


if __name__ == "__main__":
    unittest.main()
