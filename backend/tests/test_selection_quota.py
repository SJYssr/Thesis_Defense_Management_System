"""D27 名额超额回归: 提交与审核两条路径都不允许突破 max_students.

unit 部分跑 sqlite in-memory(默认 suite); 并发部分需要真库(MySQL REPEATABLE READ 下
锁定读才能被证伪/证实), 由环境变量 P2B_MYSQL=1 打开:

    P2B_MYSQL=1 P2B_MYSQL_URL="mysql+pymysql://root:root123456@127.0.0.1:3306/graduation_p2b" \
        .venv/bin/python -m unittest tests.test_selection_quota -v
"""
import os
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, create_engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.exceptions import BusinessException, register_exception_handlers
from app.core.permissions import CurrentUser, require_teacher
from app.db.mysql import get_db
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationTopic, TopicSelection
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.selection import router as selection_router
from app.modules.selection.router import (
    ApplyRequest,
    _review_selection,
    _approved_selection_ids,
    apply_selection,
)

TEACHER_USER = CurrentUser(id=1, username="t001", real_name="教师甲", role="TEACHER", profile_id=1)
STUDENT_A = CurrentUser(id=2, username="s001", real_name="学生甲", role="STUDENT", profile_id=1)
STUDENT_B = CurrentUser(id=3, username="s002", real_name="学生乙", role="STUDENT", profile_id=2)
STUDENT_C = CurrentUser(id=4, username="s003", real_name="学生丙", role="STUDENT", profile_id=3)


def sqlite_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


def seed(db: Session, *, max_students: int = 1) -> GraduationTopic:
    db.add(SysUser(id=1, username="t001", real_name="教师甲", password_hash="x", role="TEACHER"))
    db.add(SysUser(id=2, username="s001", real_name="学生甲", password_hash="x", role="STUDENT"))
    db.add(SysUser(id=3, username="s002", real_name="学生乙", password_hash="x", role="STUDENT"))
    db.flush()  # 真库有 FK: 先落 sys_user, 再落教师/学生档案
    db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
    db.add(StudentProfile(id=1, user_id=2, student_no="S001"))
    db.add(StudentProfile(id=2, user_id=3, student_no="S002"))
    db.flush()
    topic = GraduationTopic(title="并发名额题", teacher_id=1, max_students=max_students,
                            status="PUBLISHED", topic_type="软件开发")
    db.add(topic)
    db.flush()
    return topic


def add_selection(db: Session, *, topic_id: int, student_id: int, status: str,
                  selection_id: int | None = None) -> TopicSelection:
    selection = TopicSelection(id=selection_id, student_id=student_id, topic_id=topic_id,
                               teacher_id=1, status=status, active_flag=1,
                               student_reason="希望研究该方向的具体工程实现")
    db.add(selection)
    db.flush()
    return selection


