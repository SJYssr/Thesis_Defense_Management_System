"""P2-A 答辩主链状态机测试: 场次/席位/正式结论统一走 shared.state_machine.transition.

覆盖验收:
1. 场次 DRAFT -> PUBLISHED -> IN_PROGRESS -> FINISHED, 取消走 cancel; 每个动作的非法转移 -> 40003
2. 席位 SCHEDULED -> ATTENDED/ABSENT, 且出席是结论计算的前置条件
4. 正式结论 PENDING -> READY -> PUBLISHED -> WITHDRAWN; 未 calculate 就 publish -> 40003
5. 评分单轨: 只写 DefenseResult.score, FinalDefense.score 保持 None
6. 并发: 真库(MySQL)两个连接同时发布同一结论, 只有一个成功
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ConflictException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import FinalDefense, GraduationCycle, GraduationSchedule, GraduationTopic
from app.models.defense import (
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
)
from app.models.enums import ResultStatus, ScoreStatus, SeatStatus, SessionStatus
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.defense import service
from app.modules.defense.seat_state import mark_seat
from app.modules.defense.service import (
    calculate_result,
    cancel_session,
    compute_result,
    finish_session,
    get_rubric_for,
    publish_result,
    publish_session,
    start_session,
    withdraw_result,
)
from app.shared.state_machine import SESSION, allowed_actions, can

ADMIN = SimpleNamespace(id=1, username="admin", role="ADMIN")
INVALID = 40003

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYSQL_SERVER = os.getenv("P2A_MYSQL_SERVER", "mysql+pymysql://root:root123456@127.0.0.1:3306/")
MYSQL_DSN = os.getenv("P2A_MYSQL_DSN", "mysql+pymysql://root:root123456@127.0.0.1:3306/graduation_p2a")


# ---------------- fixtures ----------------

def _setup(db: Session, pass_line: int = 60, min_examiners: int = 2) -> ReviewRubric:
    db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
    db.add_all([
        SysUser(id=11, username="t1", password_hash="x", real_name="评委甲", role="TEACHER"),
        SysUser(id=12, username="t2", password_hash="x", real_name="评委乙", role="TEACHER"),
        SysUser(id=13, username="t3", password_hash="x", real_name="导师丙", role="TEACHER"),
        SysUser(id=21, username="20260001", password_hash="x", real_name="学生A", role="STUDENT"),
        SysUser(id=22, username="20260002", password_hash="x", real_name="学生B", role="STUDENT"),
    ])
    db.flush()
    db.add_all([
        TeacherProfile(id=1, user_id=11, teacher_no="T1"),
        TeacherProfile(id=2, user_id=12, teacher_no="T2"),
        TeacherProfile(id=3, user_id=13, teacher_no="T3"),
        StudentProfile(id=1, user_id=21, student_no="20260001"),
        StudentProfile(id=2, user_id=22, student_no="20260002"),
    ])
    db.flush()  # MySQL 有外键约束: 必须先落 profile 再插 topic/cycle
    db.add(GraduationTopic(id=1, teacher_id=1, title="题目X", status="PUBLISHED"))
    db.flush()
    db.add_all([
        GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1, teacher_id=3),
        GraduationCycle(id=2, academic_year="2026-2027", student_id=2, topic_id=1, teacher_id=3),
    ])
    db.flush()
    rubric = ReviewRubric(
        id=1, academic_year="2026-2027", stage="FINAL", version=1,
        min_examiners=min_examiners, pass_line=pass_line,
        weights_json=json.dumps([{"name": "报告质量", "weight": 50}, {"name": "答辩表现", "weight": 50}],
                                ensure_ascii=False), enabled=1)
    db.add(rubric)
    db.flush()
    return rubric


def _session(db: Session, sid: int = 1, start: datetime | None = None,
             status: str = SessionStatus.DRAFT.value) -> DefenseSession:
    start = start or (datetime.now() - timedelta(hours=1))
    s = DefenseSession(id=sid, academic_year="2026-2027", stage="FINAL", title=f"场次{sid}",
                       starts_at=start, ends_at=start + timedelta(hours=2),
                       location="A101", capacity=5, status=status)
    db.add(s)
    db.flush()
    return s


def _examiners(db: Session, sid: int = 1):
    db.add_all([
        DefenseExaminer(session_id=sid, teacher_id=1, examiner_role="CHAIR"),
        DefenseExaminer(session_id=sid, teacher_id=2, examiner_role="EXAMINER"),
    ])
    db.flush()


def _assignment(db: Session, sid: int = 1, cycle_id: int = 1,
                seat_status: str = SeatStatus.SCHEDULED.value) -> DefenseAssignment:
    a = DefenseAssignment(id=1, session_id=sid, cycle_id=cycle_id, seat_status=seat_status)
    db.add(a)
    db.flush()
    return a


def _submit(db: Session, assignment_id: int, examiner_id: int, scores: dict,
            version: int = 1) -> ExaminerScore:
    row = ExaminerScore(assignment_id=assignment_id, examiner_id=examiner_id, version=version,
                        status=ScoreStatus.SUBMITTED.value,
                        scores_json=json.dumps(scores, ensure_ascii=False), submitted_at=datetime.now())
    db.add(row)
    db.flush()
    return row


def _sqlite_session() -> tuple:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


# ---------------- 1. 场次生命周期 ----------------

class SessionStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.db = _sqlite_session()
        self.rubric = _setup(self.db)

    def tearDown(self):
        self.db.close()

    def test_full_lifecycle_draft_published_in_progress_finished(self):
        s = _session(self.db)
        _examiners(self.db)
        self.assertEqual(s.status, SessionStatus.DRAFT.value)
        publish_session(self.db, s.id, ADMIN)
        self.db.flush()
        self.assertEqual(s.status, SessionStatus.PUBLISHED.value)
        rev = s.revision
        self.assertGreaterEqual(rev, 1)
        start_session(self.db, s.id, ADMIN)
        self.assertEqual(s.status, SessionStatus.IN_PROGRESS.value)
        finish_session(self.db, s.id, ADMIN)
        self.assertEqual(s.status, SessionStatus.FINISHED.value)

    def test_start_before_publish_rejected(self):
        """未发布就 start -> 40003(核心验收)."""
        s = _session(self.db)
        with self.assertRaises(BusinessException) as ctx:
            start_session(self.db, s.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertEqual(s.status, SessionStatus.DRAFT.value)

    def test_start_before_starts_at_rejected(self):
        s = _session(self.db, start=datetime.now() + timedelta(hours=3))
        _examiners(self.db)
        publish_session(self.db, s.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            start_session(self.db, s.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("未到开始时间", ctx.exception.message)
        self.assertEqual(s.status, SessionStatus.PUBLISHED.value)

    def test_publish_twice_rejected(self):
        s = _session(self.db)
        _examiners(self.db)
        publish_session(self.db, s.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            publish_session(self.db, s.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("不允许动作 publish", ctx.exception.message)

    def test_finish_before_start_rejected(self):
        s = _session(self.db)
        _examiners(self.db)
        publish_session(self.db, s.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            finish_session(self.db, s.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertEqual(s.status, SessionStatus.PUBLISHED.value)

    def test_cancel_requires_reason(self):
        s = _session(self.db)
        _examiners(self.db)
        publish_session(self.db, s.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            cancel_session(self.db, s.id, ADMIN, None)
        self.assertEqual(ctx.exception.code, INVALID)
        cancel_session(self.db, s.id, ADMIN, "评委临时冲突")
        self.assertEqual(s.status, SessionStatus.CANCELLED.value)

    def test_cancel_after_finished_rejected(self):
        s = _session(self.db)
        _examiners(self.db)
        publish_session(self.db, s.id, ADMIN)
        start_session(self.db, s.id, ADMIN)
        finish_session(self.db, s.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            cancel_session(self.db, s.id, ADMIN, "已结束还想取消")
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertEqual(s.status, SessionStatus.FINISHED.value)

    def test_publish_requires_examiners_and_chair(self):
        s = _session(self.db)
        with self.assertRaises(BusinessException) as ctx:
            publish_session(self.db, s.id, ADMIN)
        self.assertIn("分配评委", ctx.exception.message)
        self.db.add(DefenseExaminer(session_id=s.id, teacher_id=1, examiner_role="EXAMINER"))
        self.db.flush()
        with self.assertRaises(BusinessException) as ctx2:
            publish_session(self.db, s.id, ADMIN)
        self.assertIn("CHAIR", ctx2.exception.message)
        self.assertEqual(s.status, SessionStatus.DRAFT.value)

    def test_transition_table_is_the_only_authority(self):
        """状态机自证: DRAFT 只允许 publish/cancel, IN_PROGRESS 才允许 finish."""
        draft_actions = set(allowed_actions(SESSION, SessionStatus.DRAFT.value))
        self.assertIn("publish", draft_actions)
        self.assertIn("cancel", draft_actions)
        self.assertNotIn("start", draft_actions)
        self.assertNotIn("finish", draft_actions)
        self.assertEqual(allowed_actions(SESSION, SessionStatus.FINISHED.value), [])


# ---------------- 2. 席位 ----------------

class SeatStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.db = _sqlite_session()
        self.rubric = _setup(self.db)
        self.session = _session(self.db)
        _examiners(self.db)
        self.assignment = _assignment(self.db)

    def tearDown(self):
        self.db.close()

    def _published(self):
        publish_session(self.db, self.session.id, ADMIN)
        self.db.flush()

    def test_attend_from_published_session(self):
        self._published()
        mark_seat(self.db, self.assignment, SeatStatus.ATTENDED.value, ADMIN)
        self.db.flush()
        self.assertEqual(self.assignment.seat_status, SeatStatus.ATTENDED.value)

    def test_attend_illegal_when_draft(self):
        with self.assertRaises(BusinessException) as ctx:
            mark_seat(self.db, self.assignment, SeatStatus.ATTENDED.value, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertEqual(self.assignment.seat_status, SeatStatus.SCHEDULED.value)

    def test_attend_twice_rejected(self):
        self._published()
        mark_seat(self.db, self.assignment, SeatStatus.ATTENDED.value, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            mark_seat(self.db, self.assignment, SeatStatus.ATTENDED.value, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("不允许动作 attend", ctx.exception.message)

    def test_absent_requires_finished_session(self):
        self._published()
        with self.assertRaises(BusinessException) as ctx:
            mark_seat(self.db, self.assignment, SeatStatus.ABSENT.value, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("场次结束后", ctx.exception.message)
        start_session(self.db, self.session.id, ADMIN)
        finish_session(self.db, self.session.id, ADMIN)
        mark_seat(self.db, self.assignment, SeatStatus.ABSENT.value, ADMIN)
        self.assertEqual(self.assignment.seat_status, SeatStatus.ABSENT.value)

    def test_postponed_and_retake_reachable(self):
        """延期/补答辩/撤销延期必须可达: 前端「延期」按钮(DefenseSchedule.vue:227)与
        POSTPONED/RETAKE 展示(MyDefense.vue:10)都依赖这三个目标态."""
        self._published()
        mark_seat(self.db, self.assignment, SeatStatus.POSTPONED.value, ADMIN, remark="延期")
        self.assertEqual(self.assignment.seat_status, SeatStatus.POSTPONED.value)
        mark_seat(self.db, self.assignment, SeatStatus.RETAKE.value, ADMIN)
        self.assertEqual(self.assignment.seat_status, SeatStatus.RETAKE.value)
        mark_seat(self.db, self.assignment, SeatStatus.SCHEDULED.value, ADMIN)
        self.assertEqual(self.assignment.seat_status, SeatStatus.SCHEDULED.value)

    def test_postponed_unknown_target_still_rejected(self):
        """未定义的目标态仍必须 40003, 不能静默直写."""
        self._published()
        with self.assertRaises(BusinessException) as ctx:
            mark_seat(self.db, self.assignment, "NONSENSE", ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)


# ---------------- 4/5. 正式结论 ----------------

class ResultStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.db = _sqlite_session()
        self.rubric = _setup(self.db)
        self.session = _session(self.db)
        _examiners(self.db)
        publish_session(self.db, self.session.id, ADMIN)
        self.assignment = _assignment(self.db, seat_status=SeatStatus.ATTENDED.value)
        _submit(self.db, self.assignment.id, 1, {"报告质量": 80, "答辩表现": 80})
        _submit(self.db, self.assignment.id, 2, {"报告质量": 80, "答辩表现": 80})
        self.result = DefenseResult(assignment_id=self.assignment.id, cycle_id=1, stage="FINAL",
                                    status=ResultStatus.PENDING.value)
        self.db.add(self.result)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_publish_without_calculate_rejected(self):
        """未 calculate 就 publish -> 40003(核心验收)."""
        with self.assertRaises(BusinessException) as ctx:
            publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("不允许动作 publish", ctx.exception.message)
        self.assertEqual(self.result.status, ResultStatus.PENDING.value)

    def test_calculate_then_publish_then_withdraw(self):
        calculate_result(self.db, self.result.id, ADMIN)
        self.assertEqual(self.result.status, ResultStatus.READY.value)
        self.assertEqual(self.result.score, 80)
        self.assertEqual(self.result.decision, "PASS")
        self.assertIsNotNone(self.result.score_detail_json)
        publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(self.result.status, ResultStatus.PUBLISHED.value)
        self.assertIsNotNone(self.result.published_at)
        self.assertEqual(self.result.published_by, ADMIN.id)
        withdraw_result(self.db, self.result.id, ADMIN, "评委复核后重新计算")
        self.assertEqual(self.result.status, ResultStatus.WITHDRAWN.value)
        self.assertIsNotNone(self.result.withdrawn_at)
        self.assertEqual(self.result.withdraw_reason, "评委复核后重新计算")
        self.assertEqual(self.result.withdrawn_by, ADMIN.id)

    def test_calculate_twice_rejected(self):
        calculate_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            calculate_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)

    def test_publish_twice_rejected(self):
        calculate_result(self.db, self.result.id, ADMIN)
        publish_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("不允许动作 publish", ctx.exception.message)

    def test_withdraw_requires_published_and_reason(self):
        calculate_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            withdraw_result(self.db, self.result.id, ADMIN, "复核")
        self.assertEqual(ctx.exception.code, INVALID)
        publish_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx2:
            withdraw_result(self.db, self.result.id, ADMIN, "  ")
        self.assertEqual(ctx2.exception.code, INVALID)
        withdraw_result(self.db, self.result.id, ADMIN, "复核")
        with self.assertRaises(BusinessException) as ctx3:
            withdraw_result(self.db, self.result.id, ADMIN, "再撤一次")
        self.assertEqual(ctx3.exception.code, INVALID)

    def test_publish_blocked_when_seat_not_attended(self):
        self.assignment.seat_status = SeatStatus.SCHEDULED.value
        self.db.flush()
        calculate_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("未标记到场", ctx.exception.message)
        self.assertEqual(self.result.status, ResultStatus.READY.value)

    def test_publish_blocked_when_examiners_insufficient(self):
        self.assignment.seat_status = SeatStatus.ATTENDED.value
        rows = self.db.execute(select(ExaminerScore).where(ExaminerScore.examiner_id == 2)).scalars().all()
        for row in rows:
            row.status = ScoreStatus.SUPERSEDED.value
        self.db.flush()
        calculate_result(self.db, self.result.id, ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("有效评委评分不足", ctx.exception.message)

    def test_publish_blocked_when_session_cancelled(self):
        calculate_result(self.db, self.result.id, ADMIN)
        cancel_session(self.db, self.session.id, ADMIN, "临时取消")
        with self.assertRaises(BusinessException) as ctx:
            publish_result(self.db, self.result.id, ADMIN)
        self.assertEqual(ctx.exception.code, INVALID)
        self.assertIn("不允许发布结论", ctx.exception.message)

    def test_scoring_is_single_track_final_defense_score_untouched(self):
        """评分单轨: 只写 DefenseResult.score, FinalDefense.score 恒为 None."""
        # SQLite 下 BigInteger 主键不自增, 显式给 id(MySQL 侧无影响)
        fd = FinalDefense(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=3,
                          status="APPROVED", score=None)
        self.db.add(fd)
        self.db.flush()
        calculate_result(self.db, self.result.id, ADMIN)
        publish_result(self.db, self.result.id, ADMIN)
        self.db.flush()
        self.db.refresh(fd)
        self.assertEqual(self.result.score, 80)
        self.assertIsNone(fd.score)
        detail = json.loads(self.result.score_detail_json)
        self.assertEqual(detail["summary"]["score"], 80)
        self.assertEqual(detail["summary"]["score_precise"], "80.0")
        self.assertEqual(detail["summary"]["missing_item_policy"], service.MISSING_ITEM_POLICY)


# ---------------- 6. 并发发布(真库 MySQL) ----------------

_SCHEMA_SCRIPT = """
import sys
from sqlalchemy import create_engine
from app.db.base import Base
from app.models import all as _all  # noqa: F401

