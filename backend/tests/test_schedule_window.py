"""学年阶段时间配置单测(roadmap 八十四章/八十五章 允许选题时间)."""
import json
import unittest
from datetime import datetime, timedelta, timezone
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


def _sqlite_session():
    """SQLite 内存库 + BigInteger 主键显式分配(与上方用例同一套约定)."""
    from sqlalchemy import event

    from app.models.system import OperationLog

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    @event.listens_for(db, "before_flush")
    def assign_sqlite_ids(session, *_):
        next_id = getattr(assign_sqlite_ids, "next_id", 1900)
        for item in session.new:
            if isinstance(item, (OperationLog, GraduationSchedule)) and item.id is None:
                next_id += 1
                item.id = next_id
        assign_sqlite_ids.next_id = next_id

    return engine, db


ADMIN_USER = SimpleNamespace(id=1, username="admin")


class WindowTimezoneNormalizationTest(unittest.TestCase):
    """D62: 前端传带时区的时间必须与不带时区入库等价(不再静默吞掉偏移)."""

    def setUp(self):
        self.engine, self.db = _sqlite_session()
        self.addCleanup(self.engine.dispose)  # LIFO: 先关 session 再释放引擎
        self.addCleanup(self.db.close)

    def _schedule(self, year):
        s = GraduationSchedule(academic_year=year, is_active=0)
        self.db.add(s)
        self.db.commit()
        return s

    def test_to_naive_keeps_wall_clock_and_drops_offset(self):
        from app.modules.schedule.router import to_naive

        aware = to_naive("2026-03-01T00:00:00+08:00")
        naive = to_naive("2026-03-01T00:00:00")
        self.assertEqual(aware, naive)
        self.assertEqual(aware, datetime(2026, 3, 1, 0, 0))
        self.assertIsNone(aware.tzinfo, "入库值必须一律 naive")

    def test_aware_and_naive_strings_persist_identically(self):
        from app.modules.schedule.router import _set_windows

        first = self._schedule("2025-2026")
        _set_windows(first, {"opening_start": "2026-03-01T00:00:00+08:00"}, ADMIN_USER, self.db, "CREATE_SCHEDULE")
        self.db.commit()
        self.db.refresh(first)

        second = self._schedule("2026-2027")
        _set_windows(second, {"opening_start": "2026-03-01T00:00:00"}, ADMIN_USER, self.db, "CREATE_SCHEDULE")
        self.db.commit()
        self.db.refresh(second)

        self.assertEqual(first.opening_start, second.opening_start)
        self.assertEqual(first.opening_start, datetime(2026, 3, 1, 0, 0))
        self.assertIsNone(first.opening_start.tzinfo)

    def test_invalid_time_string_rejected_with_clear_code(self):
        from app.modules.schedule.router import validate_windows

        with self.assertRaises(BusinessException) as ctx:
            validate_windows({"selection_start": "2026/03/01"})
        self.assertEqual(ctx.exception.code, 40002)
        self.assertIn("ISO 8601", ctx.exception.message)

    def test_create_schedule_accepts_iso_string_body(self):
        from app.modules.schedule.router import ScheduleBody, create_schedule

        body = ScheduleBody(academic_year="2027-2028", windows={
            "opening_start": "2026-03-01T00:00:00+08:00",
            "opening_end": "2026-04-01T00:00:00+08:00",
        })
        data = json.loads(create_schedule(body, ADMIN_USER, self.db).body)["data"]
        self.assertEqual(data["opening_start"], "2026-03-01T00:00:00")
        self.assertEqual(data["opening_end"], "2026-04-01T00:00:00")
        self.assertTrue(data["opening_start"] < data["opening_end"])

    def test_offset_does_not_shift_days_left_boundary(self):
        """D62 的症状回归: 带 +08:00 的窗口若被整体挪 8 小时, 边界判断会错位."""
        now = datetime.now()
        end = (now + timedelta(days=1)).replace(microsecond=0, tzinfo=timezone(timedelta(hours=8)))
        start = (now - timedelta(days=1)).replace(microsecond=0, tzinfo=timezone(timedelta(hours=8)))
        s = GraduationSchedule(academic_year="2028-2029", is_active=1)
        self.db.add(s)
        self.db.commit()

        from app.modules.schedule.router import _set_windows

        _set_windows(s, {"selection_start": start.isoformat(), "selection_end": end.isoformat()},
                     ADMIN_USER, self.db, "UPDATE_SCHEDULE")
        self.db.commit()
        self.db.refresh(s)
        self.assertEqual(s.selection_end.replace(microsecond=0), end.replace(tzinfo=None))
        # 窗口仍然有效: 若偏移被吞, 结束时间会落到 now+1d-8h 之外/之内不等价
        self.assertGreaterEqual(s.selection_start, datetime.now() - timedelta(days=1, minutes=1))
        self.assertLessEqual(s.selection_end, datetime.now() + timedelta(days=1, minutes=1))
        ProgressService.ensure_selection_window_open(self.db)


class ScheduleListPaginationTest(unittest.TestCase):
    """D61: 学年配置列表分页(前端不再一次性拉全量)."""

    def setUp(self):
        self.engine, self.db = _sqlite_session()
        self.addCleanup(self.engine.dispose)  # LIFO: 先关 session 再释放引擎
        self.addCleanup(self.db.close)
        for year in ("2023-2024", "2024-2025", "2025-2026"):
            self.db.add(GraduationSchedule(academic_year=year, is_active=0))
        self.db.commit()

    def test_page_size_and_total(self):
        from app.modules.schedule.router import list_schedules

        first = json.loads(list_schedules(page=1, page_size=2, _=ADMIN_USER, db=self.db).body)["data"]
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["page_size"], 2)
        self.assertEqual(len(first["items"]), 2)
        second = json.loads(list_schedules(page=2, page_size=2, _=ADMIN_USER, db=self.db).body)["data"]
        self.assertEqual(second["total"], 3)
        self.assertEqual(len(second["items"]), 1)

    def test_default_page_size_applied(self):
        from app.modules.schedule.router import list_schedules

        data = json.loads(list_schedules(page=1, page_size=10, _=ADMIN_USER, db=self.db).body)["data"]
        self.assertEqual(len(data["items"]), 3)
        self.assertEqual(data["page_size"], 10)


if __name__ == "__main__":
    unittest.main()
