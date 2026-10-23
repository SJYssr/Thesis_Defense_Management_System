"""进度接口不得在 GET 里写库(D71), 逾期扫描不得同步全表跑单事务(D67).

D71 的证伪点: 旧实现 GET 里 ProgressService.recompute() + commit(), 会刷新
graduation_process.updated_at 并改写 progress_percent/risk_level。
D67 的证伪点: 旧实现一次遍历全部 GraduationProcess 并单次 commit。
"""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import (GraduationCycle, GraduationProcess,
                                 GraduationSchedule)
from app.models.system import Notification, OperationLog
from app.models.user import StudentProfile, SysUser
from app.tasks import risk_scan as risk_scan_module
from app.modules.progress import router as progress_router
from app.modules.progress.router import (_run_overdue_scan_batched, admin_overdue_scan,
                                         my_progress, student_progress_detail)

FROZEN = datetime(2021, 1, 1, 8, 0, 0)
ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
TEACHER = CurrentUser(id=2, username="t1", real_name="教师甲", role="TEACHER", profile_id=9)
STUDENT_USER = CurrentUser(id=11, username="stu1", real_name="学生1", role="STUDENT", profile_id=21)


def _data(response):
    return json.loads(response.body)["data"]


def _sqlite_ids(session, *_):
    for item in session.new:
        if isinstance(item, Notification) and item.id is None:
            item.id = 901
        if isinstance(item, OperationLog) and item.id is None:
            item.id = 902


class D71ReadOnlyGetTest(unittest.TestCase):
    """D71: 连续两次 GET 不得改动 graduation_process.updated_at(即无写操作)."""

    def _fixture(self, db):
        db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
        db.add(SysUser(id=11, username="stu1", password_hash="x", real_name="学生1", role="STUDENT"))
        db.add(StudentProfile(id=21, user_id=11, student_no="2022001"))
        db.add(GraduationCycle(id=31, academic_year="2025-2026", student_id=21, status="ACTIVE"))
        db.add(GraduationProcess(id=41, student_id=21, cycle_id=31, current_stage="MIDTERM",
                                 progress_percent=40, risk_level="NORMAL", risk_reason=None,
                                 updated_at=FROZEN))
        db.commit()

    def test_student_get_does_not_touch_process_row(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            self._fixture(db)
            first = _data(my_progress(user=STUDENT_USER, db=db))
            second = _data(my_progress(user=STUDENT_USER, db=db))
            db.expire_all()
            process = db.get(GraduationProcess, 41)
            self.assertEqual(process.updated_at, FROZEN, "GET 不得刷新 updated_at")
            self.assertEqual(process.progress_percent, 40, "GET 只读现值, 不重算")
            self.assertEqual(first["updated_at"], second["updated_at"])
            self.assertEqual(first["progress_percent"], second["progress_percent"])
            self.assertEqual(len(db.new), 0)
            self.assertEqual(len(db.dirty), 0)
        engine.dispose()

    def test_teacher_detail_get_does_not_touch_process_row(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            self._fixture(db)
            with patch("app.core.permissions.PermissionService.teacher_student_ids", return_value=[21]):
                _data(student_progress_detail(student_id=21, user=TEACHER, db=db))
                _data(student_progress_detail(student_id=21, user=TEACHER, db=db))
            db.expire_all()
            self.assertEqual(db.get(GraduationProcess, 41).updated_at, FROZEN)
            self.assertEqual(len(db.dirty), 0)
        engine.dispose()


class D67OverdueScanTest(unittest.TestCase):
    """D67: 走队列时立即受理; 同步兜底必须分批提交."""

    def _fixture(self, db, count):
        db.add(GraduationSchedule(id=1, academic_year="2025-2026", is_active=1))
        for i in range(1, count + 1):
            db.add(SysUser(id=1000 + i, username=f"stu{i}", password_hash="x", real_name=f"学生{i}", role="STUDENT"))
            db.add(StudentProfile(id=i, user_id=1000 + i, student_no=f"2022{i:04d}"))
            db.add(GraduationCycle(id=1000 + i, academic_year="2025-2026", student_id=i, status="ACTIVE"))
            db.add(GraduationProcess(id=2000 + i, student_id=i, cycle_id=1000 + i, current_stage="MIDTERM",
                                     progress_percent=40, risk_level="NORMAL", updated_at=FROZEN))
        db.commit()

    def test_sync_fallback_commits_per_batch(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as setup:
            self._fixture(setup, 250)
        summary = _run_overdue_scan_batched(session_factory=lambda: Session(engine))
        self.assertEqual(summary["mode"], "sync")
        self.assertEqual(summary["scanned"], 250)
        self.assertEqual(summary["batches"], 3, "250 条按每批 100 必须分成 3 个事务, 不得单事务跑全表")
        self.assertEqual(progress_router.OVERDUE_SCAN_BATCH_SIZE, 100)
        engine.dispose()

    def test_celery_branch_returns_immediately_without_scanning(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            event.listen(db, "before_flush", _sqlite_ids)
            self._fixture(db, 3)
            with patch.object(progress_router, "_enqueue_overdue_scan", return_value=True):
                data = _data(admin_overdue_scan(user=ADMIN, db=db))
            self.assertEqual(data["mode"], "celery")
            self.assertIs(data["queued"], True)
            self.assertEqual(data["scanned"], 0, "入队即返回, 请求内不做扫描")
            db.expire_all()
            self.assertEqual(db.get(GraduationProcess, 2001).updated_at, FROZEN)
            self.assertEqual(db.query(OperationLog).one().action, "OVERDUE_SCAN")
        engine.dispose()

    def test_enqueue_disabled_when_celery_off(self):
        # 本仓库默认 CELERY_ENABLED=false: 入队分支必须干净地落回 False, 交给同步兜底
        self.assertIs(progress_router._enqueue_overdue_scan(), False)

    def test_router_fallback_and_celery_task_share_one_implementation(self):
        # 逾期规则只有一份实现: 路由兜底与 Celery 任务都必须落到 risk_scan.scan_overdue_risk_batched
        calls = []

        def _fake(session_factory=None, batch_size=None):
            calls.append((session_factory, batch_size))
            return {"mode": "sync", "scanned": 0, "batches": 0, "sentinel": True}

        with patch.object(risk_scan_module, "scan_overdue_risk_batched", _fake):
            via_router = progress_router._run_overdue_scan_batched()
            via_task = risk_scan_module.run_overdue_risk_scan()
        self.assertEqual(len(calls), 2, "两条路径必须复用同一个实现, 不得各写一份逾期规则")
        self.assertIs(via_router["sentinel"], True)
        self.assertIs(via_task["sentinel"], True)
        self.assertEqual(progress_router.OVERDUE_SCAN_BATCH_SIZE, risk_scan_module.OVERDUE_SCAN_BATCH_SIZE)
        self.assertEqual(risk_scan_module.OVERDUE_SCAN_BATCH_SIZE, 100)


if __name__ == "__main__":
    unittest.main()
