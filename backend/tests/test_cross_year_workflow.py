"""换届申请、历史审核与风险重算必须保持流程实例归属。"""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.business import GraduationCycle, GraduationSchedule, TopicSelection
from app.modules.selection.router import my_selections
from app.shared.progress import ProgressService
from app.shared.workflow import GraduationWorkflowService
from tests.test_material_version import STUDENT, new_engine, seed


class CrossYearWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.old = self.db.get(GraduationCycle, 1)
        self.old.academic_year = "2025-2026"
        self.db.add(GraduationSchedule(academic_year="2026-2027", is_active=1))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_previous_year_approval_does_not_block_new_year_application(self):
        GraduationWorkflowService.require_can_apply_selection(self.db, 1)

    def test_review_old_selection_keeps_cycle_and_active_year(self):
        self.old.status = "COMPLETED"
        active = GraduationCycle(student_id=1, academic_year="2026-2027", status="ACTIVE")
        self.db.add(active)
        self.db.commit()
        selection = self.db.get(TopicSelection, 1)
        process = GraduationWorkflowService.on_selection_approved(self.db, selection)
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(selection.cycle_id, 1)
        self.assertEqual(process.cycle_id, 1)
        self.assertEqual(self.db.get(GraduationCycle, 1).status, "COMPLETED")
        self.assertEqual(self.db.get(GraduationCycle, active.id).status, "ACTIVE")

    def test_risk_uses_target_cycle_stage_and_its_own_schedule(self):
        # 历史学年已选题, 当前应补开题; 新学年尚未选题且选题未截止。
        self.old.status = "COMPLETED"
        self.db.add(GraduationCycle(student_id=1, academic_year="2026-2027", status="ACTIVE"))
        self.db.add(GraduationSchedule(
            academic_year="2025-2026", is_active=0,
            opening_end=datetime.now() - timedelta(days=10)))
        self.db.commit()
        risk, reason = ProgressService.compute_risk(self.db, 1, self.old)
        self.assertEqual(risk, "HIGH")
        self.assertIn("开题", reason)
        self.assertEqual(ProgressService.compute_risk(self.db, 1), ("NORMAL", None))

    def test_selection_history_marks_only_active_cycle_as_current(self):
        self.old.status = "COMPLETED"
        self.db.add(GraduationCycle(student_id=1, academic_year="2026-2027", status="ACTIVE"))
        self.db.commit()
        items = json.loads(my_selections(STUDENT, self.db).body)["data"]
        self.assertFalse(items[0]["is_current"])
        self.assertEqual(items[0]["cycle_id"], 1)
