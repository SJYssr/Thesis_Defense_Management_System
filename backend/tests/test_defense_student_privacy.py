"""学生场次查询不得泄露同场学生或尚未发布的排期。"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException, ResourceNotFoundException
from app.db.base import Base
from app.models.defense import DefenseAssignment, DefenseExaminer
from app.modules.defense.router import get_session, my_sessions
from tests.test_defense_flow import _session, _setup


class StudentSessionPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        _setup(self.db)
        self.session = _session(self.db, 1, datetime(2026, 10, 10, 9))
        self.session.status = "PUBLISHED"
        self.db.add_all([
            DefenseAssignment(id=1, session_id=1, cycle_id=1),
            DefenseAssignment(id=2, session_id=1, cycle_id=2, remark="他人的私人备注"),
            DefenseExaminer(session_id=1, teacher_id=1, examiner_role="CHAIR"),
        ])
        self.db.commit()
        self.student = SimpleNamespace(id=21, role="STUDENT", profile_id=1)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def assert_private(self, item):
        self.assertEqual([a["student_id"] for a in item["assignments"]], [1])
        self.assertNotIn("他人的私人备注", json.dumps(item, ensure_ascii=False))
        self.assertEqual(item["examiners"][0]["teacher_name"], "评委甲")

    def test_mine_and_detail_only_include_own_assignment(self):
        mine = json.loads(my_sessions(self.student, self.db).body)["data"]
        self.assertEqual(len(mine), 1)
        self.assert_private(mine[0])
        self.assert_private(json.loads(get_session(1, self.student, self.db).body)["data"])

    def test_draft_hidden_from_mine_and_detail(self):
        self.session.status = "DRAFT"
        self.db.commit()
        self.assertEqual(json.loads(my_sessions(self.student, self.db).body)["data"], [])
        with self.assertRaises(ResourceNotFoundException):
            get_session(1, self.student, self.db)

    def test_admin_and_assigned_examiner_keep_full_roster(self):
        for user in (SimpleNamespace(role="ADMIN"), SimpleNamespace(role="TEACHER", profile_id=1)):
            item = json.loads(get_session(1, user, self.db).body)["data"]
            self.assertEqual(len(item["assignments"]), 2)

    def test_unassigned_student_denied(self):
        with self.assertRaises(PermissionDeniedException):
            get_session(1, SimpleNamespace(role="STUDENT", profile_id=99), self.db)
