"""公告生命周期接状态机(D53–D56): 草稿/发布/下线, 过期不可见, 更新留痕, 通知已读."""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Integer, create_engine, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import Announcement, Notification, OperationLog
from app.modules.announcement.router import (
    AnnouncementRequest, create_announcement, delete_announcement, get_announcement,
    list_announcements, mark_all_read, my_notifications, update_announcement,
)

ADMIN = CurrentUser(id=99, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
READER = CurrentUser(id=7, username="student", real_name="学生", role="STUDENT", profile_id=3)


def new_session() -> tuple[Session, object]:
    """sqlite 只有 INTEGER PRIMARY KEY 才自增, 测试库把 BIGINT 主键降级(不改模型定义)."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine), engine


class AnnouncementLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.db, self.engine = new_session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create(self, title: str, status: str | None = None, expire_time=None) -> int:
        body = {"title": title, "content": "正文占位内容", "expire_time": expire_time}
        if status is not None:
            body["status"] = status
        response = create_announcement(AnnouncementRequest(**body), user=ADMIN, db=self.db)
        return json.loads(response.body)["data"]["id"]

    def _reload(self, announcement_id: int) -> Announcement:
        self.db.expire_all()
        return self.db.get(Announcement, announcement_id)

    def test_draft_publish_offline_goes_through_state_machine(self):
        ann_id = self._create("草稿公告", status="DRAFT")
        a = self._reload(ann_id)
        self.assertEqual(a.status, "DRAFT")
        # D55: 草稿没有发布时间(不再"创建即写 publish_time")
        self.assertIsNone(a.publish_time)

        update_announcement(ann_id, AnnouncementRequest(title="正式公告", content="正文占位内容",
                                                        status="PUBLISHED"), user=ADMIN, db=self.db)
        a = self._reload(ann_id)
        self.assertEqual(a.status, "PUBLISHED")
        self.assertIsNotNone(a.publish_time)  # 状态机 publish 副作用

        delete_announcement(ann_id, user=ADMIN, db=self.db)
        self.assertEqual(self._reload(ann_id).status, "OFFLINE")

        actions = self.db.execute(
            select(OperationLog.action).where(OperationLog.module == "ANNOUNCEMENT")
        ).scalars().all()
        self.assertIn("CREATE_ANNOUNCEMENT", actions)
        self.assertIn("UPDATE_ANNOUNCEMENT", actions)
        self.assertIn("OFFLINE_ANNOUNCEMENT", actions)

    def test_create_defaults_to_published(self):
        ann_id = self._create("默认发布公告")
        a = self._reload(ann_id)
        self.assertEqual(a.status, "PUBLISHED")
        self.assertIsNotNone(a.publish_time)
        self.assertEqual(a.publisher_id, ADMIN.id)

    def test_offline_requires_published_source(self):
        ann_id = self._create("草稿不能下线", status="DRAFT")
        with self.assertRaises(BusinessException) as ctx:
            delete_announcement(ann_id, user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.code, 40003)
        self.assertEqual(self._reload(ann_id).status, "DRAFT")

    def test_expired_announcement_hidden_from_list_and_detail(self):
        now = datetime.now()
        expired_id = self._create("已过期公告", expire_time=now - timedelta(days=1))
        active_id = self._create("有效期公告", expire_time=now + timedelta(days=5))
        forever_id = self._create("长期公告", expire_time=None)

        listed = list_announcements(page=1, page_size=20, db=self.db, _=READER)
        data = json.loads(listed.body)["data"]
        ids = {item["id"] for item in data["items"]}
        self.assertNotIn(expired_id, ids)
        self.assertIn(active_id, ids)
        self.assertIn(forever_id, ids)
        self.assertEqual(data["total"], 2)

        with self.assertRaises(ResourceNotFoundException):
            get_announcement(expired_id, db=self.db, _=READER)
        detail = json.loads(get_announcement(active_id, db=self.db, _=READER).body)["data"]
        self.assertEqual(detail["id"], active_id)

    def test_update_writes_operation_log(self):
        ann_id = self._create("待更新公告")
        update_announcement(ann_id, AnnouncementRequest(title="更新后的公告", content="新正文占位",
                                                        priority="IMPORTANT"), user=ADMIN, db=self.db)
        logs = self.db.execute(
            select(OperationLog).where(OperationLog.action == "UPDATE_ANNOUNCEMENT")
        ).scalars().all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].module, "ANNOUNCEMENT")
        self.assertEqual(logs[0].user_id, ADMIN.id)
        self.assertIn(str(ann_id), logs[0].detail or "")
        self.assertEqual(self._reload(ann_id).title, "更新后的公告")

    def test_notification_read_all_visible_immediately(self):
        self.db.add_all([
            Notification(user_id=READER.id, type="SYSTEM", title="通知一"),
            Notification(user_id=READER.id, type="SYSTEM", title="通知二"),
            Notification(user_id=8, type="SYSTEM", title="他人通知"),
        ])
        self.db.commit()

        mark_all_read(user=READER, db=self.db)
        data = json.loads(my_notifications(page=1, page_size=20, unread_only=False,
                                           user=READER, db=self.db).body)["data"]
        self.assertEqual(data["unread"], 0)
        self.assertEqual(data["total"], 2)
        self.assertTrue(all(item["is_read"] == 1 for item in data["items"]))
        # 别人的通知不受影响
        other = self.db.execute(select(Notification.is_read).where(Notification.user_id == 8)).scalar_one()
        self.assertEqual(other, 0)

    def test_update_without_status_keeps_current_status(self):
        """回归: 管理端只改标题(不传 status)时, 已下线公告不能被默认值"重新发布"而报 40003."""
        ann_id = self._create("已下线公告")
        delete_announcement(ann_id, user=ADMIN, db=self.db)
        update_announcement(ann_id, AnnouncementRequest(title="修订后标题", content="修订正文"),
                            user=ADMIN, db=self.db)
        a = self._reload(ann_id)
        self.assertEqual(a.status, "OFFLINE")
        self.assertEqual(a.title, "修订后标题")


if __name__ == "__main__":
    unittest.main()