class QuotaUnitTest(unittest.TestCase):
    """sqlite 下可验证的名额边界(锁定读的并发语义交给真库用例)."""

    def test_apply_reports_quota_full_instead_of_new_selection(self):
        with sqlite_session() as db:
            topic = seed(db, max_students=1)
            add_selection(db, topic_id=topic.id, student_id=1, status="APPROVED", selection_id=1)
            db.commit()

            with self.assertRaises(BusinessException) as ctx:
                apply_selection(ApplyRequest(topic_id=topic.id, reason="我也想选这个题目, 请老师考虑"),
                                user=STUDENT_B, db=db)
            self.assertIn("名额已满", ctx.exception.message)
            self.assertEqual(ctx.exception.code, 400)

            db.rollback()
            rows = db.execute(select(func.count(TopicSelection.id))
                              .where(TopicSelection.student_id == 2)).scalar()
            self.assertEqual(rows, 0, "名额已满时不得为学生乙落库新的选题记录")

    def test_apply_succeeds_while_seat_available(self):
        with sqlite_session() as db:
            topic = seed(db, max_students=2)
            add_selection(db, topic_id=topic.id, student_id=1, status="APPROVED", selection_id=1)
            db.commit()
            apply_selection(ApplyRequest(topic_id=topic.id, reason="名额还有一个, 我来申请该题目"),
                            user=STUDENT_B, db=db)
            pending = db.execute(select(TopicSelection)
                                 .where(TopicSelection.student_id == 2)).scalars().all()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "PENDING")

    def test_review_approve_rejects_when_quota_already_taken(self):
        with sqlite_session() as db:
            topic = seed(db, max_students=1)
            add_selection(db, topic_id=topic.id, student_id=1, status="APPROVED", selection_id=1)
            target = add_selection(db, topic_id=topic.id, student_id=2, status="PENDING", selection_id=2)
            db.commit()

            with self.assertRaises(BusinessException) as ctx:
                _review_selection(db, target.id, TEACHER_USER, "approve", "同意")
            self.assertIn("名额已满", ctx.exception.message)

            db.rollback()
            approved = db.execute(select(func.count(TopicSelection.id)).where(
                TopicSelection.topic_id == topic.id, TopicSelection.status == "APPROVED")).scalar()
            self.assertEqual(approved, 1, "已通过人数必须等于 max_students" )
            self.assertEqual(db.get(TopicSelection, 2).status, "PENDING")

    def test_approve_last_seat_closes_topic(self):
        with sqlite_session() as db:
            topic = seed(db, max_students=1)
            target = add_selection(db, topic_id=topic.id, student_id=1, status="PENDING", selection_id=1)
            db.commit()
            _review_selection(db, target.id, TEACHER_USER, "approve", "通过")
            db.expire_all()
            self.assertEqual(db.get(TopicSelection, 1).status, "APPROVED")
            self.assertEqual(db.get(GraduationTopic, topic.id).status, "CLOSED")
            self.assertEqual(len(_approved_selection_ids(db, topic.id)), 1)

    def test_two_seat_topic_closes_only_when_last_seat_taken(self):
        """TOPIC.close 走状态机: 第 2 个名额通过时才关闭, 且 topic.status 由转移表写."""
        with sqlite_session() as db:
            topic = seed(db, max_students=2)
            add_selection(db, topic_id=topic.id, student_id=1, status="PENDING", selection_id=1)
            add_selection(db, topic_id=topic.id, student_id=2, status="PENDING", selection_id=2)
            db.commit()
            _review_selection(db, 1, TEACHER_USER, "approve", "通过")
            db.expire_all()
            self.assertEqual(db.get(GraduationTopic, topic.id).status, "PUBLISHED",
                             "名额未占满时不得关闭题目")
            _review_selection(db, 2, TEACHER_USER, "approve", "通过")
            db.expire_all()
            self.assertEqual(db.get(GraduationTopic, topic.id).status, "CLOSED",
                             "占满最后一个名额后由 TOPIC.close 关闭题目")
            print(f"[TOPIC] 2 个名额: 第 1 个通过后=PUBLISHED, 第 2 个通过后="
                  f"{db.get(GraduationTopic, topic.id).status}")

    def test_approve_on_already_closed_topic_does_not_break_review(self):
        """题目已 CLOSED(管理员手动下架)时, 审核通过不得被 40003 打断(sm.can 预判)."""
        with sqlite_session() as db:
            topic = seed(db, max_students=2)
            topic.status = "CLOSED"  # 夹具: 模拟已被下架的历史数据
            add_selection(db, topic_id=topic.id, student_id=1, status="APPROVED", selection_id=1)
            add_selection(db, topic_id=topic.id, student_id=2, status="PENDING", selection_id=2)
            db.commit()
            _review_selection(db, 2, TEACHER_USER, "approve", "通过")
            db.expire_all()
            self.assertEqual(db.get(TopicSelection, 2).status, "APPROVED")
            self.assertEqual(db.get(GraduationTopic, topic.id).status, "CLOSED")
            print("[TOPIC] 已 CLOSED 的题目上审核通过: 转移成功, 题目状态仍 CLOSED")

    def test_reject_requires_comment(self):
        with sqlite_session() as db:
            topic = seed(db, max_students=1)
            target = add_selection(db, topic_id=topic.id, student_id=1, status="PENDING", selection_id=1)
            db.commit()
            with self.assertRaises(BusinessException):
                _review_selection(db, target.id, TEACHER_USER, "reject", None)

