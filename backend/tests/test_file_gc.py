"""P9-B 孤儿文件清理: 判定口径 / 双路径(每日任务 + 管理员端点) / 幂等与安全.

口径(顺序即优先级):
  scanned            = business_type IS NULL AND deleted_at IS NULL
  skipped_recent     = 未到期(created_at >= now - retention_days), 一律不删
  skipped_referenced = 已到期但被 4 张表任一 file_id 引用
  deletable          = 已到期 且 无引用
business_type 非空的记录(如 MATERIAL/ARCHIVE)任何情况都不碰。
"""
import collections
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.exceptions import register_exception_handlers
from app.core.permissions import CurrentUser, get_current_user
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocument
from app.models.defense import ArchiveFile
from app.models.system import OperationLog, SysFile
from app.modules.file.router import router as file_router
from app.shared.storage import LocalStorage
from app.tasks import file_gc

ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
STUDENT = CurrentUser(id=21, username="stu_01", real_name="学生一", role="STUDENT", profile_id=201)

KB = 1024


class FileGcTest(unittest.TestCase):
    def setUp(self):
        # HTTP 用例在同进程另一线程里跑, 内存库必须用 StaticPool 共享同一条连接
        self.engine = create_engine("sqlite+pysqlite:///:memory:",
                                    connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        # SQLite 下 BigInteger 主键不自增, 统一改 Integer
        for table in Base.metadata.tables.values():
            for column in table.primary_key.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)   # LIFO: 先关 session 再释放引擎
        self.addCleanup(self.db.close)

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.storage = LocalStorage(Path(self.tmpdir) / "uploads")
        patcher = patch("app.tasks.file_gc.storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- 造数 ----------

    def _file(self, name: str, days_ago: float, size: int = KB,
              business_type: str | None = None, business_id: int | None = None) -> SysFile:
        _, rel_path = self.storage.save(b"x" * size, name)
        record = SysFile(
            original_name=name, stored_name=Path(rel_path).name, extension="pdf",
            mime_type="application/pdf", size=size, storage_type="LOCAL", storage_path=rel_path,
            business_type=business_type, business_id=business_id,
            created_at=datetime.now() - timedelta(days=days_ago),
        )
        self.db.add(record)
        self.db.commit()
        return record

    def _seed(self):
        """验收口径的 5 条样本: A 可删 / B 被归档引用 / C 未到期 / D 有 business_type / E 被 AI 文档引用."""
        a = self._file("orphan_a.pdf", 30)
        b = self._file("kept_archive.pdf", 30)
        c = self._file("recent_c.pdf", 1.25)          # 30 小时: 保留期 7 天算"未到期", 保留期 1 天算"已到期"
        d = self._file("material_d.pdf", 30, business_type="MATERIAL", business_id=1)
        e = self._file("kept_ai.pdf", 30)
        self.db.add(ArchiveFile(package_id=1, file_id=b.id, file_role="THESIS"))
        self.db.add(AIDocument(document_type="THESIS", document_name="论文", file_id=e.id, scope="STUDENT"))
        self.db.commit()
        return a, b, c, d, e

    def _path(self, record: SysFile) -> Path:
        return self.storage.abs_path(record.storage_path)

    def _ids(self) -> set[int]:
        self.db.expire_all()
        return {row.id for row in self.db.query(SysFile).all()}

    def _client(self, user) -> TestClient:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(file_router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    # ---------- 验收: dry_run 只统计 ----------

    def test_dry_run_counts_without_deleting(self):
        a, b, c, d, e = self._seed()

        summary = file_gc.collect(self.db, retention_days=7, dry_run=True)

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["scanned"], 4, "A/B/C/E 计入扫描域, D(business_type=MATERIAL) 不计入")
        self.assertEqual(summary["deleted"], 0, "dry_run 一律不删")
        self.assertEqual(summary["skipped_recent"], 1)
        self.assertEqual(summary["skipped_referenced"], 2)
        self.assertEqual(summary["freed_bytes"], 0)
        self.assertEqual(summary["deletable"], 1, "A 是可删候选, 规模无需拉明细即可判断")
        reasons = collections.Counter(item["reason"] for item in summary["items"])
        self.assertEqual(reasons, {"referenced": 2, "too_recent": 1, "deletable": 1})
        # 库与磁盘都不动
        self.assertEqual(self._ids(), {a.id, b.id, c.id, d.id, e.id})
        self.assertTrue(self._path(a).exists())

    def test_dry_run_lists_deletable_candidates(self):
        """dry_run 的核心用途: 能预览"将要删的清单", 而不是只看到"不删的原因"."""
        a, b, c, d, e = self._seed()

        summary = file_gc.collect(self.db, retention_days=7, dry_run=True)

        deletable_items = [item for item in summary["items"] if item["reason"] == "deletable"]
        self.assertEqual([item["file_id"] for item in deletable_items], [a.id])
        self.assertEqual(deletable_items[0]["original_name"], "orphan_a.pdf")
        self.assertEqual(deletable_items[0]["size"], a.size)
        self.assertEqual(summary["deletable"], len(deletable_items))
        self.assertEqual(summary["deleted"], 0, "dry_run 只预览, 不删")
        self.assertNotIn("deleted", {item["reason"] for item in summary["items"]})

    def test_live_run_marks_candidate_deleted_not_duplicated(self):
        """实删路径: 可删候选以 deleted 出现, 同一条不得既是 deletable 又是 deleted."""
        a, b, c, d, e = self._seed()

        summary = file_gc.collect(self.db, retention_days=7, dry_run=False)

        self.assertEqual(summary["deletable"], 1, "实删前算出的可删条数")
        items_of_a = [item for item in summary["items"] if item["file_id"] == a.id]
        self.assertEqual(len(items_of_a), 1, "同一条文件只能出现一次")
        self.assertEqual(items_of_a[0]["reason"], "deleted")
        self.assertNotIn("deletable", {item["reason"] for item in summary["items"]})
        self.assertEqual(summary["deleted"], summary["deletable"])

    # ---------- 验收: dry_run=false 只删无引用且已到期的 ----------

    def test_live_run_deletes_only_expired_unreferenced(self):
        a, b, c, d, e = self._seed()

        summary = file_gc.collect(self.db, retention_days=7, dry_run=False)

        self.assertFalse(summary["dry_run"])
        self.assertEqual(summary["scanned"], 4)
        self.assertEqual(summary["deleted"], 1, "只有 A 满足 无引用 + 已过期")
        self.assertEqual(summary["skipped_recent"], 1)
        self.assertEqual(summary["skipped_referenced"], 2)
        self.assertEqual(summary["freed_bytes"], a.size)
        self.assertEqual([item["file_id"] for item in summary["items"] if item["reason"] == "deleted"], [a.id])

        self.assertEqual(self._ids(), {b.id, c.id, d.id, e.id}, "A 记录已删, B/C/D/E 全部保留")
        self.assertFalse(self._path(a).exists(), "A 的物理文件必须被删")
        for kept in (b, c, d, e):
            self.assertTrue(self._path(kept).exists(), f"{kept.original_name} 的物理文件不得被删")
        # 幂等: 再跑一次没有可删记录
        again = file_gc.collect(self.db, retention_days=7, dry_run=False)
        self.assertEqual((again["scanned"], again["deleted"], again["freed_bytes"]), (3, 0, 0))

    # ---------- 验收: retention_days=1 时 C 变可删 ----------

    def test_shorter_retention_makes_recent_candidate_deletable(self):
        a, b, c, d, e = self._seed()

        summary = file_gc.collect(self.db, retention_days=1, dry_run=True)
        self.assertEqual((summary["scanned"], summary["skipped_recent"], summary["skipped_referenced"]), (4, 0, 2))

        summary = file_gc.collect(self.db, retention_days=1, dry_run=False)
        self.assertEqual(summary["deleted"], 2, "A 与 C 都已到期且无引用")
        self.assertNotIn(c.id, self._ids())
        self.assertFalse(self._path(c).exists())
        self.assertEqual(self._ids(), {b.id, d.id, e.id})

    # ---------- 验收: business_type 非空任何情况都不碰 ----------

    def test_business_type_records_never_touched(self):
        d = self._file("material_d.pdf", 365, business_type="MATERIAL", business_id=1)

        for days in (1, 7, 365):
            summary = file_gc.collect(self.db, retention_days=days, dry_run=False)
            self.assertEqual(summary["scanned"], 0, "business_type 非空的记录不进扫描域")
            self.assertEqual(summary["deleted"], 0)
        self.assertEqual(self._ids(), {d.id})
        self.assertTrue(self._path(d).exists())

    # ---------- 验收: 物理文件缺失仍计 deleted 且不抛异常 ----------

    def test_missing_physical_file_still_deletes_record(self):
        a = self._file("gone_a.pdf", 30)
        os.remove(self._path(a))

        summary = file_gc.collect(self.db, retention_days=7, dry_run=False)

        self.assertEqual(summary["deleted"], 1, "物理文件缺失不得阻断记录清理")
        self.assertEqual(summary["freed_bytes"], a.size)
        self.assertEqual(self._ids(), set())

    # ---------- 验收: 端点鉴权 403 ----------

    def test_endpoint_requires_admin(self):
        self._seed()
        response = self._client(STUDENT).post("/api/v1/admin/files/gc")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], 40300)
        self.assertEqual(self.db.query(SysFile).count(), 5, "被拒的调用不得改动数据")

    # ---------- 验收: admin 端点默认 dry_run=True 且与任务共用实现 ----------

    def test_endpoint_admin_defaults_to_dry_run_and_logs(self):
        a, b, c, d, e = self._seed()

        response = self._client(ADMIN).post("/api/v1/admin/files/gc")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(set(data), {"dry_run", "retention_days", "scanned", "deleted", "deletable",
                                     "skipped_referenced", "skipped_recent", "freed_bytes", "items"})
        self.assertIs(data["dry_run"], True, "默认 dry_run=True")
        self.assertEqual((data["retention_days"], data["scanned"], data["deleted"]), (7, 4, 0))
        self.assertEqual(data["deletable"], 1, "端点(dry_run)也能看到可删规模")
        self.assertEqual([i["file_id"] for i in data["items"] if i["reason"] == "deletable"], [a.id])
        self.assertEqual(self.db.query(SysFile).count(), 5)
        log = self.db.query(OperationLog).filter(OperationLog.module == "FILE",
                                                 OperationLog.action == "GC").one()
        self.assertIn("retention_days=7", log.detail)
        self.assertIn("dry_run=True", log.detail)
        self.assertIn("deleted=0", log.detail)

    def test_endpoint_live_run_deletes_with_retention_override(self):
        a, b, c, d, e = self._seed()

        response = self._client(ADMIN).post("/api/v1/admin/files/gc",
                                            params={"retention_days": 1, "dry_run": "false"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual((data["dry_run"], data["retention_days"], data["deleted"]), (False, 1, 2))
        self.assertEqual(self._ids(), {b.id, d.id, e.id})

    def test_endpoint_validates_retention_range(self):
        client = self._client(ADMIN)
        # RequestValidationError 由 core/exceptions.py:66-72 统一映射为 400 / 40002
        for value in (0, 366):
            response = client.post("/api/v1/admin/files/gc", params={"retention_days": value})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], 40002)

    # ---------- 明细上限 ----------

    def test_items_capped_at_50(self):
        for i in range(55):
            self._file(f"recent_{i:02d}.pdf", 1)

        summary = file_gc.collect(self.db, retention_days=7, dry_run=True)
        self.assertEqual(summary["skipped_recent"], 55)
        self.assertEqual(len(summary["items"]), 50, "items 最多 50 条")

    # ---------- 验收: Celery 不可用时 daily_gc 可 import ----------

    def test_daily_gc_registered_under_expected_name(self):
        from app.tasks.celery_app import CELERY_AVAILABLE

        if not CELERY_AVAILABLE:
            self.skipTest("celery 不可用")
        self.assertEqual(file_gc.daily_gc.name, "app.tasks.file_gc.daily_gc")

    def test_beat_schedule_has_daily_gc_at_0230(self):
        from app.tasks.celery_app import CELERY_AVAILABLE, celery_app

        if not CELERY_AVAILABLE:
            self.skipTest("celery 不可用")
        entry = celery_app.conf.beat_schedule["daily-file-gc"]
        self.assertEqual(entry["task"], "app.tasks.file_gc.daily_gc")
        schedule = entry["schedule"]
        self.assertEqual((schedule.hour, schedule.minute), ({2}, {30}))

    def test_daily_gc_importable_when_celery_unavailable(self):
        # importlib.reload 不清空旧命名空间, 无法验证"Celery 不可用时模块能否 import";
        # 因此从 sys.modules 摘掉后重新 import, 并保证结束后放回原模块对象(不影响其它用例的 patch)
        original = sys.modules.get("app.tasks.file_gc")
        assert original is not None
        try:
            with patch("app.tasks.celery_app.celery_app", None):
                sys.modules.pop("app.tasks.file_gc", None)
                fresh = importlib.import_module("app.tasks.file_gc")
                self.assertIsNone(getattr(fresh, "daily_gc", None),
                                  "celery_app 缺失时 daily_gc 退化为 None")
                self.assertTrue(callable(fresh.collect), "同步调用入口必须仍然可用")
        finally:
            sys.modules["app.tasks.file_gc"] = original
        self.assertIs(sys.modules["app.tasks.file_gc"], original)


if __name__ == "__main__":
    unittest.main()
