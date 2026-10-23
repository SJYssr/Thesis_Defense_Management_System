"""题目生命周期接状态机(D52): DRAFT→PUBLISHED→CLOSED / 非法源状态 40003 / 归属校验保留.

验收要求: 状态列不再被路由直写, publish_time 由状态机副作用写入。
"""
import json
import unittest
from datetime import datetime

from sqlalchemy import BigInteger, Integer, create_engine, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationTopic
from app.models.system import OperationLog
from app.models.user import SysUser, TeacherProfile
from app.modules.topic.router import (
    TopicCreateRequest, close_topic, create_topic, list_topics, publish_topic,
)


def new_session() -> tuple[Session, object]:
    """sqlite 只有 INTEGER PRIMARY KEY 才自增, 测试库把 BIGINT 主键降级(不改模型定义)."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine), engine


def teacher(user_id: int = 1, profile_id: int = 1) -> CurrentUser:
    return CurrentUser(id=user_id, username=f"teacher{user_id}", real_name=f"教师{profile_id}",
                       role="TEACHER", profile_id=profile_id)


def student(user_id: int = 50, profile_id: int = 50) -> CurrentUser:
    return CurrentUser(id=user_id, username="student", real_name="学生", role="STUDENT", profile_id=profile_id)


class TopicLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.db, self.engine = new_session()
        for uid, pid in ((1, 1), (2, 2)):
            self.db.add(SysUser(id=uid, username=f"t{uid}", real_name=f"教师{pid}", password_hash="x", role="TEACHER"))
            self.db.add(TeacherProfile(id=pid, user_id=uid, teacher_no=f"T00{pid}"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_topic(self, actor: CurrentUser, title: str = "智能制造数据平台") -> int:
        response = create_topic(TopicCreateRequest(title=title), user=actor, db=self.db)
        return json.loads(response.body)["data"]["id"]

    def test_draft_publish_close_goes_through_state_machine(self):
        topic_id = self._create_topic(teacher(1, 1))
        topic = self.db.get(GraduationTopic, topic_id)
        self.assertEqual(topic.status, "DRAFT")
        self.assertIsNone(topic.publish_time)

        publish_topic(topic_id, user=teacher(1, 1), db=self.db)
        self.db.expire_all()
        topic = self.db.get(GraduationTopic, topic_id)
        self.assertEqual(topic.status, "PUBLISHED")
        # 副作用由状态机写 publish_time, 路由不再手写
        self.assertIsNotNone(topic.publish_time)
        self.assertLessEqual(topic.publish_time, datetime.now())

        close_topic(topic_id, user=teacher(1, 1), db=self.db)
        self.db.expire_all()
        self.assertEqual(self.db.get(GraduationTopic, topic_id).status, "CLOSED")

        actions = self.db.execute(
            select(OperationLog.action).where(OperationLog.module == "TOPIC")
        ).scalars().all()
        self.assertIn("PUBLISH_TOPIC", actions)
        self.assertIn("CLOSE_TOPIC", actions)

    def test_published_topic_visible_in_student_list(self):
        topic_id = self._create_topic(teacher(1, 1))
        publish_topic(topic_id, user=teacher(1, 1), db=self.db)
        listed = list_topics(page=1, page_size=20, keyword=None, major_id=None, difficulty=None,
                             user=student(), db=self.db)
        data = json.loads(listed.body)["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], topic_id)
        self.assertIsNotNone(data["items"][0]["publish_time"])

    def test_invalid_source_state_raises_40003(self):
        topic_id = self._create_topic(teacher(1, 1))
        # DRAFT 不能直接关闭
        with self.assertRaises(BusinessException) as ctx:
            close_topic(topic_id, user=teacher(1, 1), db=self.db)
        self.assertEqual(ctx.exception.code, 40003)
        self.db.expire_all()
        self.assertEqual(self.db.get(GraduationTopic, topic_id).status, "DRAFT")

        publish_topic(topic_id, user=teacher(1, 1), db=self.db)
        # 已发布不能再发布(并发/重复点击同样 40003)
        with self.assertRaises(BusinessException) as ctx:
            publish_topic(topic_id, user=teacher(1, 1), db=self.db)
        self.assertEqual(ctx.exception.code, 40003)
        self.db.expire_all()
        self.assertEqual(self.db.get(GraduationTopic, topic_id).status, "PUBLISHED")

    def test_other_teacher_cannot_publish(self):
        topic_id = self._create_topic(teacher(1, 1))
        with self.assertRaises(PermissionDeniedException) as ctx:
            publish_topic(topic_id, user=teacher(2, 2), db=self.db)
        self.assertEqual(ctx.exception.code, 40300)
        self.db.expire_all()
        topic = self.db.get(GraduationTopic, topic_id)
        self.assertEqual(topic.status, "DRAFT")
        self.assertIsNone(topic.publish_time)


if __name__ == "__main__":
    unittest.main()
