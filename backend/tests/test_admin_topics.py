"""Admin topic writes use real records and preserve active selections."""
import json
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationTopic, TopicSelection
from app.models.system import OperationLog
from app.models.user import SysUser, TeacherProfile
from app.modules.topic.router import (
    AdminTopicRequest, admin_create_topic, admin_delete_topic,
    admin_list_topics, admin_publish_topic,
)
from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser


ADMIN = CurrentUser(id=99, username="admin", real_name="管理员", role="ADMIN", profile_id=None)


class AdminTopicsTest(unittest.TestCase):
    def test_create_publish_filter_and_protect_selected_topic(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            next_log_id = [100]
            @event.listens_for(db, "before_flush")
            def assign_sqlite_topic_id(session, *_):
                for item in session.new:
                    if isinstance(item, GraduationTopic) and item.id is None:
                        item.id = 1
                    if isinstance(item, OperationLog) and item.id is None:
                        item.id = next_log_id[0]
                        next_log_id[0] += 1
            db.add(SysUser(id=1, username="teacher", real_name="教师甲", password_hash="x", role="TEACHER"))
            db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
            db.flush()
            body = AdminTopicRequest(title="真实课题", teacher_id=1, topic_type="软件开发", max_students=2)
            admin_create_topic(body, db=db, user=ADMIN)
            topic = db.query(GraduationTopic).one()
            self.assertEqual(topic.status, "DRAFT")
            admin_publish_topic(topic.id, db=db, user=ADMIN)
            self.assertEqual(topic.status, "PUBLISHED")
            listed = admin_list_topics(page=1, page_size=10, keyword="真实", status="PUBLISHED",
                teacher_id=None, major_id=None, college_id=None, teacher_name="教师甲",
                topic_type="软件开发", published_from=None, published_to=None, db=db, _=None)
            self.assertEqual(json.loads(listed.body)["data"]["total"], 1)
            db.add(TopicSelection(id=1, student_id=1, topic_id=topic.id, teacher_id=1,
                                  status="PENDING", active_flag=1))
            db.commit()
            with self.assertRaises(BusinessException):
                admin_delete_topic(topic.id, db=db, user=ADMIN)
            self.assertIsNone(topic.deleted_at)
        engine.dispose()

    def test_invalid_teacher_rejected(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            with self.assertRaises(BusinessException):
                admin_create_topic(AdminTopicRequest(title="不存在教师", teacher_id=99), db=db, user=ADMIN)
        engine.dispose()
