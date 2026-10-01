"""Administrator statistics use published formal results only."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.defense import DefenseResult
from app.modules.dashboard.router import admin_analytics


class AdminAnalyticsTest(unittest.TestCase):
    def test_formal_result_excludes_unpublished(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([
                DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL", score=86,
                              decision="PASS", status="PUBLISHED"),
                DefenseResult(id=2, assignment_id=2, cycle_id=2, stage="FINAL", score=99,
                              decision="PASS", status="PENDING"),
                DefenseResult(id=3, assignment_id=3, cycle_id=3, stage="MIDTERM", score=95,
                              decision="PASS", status="PUBLISHED"),
            ])
            db.commit()
            admin = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
            data = json.loads(admin_analytics(user=admin, db=db).body)["data"]
            self.assertEqual(data["defense_total"], 1)
            self.assertEqual({item["name"]: item["value"] for item in data["defense_results"]}["良好"], 1)
            teacher = CurrentUser(id=2, username="teacher", real_name="教师", role="TEACHER", profile_id=1)
            with self.assertRaises(PermissionDeniedException):
                admin_analytics(user=teacher, db=db)
        engine.dispose()