class SelectionDecisionTest(unittest.TestCase):
    """decision 三值显式语义(与 stages 模块 P1-A 对齐).

    SELECTION 表块只有 approve/reject 两个动作, 因此 request_revision 必须给出明确业务错误,
    而不是靠"comment 是否为空"猜结论。
    """

    def setUp(self):
        # HTTP 用例在同进程另一线程里跑, 内存库必须用 StaticPool 共享同一条连接
        engine = create_engine("sqlite+pysqlite:///:memory:",
                               connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        for table in Base.metadata.tables.values():
            for column in table.primary_key.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        Base.metadata.create_all(engine)
        self.engine = engine
        self.db = Session(engine)
        topic = seed(self.db, max_students=2)
        add_selection(self.db, topic_id=topic.id, student_id=1, status="PENDING", selection_id=1)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _status(self) -> str:
        self.db.expire_all()
        return self.db.get(TopicSelection, 1).status

    def _post(self, action: str, payload: dict):
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(selection_router.teacher_router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_teacher] = lambda: TEACHER_USER
        client = TestClient(app, raise_server_exceptions=False)
        return client.post(f"/api/v1/teacher/selections/1/{action}", json=payload)

    def test_request_revision_is_rejected_with_business_error(self):
        with self.assertRaises(BusinessException) as ctx:
            _review_selection(self.db, 1, TEACHER_USER, "request_revision", "请修改")
        self.assertIn("不支持 request_revision", ctx.exception.message)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self._status(), "PENDING", "被拒绝的结论不得改变申请状态")
        print(f"[decision] 选题 request_revision -> 业务错误: {ctx.exception.message}")

    def test_unknown_decision_is_rejected(self):
        with self.assertRaises(BusinessException) as ctx:
            _review_selection(self.db, 1, TEACHER_USER, "approve_typo", None)
        self.assertIn("审核结论非法", ctx.exception.message)

    def test_approve_endpoint_defaults_to_approve_without_decision(self):
        resp = self._post("approve", {"comment": "同意"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(), "APPROVED")

    def test_reject_endpoint_defaults_to_reject_without_decision(self):
        resp = self._post("reject", {"comment": "课题与方向不符"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(), "REJECTED")

    def test_http_request_revision_returns_business_error(self):
        for action in ("approve", "reject"):
            resp = self._post(action, {"decision": "request_revision", "comment": "改一下"})
            self.assertEqual(resp.status_code, 400, f"{action} 端点收到 request_revision 必须拒绝: {resp.text}")
            self.assertNotEqual(self._status(), "RESUBMIT_REQUIRED")
        print("[decision] 选题 approve/reject 端点传 request_revision -> HTTP 400")

    def test_invalid_decision_rejected_by_request_schema(self):
        resp = self._post("approve", {"decision": "maybe"})
        self.assertIn(resp.status_code, (400, 422), f"非法 decision 必须被拒绝, 实际 {resp.status_code}")
        self.assertEqual(self._status(), "PENDING")
        print(f"[decision] 选题非法值 -> HTTP {resp.status_code}")


MYSQL_URL = os.getenv(
    "P2B_MYSQL_URL", "mysql+pymysql://root:root123456@127.0.0.1:3306/graduation_p2b")


@unittest.skipUnless(os.getenv("P2B_MYSQL") == "1", "真库并发用例: 设置 P2B_MYSQL=1 才运行")
class QuotaConcurrencyTest(unittest.TestCase):
    """真库(REPEATABLE READ)并发: 两个连接同时抢同一个题目的最后 1 个名额."""

    @classmethod
    def setUpClass(cls):
        server_url, _, db_name = MYSQL_URL.rpartition("/")
        admin_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            conn.execute(text(f"CREATE DATABASE {db_name} DEFAULT CHARACTER SET utf8mb4"))
        admin_engine.dispose()
        cls.engine = create_engine(MYSQL_URL, isolation_level="REPEATABLE READ", future=True)
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, future=True)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def _seed(self) -> tuple[int, int, int]:
        with self.Session() as db:
            db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in ("review_record", "notification", "graduation_process", "topic_selection",
                          "graduation_cycle", "graduation_topic", "student_profile",
                          "teacher_profile", "sys_user"):
                db.execute(text(f"DELETE FROM {table}"))
            db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            db.commit()
            topic = seed(db, max_students=1)
            first = add_selection(db, topic_id=topic.id, student_id=1, status="PENDING", selection_id=1)
            second = add_selection(db, topic_id=topic.id, student_id=2, status="PENDING", selection_id=2)
            db.commit()
            return topic.id, first.id, second.id

    def test_concurrent_approvals_cannot_exceed_max_students(self):
        topic_id, sel_a, sel_b = self._seed()
        barrier = threading.Barrier(2)
        outcomes: dict[int, str] = {}

        def approve(selection_id: int):
            db = self.Session()
            try:
                barrier.wait(timeout=10)
                result = _review_selection(db, selection_id, TEACHER_USER, "approve", "并发名额验证")
                outcomes[selection_id] = f"OK status={result['status']}"
            except BusinessException as exc:  # 名额已满 / 该申请已被处理
                db.rollback()
                outcomes[selection_id] = f"BusinessException({exc.code}): {exc.message}"
            except Exception as exc:  # noqa: BLE001 - 真库异常要原样暴露在报告里
                db.rollback()
                outcomes[selection_id] = f"{type(exc).__name__}: {exc}"
            finally:
                db.close()

        threads = [threading.Thread(target=approve, args=(sid,)) for sid in (sel_a, sel_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        with self.Session() as db:
            approved = db.execute(select(func.count(TopicSelection.id)).where(
                TopicSelection.topic_id == topic_id,
                TopicSelection.status == "APPROVED")).scalar()
            max_students = db.execute(select(GraduationTopic.max_students)
                                      .where(GraduationTopic.id == topic_id)).scalar()
            detail = db.execute(select(TopicSelection.id, TopicSelection.status)
                                .where(TopicSelection.topic_id == topic_id)).all()
            topic_status = db.execute(select(GraduationTopic.status)
                                      .where(GraduationTopic.id == topic_id)).scalar()
        print(f"[D27 并发] 两连接结果: {outcomes}")
        print(f"[D27 并发] topic#{topic_id} 已选人数(APPROVED)={approved} / max_students={max_students} "
              f"/ topic.status={topic_status} / 行明细={detail}")

        ok = [sid for sid, msg in outcomes.items() if msg.startswith("OK")]
        rejected = [sid for sid, msg in outcomes.items() if "名额已满" in msg]
        self.assertEqual(len(ok), 1, f"只能有一个连接成功: {outcomes}")
        self.assertEqual(len(rejected), 1, f"另一个连接必须拿到明确的\"名额已满\"业务错误: {outcomes}")
        self.assertLessEqual(approved, max_students, "已选人数不得超过题目名额上限")
        self.assertEqual(approved, 1)

    def test_apply_reads_fresh_quota_under_stale_snapshot(self):
        """D27 提交路径: 本连接先建立一致性快照 → 期间另一连接占满名额 → 提交时必须看到最新名额.

        REPEATABLE READ 下聚合快照读会读到"未满"; 锁定读(当前读)才能看到对方刚提交的 APPROVED。
        """
        topic_id, sel_a, _ = self._seed()
        with self.Session() as db:
            db.add(SysUser(id=4, username="s003", real_name="学生丙", password_hash="x", role="STUDENT"))
            db.flush()
            db.add(StudentProfile(id=3, user_id=4, student_no="S003"))
            db.commit()

        applier_snapshot_ready = threading.Event()
        approval_committed = threading.Event()
        outcome: dict[str, str] = {}

        def late_applier():
            db = self.Session()
            try:
                # 关键: 先做一次普通读, 让本事务的一致性快照定格在"名额未占满"的时刻
                db.execute(select(TopicSelection.id).limit(1)).all()
                applier_snapshot_ready.set()
                approval_committed.wait(timeout=20)
                try:
                    apply_selection(ApplyRequest(topic_id=topic_id,
                                                 reason="我也选这个题目, 请老师考虑一下"),
                                    user=STUDENT_C, db=db)
                    outcome["apply"] = "OK"
                    db.commit()
                except BusinessException as exc:
                    db.rollback()
                    outcome["apply"] = f"BusinessException({exc.code}): {exc.message}"
            except Exception as exc:  # noqa: BLE001 - 真库异常原样暴露
                db.rollback()
                outcome["apply"] = f"{type(exc).__name__}: {exc}"
            finally:
                db.close()

        thread = threading.Thread(target=late_applier)
        thread.start()
        self.assertTrue(applier_snapshot_ready.wait(timeout=20), "提交线程未能建立快照")
        with self.Session() as db:
            _review_selection(db, sel_a, TEACHER_USER, "approve", "占满最后名额")
            # 管理员事后把题目重新开放: 此时只能靠名额复查兜底, 不能靠题目 CLOSED 状态兜底
            db.execute(update(GraduationTopic).where(GraduationTopic.id == topic_id)
                       .values(status="PUBLISHED"))
            db.commit()
        approval_committed.set()
        thread.join(timeout=60)

        with self.Session() as db:
            approved = db.execute(select(func.count(TopicSelection.id)).where(
                TopicSelection.topic_id == topic_id, TopicSelection.status == "APPROVED")).scalar()
            new_rows = db.execute(select(func.count(TopicSelection.id)).where(
                TopicSelection.student_id == 3)).scalar()
        print(f"[D27 提交路径] 陈旧快照后的提交结果: {outcome} / 已选人数={approved} / 学生丙落库行数={new_rows}")
        self.assertTrue(outcome.get("apply", "").startswith("BusinessException"), f"陈旧快照下必须被拦住: {outcome}")
        self.assertIn("名额已满", outcome["apply"])
        self.assertEqual(new_rows, 0, "名额已满时不得为学生丙落库新的选题记录")


if __name__ == "__main__":
    unittest.main()
