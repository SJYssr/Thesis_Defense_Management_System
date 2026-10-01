"""Teacher home activity uses the signed-in teacher's own records."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationProcess, GraduationTopic, TopicSelection
from app.models.user import StudentProfile, SysUser
from app.modules.dashboard.router import teacher_activity


class TeacherActivityTest(unittest.TestCase):
    def test_scope_and_real_stage_counts(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([
                SysUser(id=11, username="s1", password_hash="x", real_name="学生甲", role="STUDENT"),
                SysUser(id=12, username="s2", password_hash="x", real_name="学生乙", role="STUDENT"),
                StudentProfile(id=21, user_id=11, student_no="001"),
                StudentProfile(id=22, user_id=12, student_no="002"),
                GraduationTopic(id=31, teacher_id=1, title="本人题目", status="PUBLISHED"),
                GraduationTopic(id=32, teacher_id=2, title="其他教师题目", status="PUBLISHED"),
                TopicSelection(id=41, student_id=21, cycle_id=51, topic_id=31, teacher_id=1,
                               status="APPROVED", active_flag=1),
                TopicSelection(id=42, student_id=22, cycle_id=52, topic_id=32, teacher_id=2,
                               status="APPROVED", active_flag=1),
                GraduationProcess(id=61, student_id=21, cycle_id=51, topic_id=31, teacher_id=1,
                                  current_stage="MIDTERM"),
                GraduationProcess(id=62, student_id=22, cycle_id=52, topic_id=32, teacher_id=2,
                                  current_stage="OPENING"),
            ])
            db.commit()
            teacher = CurrentUser(id=1, username="t1", real_name="教师甲", role="TEACHER", profile_id=1)
            data = json.loads(teacher_activity(user=teacher, db=db).body)["data"]
            self.assertEqual(data["published_topics"], 1)
            self.assertEqual(data["student_stages"]["MIDTERM"], 1)
            self.assertEqual(data["student_stages"]["OPENING"], 0)
            self.assertEqual(len(data["tasks"]["doing"]), 1)
            self.assertEqual(data["tasks"]["doing"][0]["student"], "学生甲")
            self.assertNotIn("其他教师题目", json.dumps(data, ensure_ascii=False))
            with self.assertRaises(PermissionDeniedException):
                teacher_activity(user=CurrentUser(id=3, username="admin", real_name="管理员",
                                                  role="ADMIN", profile_id=None), db=db)
        engine.dispose()
