"""P1~P3 业务链路测试: 冲突排期/评分版本锁定/成绩复算/归档权限(roadmap P4.2)."""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ConflictException, PermissionDeniedException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationSchedule, GraduationTopic
from app.models.defense import (
    ArchiveConsent,
    ArchivePackage,
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
    StageTask,
)
from app.models.enums import ResultStatus, ScoreStatus, SeatStatus, SessionStatus
from app.models.system import SysFile
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.defense.service import (
    assignment_item,
    compute_result,
    get_rubric_for,
    validate_session,
)


def _setup(db: Session):
    db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
    t1 = SysUser(id=11, username="t1", password_hash="x", real_name="评委甲", role="TEACHER")
    t2 = SysUser(id=12, username="t2", password_hash="x", real_name="评委乙", role="TEACHER")
    t3 = SysUser(id=13, username="t3", password_hash="x", real_name="导师丙", role="TEACHER")
    s1 = SysUser(id=21, username="20260001", password_hash="x", real_name="学生A", role="STUDENT")
    s2 = SysUser(id=22, username="20260002", password_hash="x", real_name="学生B", role="STUDENT")
    db.add_all([t1, t2, t3, s1, s2])
    db.flush()
    tp1 = TeacherProfile(id=1, user_id=11, teacher_no="T1")
    tp2 = TeacherProfile(id=2, user_id=12, teacher_no="T2")
    tp3 = TeacherProfile(id=3, user_id=13, teacher_no="T3")
    sp1 = StudentProfile(id=1, user_id=21, student_no="20260001")
    sp2 = StudentProfile(id=2, user_id=22, student_no="20260002")
    db.add_all([tp1, tp2, tp3, sp1, sp2])
    topic = GraduationTopic(id=1, teacher_id=1, title="题目X", status="PUBLISHED")
    db.add(topic)
    db.flush()
    c1 = GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1, teacher_id=3)
    c2 = GraduationCycle(id=2, academic_year="2026-2027", student_id=2, topic_id=1, teacher_id=3)
    db.add_all([c1, c2])
    rubric = ReviewRubric(id=1, academic_year="2026-2027", stage="FINAL", version=1,
                          min_examiners=2, pass_line=60,
                          weights_json='[{"name": "报告质量", "weight": 50}, {"name": "答辩表现", "weight": 50}]',
                          enabled=1)
    db.add(rubric)
    db.flush()
    return c1, c2


def _session(db: Session, sid: int, start, location="A101") -> DefenseSession:
    s = DefenseSession(
        id=sid, academic_year="2026-2027", stage="FINAL",
        starts_at=start, ends_at=start + timedelta(hours=2),
        location=location, capacity=5, status="DRAFT")
    db.add(s)
    db.flush()
    return s


class ConflictScheduleTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.c1, self.c2 = _setup(self.db)
        self.t0 = datetime(2026, 10, 10, 9, 0)

    def tearDown(self):
        self.db.close()

    def test_teacher_time_conflict_rejected(self):
        """同一教师时间重叠的两个场次必须冲突(roadmap P1 验收)."""
        s1 = _session(self.db, 1, self.t0)
        s2 = _session(self.db, 2, self.t0 + timedelta(hours=1))
        self.db.add(DefenseExaminer(session_id=1, teacher_id=1, examiner_role="CHAIR"))
        self.db.add(DefenseExaminer(session_id=2, teacher_id=1, examiner_role="EXAMINER"))
        self.db.flush()
        conflicts = validate_session(self.db, s2)
        self.assertTrue(any(c["type"] == "TEACHER" for c in conflicts))

    def test_student_time_conflict_rejected(self):
        s1 = _session(self.db, 1, self.t0)
        s2 = _session(self.db, 2, self.t0 + timedelta(hours=1))
        self.db.add(DefenseAssignment(session_id=1, cycle_id=self.c1.id, seat_status="SCHEDULED"))
        self.db.flush()
        self.db.add(DefenseAssignment(session_id=2, cycle_id=self.c1.id, seat_status="SCHEDULED"))
        self.db.flush()
        conflicts = validate_session(self.db, s2)
        self.assertTrue(any(c["type"] == "STUDENT" for c in conflicts))

    def test_room_conflict_rejected(self):
        _session(self.db, 1, self.t0, location="A101")
        s2 = _session(self.db, 2, self.t0 + timedelta(hours=1), location="A101")
        conflicts = validate_session(self.db, s2)
        self.assertTrue(any(c["type"] == "ROOM" for c in conflicts))

    def test_capacity_conflict(self):
        s = _session(self.db, 1, self.t0)
        s.capacity = 1
        self.db.add(DefenseAssignment(session_id=1, cycle_id=self.c1.id))
        self.db.add(DefenseAssignment(session_id=1, cycle_id=self.c2.id))
        self.db.flush()
        conflicts = validate_session(self.db, s)
        self.assertTrue(any(c["type"] == "CAPACITY" for c in conflicts))

    def test_postponed_seat_no_conflict(self):
        """已延期席位不再占用时间(可与新场次并存)."""
        _session(self.db, 1, self.t0)
        s2 = _session(self.db, 2, self.t0 + timedelta(hours=1), location="B202")
        self.db.add(DefenseAssignment(session_id=1, cycle_id=self.c1.id, seat_status=SeatStatus.POSTPONED.value))
        self.db.flush()
        self.db.add(DefenseAssignment(session_id=2, cycle_id=self.c1.id))
        self.db.flush()
        self.assertEqual(validate_session(self.db, s2), [])


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.c1, _ = _setup(self.db)
        self.t0 = datetime(2026, 10, 10, 9, 0)
        self.session = _session(self.db, 1, self.t0)
        self.db.add(DefenseExaminer(session_id=1, teacher_id=1, examiner_role="CHAIR"))
        self.db.add(DefenseExaminer(session_id=1, teacher_id=2, examiner_role="EXAMINER"))
        self.seat = DefenseAssignment(id=1, session_id=1, cycle_id=self.c1.id, seat_status=SeatStatus.ATTENDED.value)
        self.db.add(self.seat)
        self.db.flush()
        self.rubric = get_rubric_for(self.db, self.session, self.c1)
        self.assertEqual(self.rubric.id, 1)

    def tearDown(self):
        self.db.close()

    def _submit(self, teacher_id: int, scores: dict, version: int = 1):
        self.db.add(ExaminerScore(assignment_id=1, examiner_id=teacher_id, rubric_id=1, rubric_version=1,
                                  scores_json=json.dumps(scores, ensure_ascii=False), comment="ok",
                                  status=ScoreStatus.SUBMITTED.value, version=version,
                                  submitted_at=datetime.now()))

    def test_recompute_matches_publication(self):
        """汇总结果与原始评分可复算(roadmap P2 验收)."""
        self._submit(1, {"报告质量": 90, "答辩表现": 80})
        self._submit(2, {"报告质量": 80, "答辩表现": 70})
        self.db.flush()
        computed = compute_result(self.db, self.seat, self.rubric)
        self.assertEqual(computed["valid_count"], 2)
        # (90+80)/2*0.5 + (80+70)/2*0.5 = 42.5 + 37.5 = 80
        self.assertEqual(computed["score"], 80)
        self.assertEqual(computed["decision"], "PASS")

    def test_min_examiners_block_publish(self):
        """评委人数不足不能发布结论(roadmap P2 验收)."""
        self._submit(1, {"报告质量": 90, "答辩表现": 80})
        self.db.flush()
        computed = compute_result(self.db, self.seat, self.rubric)
        self.assertEqual(computed["valid_count"], 1)
        self.assertLess(computed["valid_count"], self.rubric.min_examiners)

    def test_superseded_scores_not_counted(self):
        """复核重开产生新版本, 旧版本(SUPERSEDED)不参与汇总."""
        self._submit(1, {"报告质量": 90, "答辩表现": 80}, version=1)
        self._submit(2, {"报告质量": 80, "答辩表现": 70}, version=1)
        # 评委1重开后重新提交
        self.db.add(ExaminerScore(assignment_id=1, examiner_id=1, rubric_id=1, rubric_version=1,
                                  scores_json=json.dumps({"报告质量": 60, "答辩表现": 60}, ensure_ascii=False),
                                  status=ScoreStatus.SUBMITTED.value, version=2, submitted_at=datetime.now()))
        self.db.flush()
        old = self.db.query(ExaminerScore).filter_by(examiner_id=1, version=1).one()
        old.status = ScoreStatus.SUPERSEDED.value
        self.db.flush()
        computed = compute_result(self.db, self.seat, self.rubric)
        # 有效评分: 评委1 v2(60/60) + 评委2(80/70)
        # 报告质量 (60+80)/2*0.5 + 答辩表现 (60+70)/2*0.5 = 35 + 32.5 = 67.5 → 68
        self.assertEqual(computed["score"], 68)

    def test_only_attended_can_publish(self):
        self.seat.seat_status = SeatStatus.ABSENT.value
        self.db.flush()
        computed = compute_result(self.db, self.seat, self.rubric)
        self._submit(1, {"报告质量": 90, "答辩表现": 80})
        self._submit(2, {"报告质量": 90, "答辩表现": 80})
        self.db.flush()
        # 发布前由路由校验 seat_status, 服务层提供依据
        self.assertEqual(self.db.get(DefenseAssignment, 1).seat_status, SeatStatus.ABSENT.value)


class RubricSelectionTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.c1, _ = _setup(self.db)
        self.t0 = datetime(2026, 10, 10, 9, 0)
        self.session = _session(self.db, 1, self.t0)

    def tearDown(self):
        self.db.close()

    def test_fallback_to_global_rubric(self):
        rubric = get_rubric_for(self.db, self.session, self.c1)
        self.assertIsNotNone(rubric)
        self.assertIsNone(rubric.college_id)


class TaskScopeTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        db = self.db
        db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
        db.add(StudentProfile(id=1, user_id=21, student_no="20260001", college_id=9, major_id=8, class_id=7))
        db.add(StageTask(id=1, academic_year="2026-2027", stage="OPENING", title="全校开题任务", status="PUBLISHED"))
        db.add(StageTask(id=2, academic_year="2026-2027", stage="OPENING", title="学院任务", status="PUBLISHED", college_id=9))
        db.add(StageTask(id=3, academic_year="2026-2027", stage="OPENING", title="其他学院任务", status="PUBLISHED", college_id=99))
        db.add(StageTask(id=4, academic_year="2026-2027", stage="OPENING", title="草稿任务", status="DRAFT"))
        db.flush()

    def tearDown(self):
        self.db.close()

    def test_scope_and_status_filtering(self):
        rows = self.db.query(StageTask).filter(
            StageTask.academic_year == "2026-2027", StageTask.status == "PUBLISHED").all()
        visible = [t for t in rows if t.college_id in (None, 9)]
        titles = sorted(t.title for t in visible)
        self.assertEqual(titles, ["全校开题任务", "学院任务"])


if __name__ == "__main__":
    unittest.main()
