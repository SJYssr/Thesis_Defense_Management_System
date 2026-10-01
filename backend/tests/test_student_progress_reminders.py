"""Student progress exposes only that student's recent teacher reminders."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import Reminder
from app.modules.progress.router import my_progress


class StudentProgressReminderTest(unittest.TestCase):
    def test_recent_reminders_are_student_scoped_and_ordered(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([
                Reminder(id=i, sender_id=8, receiver_id=10, student_id=1,
                         title=f"提醒{i}", content=f"内容{i}")
                for i in range(1, 7)
            ])
            db.add(Reminder(id=7, sender_id=8, receiver_id=11, student_id=2,
                            title="其他学生提醒", content="不得泄露"))
            db.commit()
            student = CurrentUser(id=10, username="student", real_name="学生", role="STUDENT", profile_id=1)
            with patch("app.modules.progress.router.ProgressService.recompute"), \
                 patch("app.modules.progress.router.GraduationWorkflowService.get_process", return_value=None), \
                 patch("app.modules.progress.router.GraduationWorkflowService.get_active_selection", return_value=None), \
                 patch("app.modules.progress.router._student_base", return_value={"student_id": 1}), \
                 patch("app.modules.progress.router._timeline", return_value=[]), \
                 patch("app.modules.progress.router.formal_defense_snapshot", return_value={}), \
                 patch("app.modules.progress.router.ProgressService.stage_deadline_info", return_value={}):
                data = json.loads(my_progress(user=student, db=db).body)["data"]
            self.assertEqual([r["id"] for r in data["reminders"]], [6, 5, 4, 3, 2])
            self.assertNotIn("其他学生提醒", json.dumps(data, ensure_ascii=False))
        engine.dispose()
