"""任务提醒必须真实落库，且遵守任务归属、范围与学生状态。"""
import json
import unittest

from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.permissions import CurrentUser
from app.models.business import OpeningReport
from app.models.defense import StageTask
from app.models.system import Notification, OperationLog, Reminder
from app.models.user import SysUser
from app.modules.task.router import TaskReminder, remind_task
from tests.test_material_version import TEACHER, new_engine, seed


class TaskReminderTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.task = StageTask(academic_year="2026-2027", stage="OPENING", title="开题任务",
                              status="PUBLISHED", created_by=TEACHER.id)
        self.db.add(self.task)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def remind(self, user=TEACHER):
        return json.loads(remind_task(self.task.id, TaskReminder(content="请补齐开题报告并及时提交"),
                                     user, self.db).body)["data"]

    def test_delivery_persists_reminder_notification_and_audit(self):
        self.assertEqual(self.remind()["sent"], 1)
        self.db.expire_all()
        reminder = self.db.query(Reminder).one()
        notice = self.db.query(Notification).one()
        self.assertEqual((reminder.student_id, reminder.receiver_id), (1, 2))
        self.assertEqual(notice.content, reminder.content)
        self.assertEqual((notice.business_type, notice.business_id), ("STAGE_TASK", self.task.id))
        audit = self.db.query(OperationLog).filter_by(action="REMIND_TASK").one()
        self.assertEqual(json.loads(audit.detail)["sent"], 1)

    def test_submitted_and_approved_students_are_not_reminded(self):
        report = OpeningReport(student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                               title="开题", status="SUBMITTED")
        self.db.add(report)
        self.db.commit()
        for status in ("SUBMITTED", "APPROVED"):
            report.status = status  # 夹具: 学生当前无需再次操作。
            self.db.commit()
            self.assertEqual(self.remind()["sent"], 0)
        self.assertEqual(self.db.query(Notification).count(), 0)

    def test_out_of_scope_or_disabled_account_is_skipped(self):
        from app.models.business import GraduationCycle

        self.task.college_id = 99
        self.db.commit()
        self.assertEqual(self.remind()["sent"], 0)
        self.task.college_id = None
        self.db.get(GraduationCycle, 1).teacher_id = 2
        self.db.commit()
        self.assertEqual(self.remind()["sent"], 0)
        self.db.get(GraduationCycle, 1).teacher_id = 1
        self.db.get(SysUser, 2).status = 0
        self.db.commit()
        self.assertEqual(self.remind()["sent"], 0)

    def test_other_teacher_cannot_send_for_task(self):
        other = CurrentUser(id=99, username="other", real_name="其他教师", role="TEACHER", profile_id=2)
        with self.assertRaises(PermissionDeniedException):
            self.remind(other)
        self.assertEqual(self.db.query(Notification).count(), 0)

    def test_unpublished_task_cannot_send(self):
        self.task.status = "DRAFT"
        self.db.commit()
        with self.assertRaises(BusinessException):
            self.remind()
        self.assertEqual(self.db.query(Notification).count(), 0)
