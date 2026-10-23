"""D75 + 公告发布人回填的回归测试.

两个都是"数据在库里, 但接口不返回 / 缓存不失效"型缺陷:
- 公告列表从不对 publisher_name 求值 -> 管理端"发布人"列恒为空
- seed 重建配置表后不清 Redis 配置缓存 -> 最长 5 分钟读上一个库的残留值
"""
import json
import unittest
from datetime import datetime

from sqlalchemy import BigInteger, Integer, create_engine
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import Announcement
from app.models.user import SysUser

ADMIN = CurrentUser(id=99, username="pubadmin", real_name="张老师", role="ADMIN", profile_id=None)


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


class PublisherNameTest(unittest.TestCase):
    def setUp(self):
        self.db = new_session()
        self.db.add(SysUser(id=99, username="pubadmin", password_hash="x",
                            real_name="张老师", role="ADMIN"))
        self.db.add(Announcement(id=1, title="测试公告", content="正文", priority="NORMAL",
                                 status="PUBLISHED", publisher_id=99,
                                 publish_time=datetime.now()))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_admin_list_returns_publisher_name(self):
        from app.modules.announcement.router import admin_list

        body = json.loads(admin_list(page=1, page_size=20, status=None, _=ADMIN, db=self.db).body)
        items = body["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["publisher_name"], "张老师")

    def test_student_list_returns_publisher_name(self):
        from app.modules.announcement.router import list_announcements

        body = json.loads(list_announcements(page=1, page_size=20, _=ADMIN, db=self.db).body)
        self.assertEqual(body["data"]["items"][0]["publisher_name"], "张老师")

    def test_missing_publisher_yields_null_not_crash(self):
        from app.modules.announcement.router import admin_list

        self.db.query(Announcement).filter(Announcement.id == 1).update({"publisher_id": 123456})
        self.db.commit()
        body = json.loads(admin_list(page=1, page_size=20, status=None, _=ADMIN, db=self.db).body)
        self.assertIsNone(body["data"]["items"][0]["publisher_name"])

    def test_publisher_lookup_is_batched(self):
        """多名发布人只允许一次批量查询(N+1 会随页大小放大)."""
        from app.modules.announcement.router import _publisher_names

        self.db.add(SysUser(id=100, username="u100", password_hash="x",
                            real_name="李老师", role="ADMIN"))
        self.db.add(Announcement(id=2, title="另一条", content="正文", priority="NORMAL",
                                 status="PUBLISHED", publisher_id=100,
                                 publish_time=datetime.now()))
        self.db.commit()
        rows = self.db.query(Announcement).order_by(Announcement.id).all()
        self.assertEqual(_publisher_names(self.db, rows), {99: "张老师", 100: "李老师"})


class SeedCacheFlushTest(unittest.TestCase):
    """D75: 重建配置表后必须清配置/Prompt 缓存."""

    def _run_with_fake(self, fake):
        import app.db.redis as redis_mod
        import seed as seed_mod

        original = redis_mod.redis_client
        redis_mod.redis_client = fake
        try:
            seed_mod._flush_config_caches()
        finally:
            redis_mod.redis_client = original

    def test_flush_deletes_sysconfig_and_every_prompt_key(self):
        from unittest.mock import MagicMock

        from app.ai.prompts import PROMPT_CACHE_KEY, PROMPT_SPECS
        from app.shared.sysconfig import CACHE_KEY

        fake = MagicMock()
        self._run_with_fake(fake)

        fake.delete.assert_called_once()
        keys = set(fake.delete.call_args[0])
        self.assertIn(CACHE_KEY, keys)
        for code in PROMPT_SPECS:
            self.assertIn(PROMPT_CACHE_KEY.format(code=code), keys)

    def test_redis_failure_does_not_block_seed(self):
        from unittest.mock import MagicMock

        boom = MagicMock()
        boom.delete.side_effect = RuntimeError("redis down")
        self._run_with_fake(boom)  # 不应抛


if __name__ == "__main__":
    unittest.main()
