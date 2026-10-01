"""Administrator progress uses real scoped records and sends real notifications."""
import json
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationProcess
from app.models.system import Notification, OperationLog
from app.models.user import StudentProfile, SysUser
from app.modules.progress.router import (AdminReminderRequest, admin_progress,
                                         admin_progress_summary, admin_send_reminders)


class AdminProgressTest(unittest.TestCase):
    def test_summary_filter_and_reminder(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            @event.listens_for(db, "before_flush")
            def assign_sqlite_ids(session, *_):
                for item in session.new:
                    if isinstance(item, Notification) and item.id is None:
                        item.id = 101
                    if isinstance(item, OperationLog) and item.id is None:
                        item.id = 201
            db.add_all([
                SysUser(id=11, username="s1", password_hash="x", real_name="学生甲", role="STUDENT"),
                SysUser(id=12, username="s2", password_hash="x", real_name="学生乙", role="STUDENT"),
                StudentProfile(id=21, user_id=11, student_no="001"),
                StudentProfile(id=22, user_id=12, student_no="002"),
                GraduationCycle(id=31, academic_year="2025-2026", student_id=21),
                GraduationProcess(id=41, student_id=21, cycle_id=31, current_stage="MIDTERM",
                                  risk_level="WARNING", progress_percent=40),
            ])
            db.commit()
            admin = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
            summary = json.loads(admin_progress_summary(academic_year="2025-2026", _=admin, db=db).body)["data"]
            self.assertEqual(summary["student_total"], 1)
            self.assertEqual(summary["warning"], 1)
            response = admin_progress(risk_level="WARNING", stage=None, academic_year="2025-2026",
                                      college_id=None, major_id=None, teacher_id=None, student="001",
                                      page=1, page_size=20, _=admin, db=db)
            self.assertEqual(json.loads(response.body)["data"]["total"], 1)
            with self.assertRaises(BusinessException):
                admin_send_reminders(AdminReminderRequest(student_ids=[999], title="进度提醒", content="请联系导师"),
                                     user=admin, db=db)
            result = admin_send_reminders(AdminReminderRequest(student_ids=[21], title="进度提醒", content="请联系导师"),
                                          user=admin, db=db)
            self.assertEqual(json.loads(result.body)["data"]["sent"], 1)
            self.assertEqual(db.query(Notification).one().user_id, 11)
            self.assertEqual(db.query(OperationLog).one().action, "SEND_REMINDER")
        engine.dispose()
