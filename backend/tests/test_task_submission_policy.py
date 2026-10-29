"""任务必须能在选题前看到，时间与适用范围必须在提交端强制生效。"""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.models.business import GraduationSchedule
from app.models.defense import StageTask, StageTaskVersion
from app.models.system import ReviewRecord
from app.modules.opening.router import OPENING_CONFIG
from app.modules.stages.engine import StageEngine
from app.modules.task.router import TaskCreate, TaskPatch, create_task, my_tasks, update_task
from app.shared.task_policy import ensure_task_submission_open
from tests.test_material_version import STUDENT, TEACHER, new_engine, seed


class TaskSubmissionPolicyTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.db.add(GraduationSchedule(academic_year="2026-2027", is_active=1))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def task(self, **overrides):
        data = dict(academic_year="2026-2027", stage="OPENING", title="开题任务", status="PUBLISHED",
                    opens_at=datetime.now() - timedelta(days=2), due_at=datetime.now() + timedelta(days=2),
                    late_policy="DENY")
        data.update(overrides)
        task = StageTask(**data)
        self.db.add(task)
        self.db.commit()
        return task

    def test_student_without_cycle_sees_published_selection_task(self):
        from app.core.permissions import CurrentUser
        from app.models.user import StudentProfile, SysUser

        self.db.add(SysUser(id=3, username="new", real_name="新学生", role="STUDENT", password_hash="x"))
        self.db.add(StudentProfile(id=2, user_id=3, student_no="new", college_id=1))
        self.db.commit()
        task = self.task(stage="SELECTION", college_id=1)
        self.task(stage="SELECTION", college_id=99)
        user = CurrentUser(id=3, username="new", real_name="新学生", role="STUDENT", profile_id=2)
        data = json.loads(my_tasks(user, self.db).body)["data"]
        self.assertEqual(data["academic_year"], "2026-2027")
        self.assertEqual([t["id"] for t in data["tasks"]], [task.id])
        self.assertEqual(data["tasks"][0]["material_status"], "NOT_SELECTED")

    def test_future_task_blocks_submit_but_preserves_draft(self):
        self.task(opens_at=datetime.now() + timedelta(days=1))
        engine = StageEngine(OPENING_CONFIG)
        content = {name: "完整开题报告内容" for name in OPENING_CONFIG.required_fields}
        engine.student_save(self.db, 1, content)
        with self.assertRaises(BusinessException) as ctx:
            engine.student_submit(self.db, 1, STUDENT.id)
        self.assertEqual(ctx.exception.code, 40003)
        self.assertIn("尚未开始", ctx.exception.message)
        self.assertEqual(engine.get_report(self.db, 1).status, "DRAFT")
        self.assertEqual(self.db.query(ReviewRecord).count(), 0)

    def test_expired_deny_blocks_and_allow_passes(self):
        task = self.task(due_at=datetime.now() - timedelta(days=1))
        with self.assertRaises(BusinessException):
            ensure_task_submission_open(self.db, 1, "OPENING")
        task.late_policy = "ALLOW"
        self.db.commit()
        ensure_task_submission_open(self.db, 1, "OPENING")

    def test_other_scope_year_stage_and_draft_do_not_block(self):
        for extra in ({"college_id": 99}, {"academic_year": "2025-2026"},
                      {"stage": "MIDTERM"}, {"status": "DRAFT"}, {"status": "CLOSED"}):
            self.task(due_at=datetime.now() - timedelta(days=1), **extra)
        ensure_task_submission_open(self.db, 1, "OPENING")

    def test_invalid_window_rejected_without_creating_task(self):
        with self.assertRaises(BusinessException):
            create_task(TaskCreate(academic_year="2026-2027", stage="OPENING", title="错误时间",
                                  opens_at="2026-11-02T12:00:00+08:00", due_at="2026-11-01T12:00:00"),
                        TEACHER, self.db)
        self.assertEqual(self.db.query(StageTask).count(), 0)

    def test_update_persists_year_stage_and_normalizes_time(self):
        task = self.task(created_by=TEACHER.id)
        update_task(task.id, TaskPatch(academic_year="2027-2028", stage="MIDTERM", title="中期任务",
                                      opens_at="2027-01-01T12:00:00+08:00",
                                      due_at="2027-02-01T12:00:00+08:00"), TEACHER, self.db)
        self.db.expire_all()
        self.assertEqual((task.academic_year, task.stage), ("2027-2028", "MIDTERM"))
        self.assertIsNone(task.opens_at.tzinfo)
        snapshot = json.loads(self.db.query(StageTaskVersion).one().snapshot_json)
        self.assertEqual(snapshot["stage"], "OPENING")
        self.assertEqual(snapshot["academic_year"], "2026-2027")
