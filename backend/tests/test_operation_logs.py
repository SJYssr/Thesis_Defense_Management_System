"""Operation-log listing filters must not leak unrelated records."""
import json
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import OperationLog
from app.models.user import SysUser
from app.modules.audit.router import list_operation_logs


class OperationLogListTest(unittest.TestCase):
    def test_filters_and_pagination(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(SysUser(id=1, username="admin", real_name="管理员甲", role="ADMIN", password_hash="x"))
            db.add_all([
                OperationLog(id=1, user_id=1, module="DEFENSE", action="PUBLISH_RESULT", detail="发布成绩",
                             created_at=datetime(2026, 9, 30, 10)),
                OperationLog(id=2, user_id=1, module="ARCHIVE", action="CREATE_PACKAGE", detail="归档文件",
                             created_at=datetime(2026, 9, 30, 11)),
            ])
            db.commit()
            response = list_operation_logs(
                page=1, page_size=20, module="DEFENSE", action=None, keyword="管理员甲",
                from_date=None, to_date=None, db=db, _=None,
            )
            data = json.loads(response.body)["data"]
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["action"], "PUBLISH_RESULT")
            self.assertEqual(data["items"][0]["real_name"], "管理员甲")
        engine.dispose()
