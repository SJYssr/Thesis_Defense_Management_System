"""学年阶段时间配置单测(roadmap 八十四章/八十五章 允许选题时间)."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationSchedule
from app.modules.schedule.router import validate_windows
from app.shared.progress import ProgressService


class WindowValidationTest(unittest.TestCase):
    def test_unknown_key_rejected(self):
        with self.assertRaises(BusinessException):
            validate_windows({"holidy_start": datetime(2026, 9, 1)})

    def test_stage_start_after_end_rejected(self):
        with self.assertRaises(BusinessException):
            validate_windows({"opening_start": datetime(2026, 9, 10), "opening_end": datetime(2026, 9, 1)})

    def test_half_window_and_cross_stage_allowed(self):
        # 只设单边允许; 阶段之间允许交叉(补选期间他人已开题)
        validate_windows({
            "selection_start": datetime(2026, 9, 1),
            "opening_start": datetime(2026, 8, 1),  # 早于 selection_start
            "opening_end": datetime(2026, 10, 1),
        })


class SelectionWindowTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        from sqlalchemy import event

        from app.models.system import OperationLog

        @event.listens_for(self.db, "before_flush")
        def assign_sqlite_ids(session, *_):
            # SQLite 下 BigInteger 主键不自增, 统一显式分配
            next_id = getattr(assign_sqlite_ids, "next_id", 900)
            for item in session.new:
                if isinstance(item, (OperationLog, GraduationSchedule)) and item.id is None:
                    next_id += 1
                    item.id = next_id
            assign_sqlite_ids.next_id = next_id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _schedule(self, start=None, end=None, active=1, year="2025-2026"):
        s = GraduationSchedule(academic_year=year, is_active=active,
                               selection_start=start, selection_end=end)
        self.db.add(s)
        self.db.commit()
        return s

    def test_no_schedule_passes(self):
        ProgressService.ensure_selection_window_open(self.db)

    def test_not_open_yet_rejected(self):
        self._schedule(start=datetime.now() + timedelta(days=3))
        with self.assertRaises(BusinessException) as ctx:
            ProgressService.ensure_selection_window_open(self.db)
        self.assertEqual(ctx.exception.code, 40003)
        self.assertIn("尚未开始", ctx.exception.message)

    def test_closed_rejected(self):
        self._schedule(end=datetime.now() - timedelta(days=1))
        with self.assertRaises(BusinessException) as ctx:
            ProgressService.ensure_selection_window_open(self.db)
        self.assertEqual(ctx.exception.code, 40003)
        self.assertIn("已截止", ctx.exception.message)

    def test_within_window_passes(self):
        self._schedule(start=datetime.now() - timedelta(days=1), end=datetime.now() + timedelta(days=1))
        ProgressService.ensure_selection_window_open(self.db)

    def test_inactive_schedule_ignored(self):
        self._schedule(end=datetime.now() - timedelta(days=1), active=0)
        ProgressService.ensure_selection_window_open(self.db)

    def test_activation_deactivates_others(self):
        from app.modules.schedule.router import _set_windows

        first = self._schedule(start=datetime.now() - timedelta(days=1))
        second = GraduationSchedule(academic_year="2026-2027", is_active=1)
        self.db.add(second)
        self.db.flush()
        user = SimpleNamespace(id=1, username="admin")
        _set_windows(second, {"selection_start": datetime(2026, 9, 1)}, user, self.db, "UPDATE_SCHEDULE")
        self.db.commit()
        self.db.refresh(first)
        self.assertEqual(first.is_active, 0, "激活新学年后旧学年应自动停用")
        self.assertEqual(second.is_active, 1)


if __name__ == "__main__":
    unittest.main()
