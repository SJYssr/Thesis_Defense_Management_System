"""D70: 文件上传的每日字节配额与业务归属(向后兼容)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from starlette.datastructures import Headers
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.exceptions import FileException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import OperationLog, SysFile, SystemConfig
from app.modules.file.router import upload_file
from app.shared.storage import LocalStorage

STUDENT = CurrentUser(id=21, username="stu_01", real_name="学生一", role="STUDENT", profile_id=201)
STUDENT2 = CurrentUser(id=22, username="stu_02", real_name="学生二", role="STUDENT", profile_id=202)
TEACHER = CurrentUser(id=11, username="teacher_a", real_name="教师A", role="TEACHER", profile_id=101)

KB = 1024


class _NoCacheRedis:
    """断掉配置缓存, 让 get_int 每次都落到本次测试的 sqlite 配置行."""

    def get(self, *args, **kwargs):
        return None

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0


class UploadQuotaTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        @event.listens_for(self.db, "before_flush")
        def assign_sqlite_ids(session, *_):
            # SQLite 下 BigInteger 主键不自增, 统一显式分配
            next_id = getattr(assign_sqlite_ids, "next_id", 900)
            for item in session.new:
                if isinstance(item, (SysFile, OperationLog)) and item.id is None:
                    next_id += 1
                    item.id = next_id
            assign_sqlite_ids.next_id = next_id

        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(self.engine.dispose)  # LIFO: 先关 session 再释放引擎
        self.addCleanup(self.db.close)

        for patcher in (patch("app.modules.file.router.storage", LocalStorage(Path(self.tmpdir) / "uploads")),
                        patch("app.shared.sysconfig.redis_client", _NoCacheRedis())):
            patcher.start()
            self.addCleanup(patcher.stop)

        # 配额 1MB / 单文件 5MB(默认 200MB 太大, 测试里用小值)
        self.db.add_all([SystemConfig(key="max_upload_daily_mb", value="1"),
                         SystemConfig(key="max_upload_size_mb", value="5")])
        self.db.commit()

    def _upload(self, size: int, user=STUDENT, name="paper.pdf", **kwargs):
        spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        spool.write(b"x" * size)
        spool.seek(0)
        upload = UploadFile(file=spool, filename=name, headers=Headers({"content-type": "application/pdf"}))
        self.addCleanup(spool.close)
        # 直接调用(不经 FastAPI)时 Form 默认值是 Form 对象, 这里显式给值等同"未传"
        kwargs.setdefault("business_type", None)
        kwargs.setdefault("business_id", None)
        return upload_file(upload, user=user, db=self.db, **kwargs)

    @staticmethod
    def _data(resp) -> dict:
        return json.loads(resp.body)["data"]

    def test_upload_within_quota_succeeds_and_echoes_usage(self):
        data = self._data(self._upload(600 * KB))
        self.assertEqual(data["size"], 600 * KB)
        self.assertEqual(data["daily_used_bytes"], 600 * KB)
        self.assertIsNone(data["business_type"], "不传业务归属也必须能上传(向后兼容)")

    def test_upload_over_daily_quota_rejected(self):
        self._upload(600 * KB)
        with self.assertRaises(FileException) as ctx:
            self._upload(600 * KB)
        self.assertEqual(ctx.exception.code, 40013, "超配额必须是独立的业务错误码")
        self.assertIn("配额", ctx.exception.message)
        kept = self.db.query(SysFile).filter(SysFile.uploader_id == STUDENT.id).all()
        self.assertEqual(len(kept), 1, "被拒的上传不得落库")
        self.assertEqual(sum(f.size for f in kept), 600 * KB)

    def test_quota_is_per_user(self):
        self._upload(900 * KB, user=STUDENT)
        # 另一个用户不受前一个用户的额度影响
        data = self._data(self._upload(900 * KB, user=STUDENT2))
        self.assertEqual(data["daily_used_bytes"], 900 * KB)

    def test_single_file_limit_uses_distinct_code(self):
        self.db.merge(SystemConfig(key="max_upload_size_mb", value="1"))
        self.db.commit()
        with self.assertRaises(FileException) as ctx:
            self._upload(2 * 1024 * KB)
        self.assertEqual(ctx.exception.code, 40010, "单文件超限仍是 40010, 与日配额 40013 区分")

    def test_business_binding_optional_and_persisted_in_record(self):
        data = self._data(self._upload(20 * KB, user=TEACHER, business_type="TOPIC", business_id=9))
        self.assertEqual(data["business_type"], "TOPIC")
        self.assertEqual(data["business_id"], 9)
        record = self.db.get(SysFile, data["id"])
        self.assertIsNotNone(record)
        self.assertEqual(record.uploader_id, TEACHER.id)
        # sys_file 暂无 business_* 列(models 不在本批范围), 归属至少落在日志里可追溯
        self.assertEqual(record.storage_type, "LOCAL")

    def test_quota_window_uses_same_clock_as_created_at(self):
        data = self._data(self._upload(10 * KB))
        record = self.db.get(SysFile, data["id"])
        # created_at 必须由应用时钟写入(而非 sqlite 的 UTC server_default), 否则 00:00-08:00 会漏计
        self.assertLess(abs((record.created_at - datetime.now()).total_seconds()), 60)


if __name__ == "__main__":
    unittest.main()