url, action = sys.argv[1], sys.argv[2]
engine = create_engine(url)
Base.metadata.drop_all(engine)
if action == "recreate":
    Base.metadata.create_all(engine)
engine.dispose()
"""


def _schema(action: str = "recreate") -> None:
    """在独立解释器里建/删表.

    同进程内 tests/test_state_machine.py 会把 Base.metadata 的 BigInteger 主键改写成
    Integer(sqlite 自增需要), 该改动是全局的; 若在本进程里对 MySQL create_all,
    外键两侧类型不一致会直接失败。子进程重新 import 模型, 元数据是干净的。
    """
    subprocess.run([sys.executable, "-c", _SCHEMA_SCRIPT, MYSQL_DSN, action],
                   cwd=BACKEND_DIR, check=True, capture_output=True)


def _mysql_engine():
    try:
        server = create_engine(MYSQL_SERVER, pool_pre_ping=True)
        dbname = MYSQL_DSN.rsplit("/", 1)[-1]
        with server.begin() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {dbname} CHARACTER SET utf8mb4"))
        server.dispose()
        engine = create_engine(MYSQL_DSN, pool_pre_ping=True, pool_size=5, max_overflow=5)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:  # 真库不可用: 跳过而不是伪装通过
        print(f"[concurrency] MySQL 不可用, 跳过: {exc}")
        return None


class ConcurrentPublishTest(unittest.TestCase):
    """真库并发: 两个独立连接同时发布同一结论, 只有一个成功(条件更新 rowcount 挡住第二个)."""

    def setUp(self):
        self.engine = _mysql_engine()
        if self.engine is None:
            self.skipTest("真库 MySQL 不可用(需要 127.0.0.1:3306 root), 并发用例跳过")
        _schema("recreate")
        db = Session(self.engine)
        try:
            _setup(db)
            s = _session(db)
            _examiners(db, s.id)
            publish_session(db, s.id, ADMIN)
            a = _assignment(db, s.id, seat_status=SeatStatus.ATTENDED.value)
            _submit(db, a.id, 1, {"报告质量": 82, "答辩表现": 88})
            _submit(db, a.id, 2, {"报告质量": 84, "答辩表现": 86})
            r = DefenseResult(assignment_id=a.id, cycle_id=1, stage="FINAL",
                              status=ResultStatus.PENDING.value)
            db.add(r)
            db.flush()
            calculate_result(db, r.id, ADMIN)
            db.commit()
            self.result_id = r.id
        finally:
            db.close()

    def tearDown(self):
        if getattr(self, "engine", None) is not None:
            self.engine.dispose()
            try:
                _schema("drop")
            except subprocess.CalledProcessError as exc:  # 清库失败不影响结论
                print(f"[concurrency] 清理失败: {exc.stderr[-300:]!r}")

    def test_two_connections_publish_only_one_wins(self):
        barrier = threading.Barrier(2)
        outcomes: list[tuple] = []

        def worker():
            db = Session(self.engine)
            try:
                barrier.wait(timeout=15)
                result, _computed = publish_result(db, self.result_id, ADMIN)
                db.commit()
                outcomes.append(("ok", result.score))
            except BusinessException as exc:
                db.rollback()
                outcomes.append(("err", exc.code))
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                outcomes.append(("exception", repr(exc)))
            finally:
                db.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        print(f"[concurrency] outcomes={outcomes}")
        oks = [o for o in outcomes if o[0] == "ok"]
        errs = [o for o in outcomes if o[0] == "err"]
        self.assertEqual(len(oks), 1, f"应只有一个发布成功, 实际: {outcomes}")
        self.assertEqual(len(errs), 1, f"应只有一个失败, 实际: {outcomes}")
        self.assertIn(errs[0][1], (INVALID, 40900), f"失败码应为 40003/409, 实际: {errs}")
        self.assertEqual(oks[0][1], 85)

        db = Session(self.engine)
        try:
            published = db.execute(select(func.count(DefenseResult.id)).where(
                DefenseResult.status == ResultStatus.PUBLISHED.value)).scalar()
            self.assertEqual(published, 1)
            row = db.get(DefenseResult, self.result_id)
            self.assertEqual(row.status, ResultStatus.PUBLISHED.value)
            self.assertIsNotNone(row.published_at)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
