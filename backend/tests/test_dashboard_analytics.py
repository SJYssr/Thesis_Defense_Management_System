"""Administrator statistics use published formal results only."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationSchedule
from app.models.defense import DefenseResult
from app.modules.dashboard.router import admin_analytics


class AdminAnalyticsTest(unittest.TestCase):
    def test_formal_result_excludes_unpublished(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            # D73: 统计口径已收窄到当前学年, 结论必须挂在当前学年的 cycle 上才计入
            db.add_all([
                GraduationSchedule(id=1, academic_year="2025-2026", is_active=1),
                GraduationCycle(id=1, academic_year="2025-2026", student_id=1, status="ACTIVE"),
                GraduationCycle(id=2, academic_year="2025-2026", student_id=2, status="ACTIVE"),
                GraduationCycle(id=3, academic_year="2025-2026", student_id=3, status="ACTIVE"),
                GraduationCycle(id=9, academic_year="2024-2025", student_id=9, status="COMPLETED"),
                DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL", score=86,
                              decision="PASS", status="PUBLISHED"),
                DefenseResult(id=2, assignment_id=2, cycle_id=2, stage="FINAL", score=99,
                              decision="PASS", status="PENDING"),
                DefenseResult(id=3, assignment_id=3, cycle_id=3, stage="MIDTERM", score=95,
                              decision="PASS", status="PUBLISHED"),
                # 往届已发布 PASS: 不得计入当前学年统计(证伪点)
                DefenseResult(id=4, assignment_id=4, cycle_id=9, stage="FINAL", score=90,
                              decision="PASS", status="PUBLISHED"),
            ])
            db.commit()
            admin = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
            data = json.loads(admin_analytics(user=admin, db=db).body)["data"]
            self.assertEqual(data["defense_total"], 1)
            counts = {item["name"]: item["value"] for item in data["defense_results"]}
            self.assertEqual(counts["良好"], 1)
            self.assertEqual(counts["优秀"], 0, "往届 cycle 的已发布结论不得混入当前学年口径")
            self.assertEqual(data["scope_map"]["defense_results"], "current_year")
            teacher = CurrentUser(id=2, username="teacher", real_name="教师", role="TEACHER", profile_id=1)
            with self.assertRaises(PermissionDeniedException):
                admin_analytics(user=teacher, db=db)
        engine.dispose()
