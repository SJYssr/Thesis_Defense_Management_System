"""D21/D22/D23/D25: 新建账号强口令 + must_change_password, 用户模块操作日志, 列表条件一致性."""
import json
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.system import OperationLog
from app.models.user import SysUser, TeacherProfile
from app.modules.audit.router import list_operation_logs
from app.modules.user.router import UserCreateRequest, UserUpdateRequest, create_user, delete_user, list_users, update_user

ADMIN = CurrentUser(id=1000, username="admin", real_name="管理员", role="ADMIN", profile_id=None)


def make_request(method: str = "POST", path: str = "/api/v1/admin/users/TEACHER", ip: str = "10.0.0.9") -> Request:
    return Request({
        "type": "http", "method": method, "path": path,
        "headers": [(b"user-agent", b"unittest")],
        "client": (ip, 5555), "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "root_path": "",
    })


def teacher_body(username: str = "teacher_new", password: str = "abc12345") -> UserCreateRequest:
    return UserCreateRequest(username=username, password=password, real_name="新教师", role="TEACHER",
                             teacher_no=f"T-{username}", department="计算机")


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        @event.listens_for(self.db, "before_flush")
        def _assign_ids(session, *_):
            for item in session.new:
                if hasattr(item, "id") and getattr(item, "id", None) is None:
                    _assign_ids.next_id = getattr(_assign_ids, "next_id", 900) + 1
                    item.id = _assign_ids.next_id

        admin = SysUser(id=1000, username="admin", password_hash="x", real_name="管理员",
                        role="ADMIN", status=1, token_version=0)
        self.db.add(admin)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create(self, body: UserCreateRequest | None = None):
        return create_user("TEACHER", body or teacher_body(), make_request(), ADMIN, self.db)

    def _logs(self, action: str | None = None):
        query = self.db.query(OperationLog)
        if action:
            query = query.filter(OperationLog.action == action)
        return query.order_by(OperationLog.id).all()

    def _payload(self, response) -> dict:
        return json.loads(response.body)


class CreateUserTest(_DbCase):
    """D21: 密码必填且 >=8 位含字母+数字; 新账号带 must_change_password."""

    def test_password_required(self):
        with self.assertRaises(ValidationError):
            UserCreateRequest(username="t1", real_name="甲", role="TEACHER")

    def test_short_password_rejected(self):
        with self.assertRaises(ValidationError):
            UserCreateRequest(username="t1", password="ab12", real_name="甲", role="TEACHER")

    def test_digit_only_password_rejected(self):
        with self.assertRaises(ValidationError):
            UserCreateRequest(username="t1", password="12345678", real_name="甲", role="TEACHER")

    def test_letter_only_password_rejected(self):
        with self.assertRaises(ValidationError):
            UserCreateRequest(username="t1", password="abcdefgh", real_name="甲", role="TEACHER")

    def test_create_sets_must_change_password(self):
        result = self._payload(self._create())
        self.assertEqual(result["code"], 0)
        self.assertTrue(result["data"]["must_change_password"])

        user = self.db.query(SysUser).filter(SysUser.username == "teacher_new").one()
        self.assertTrue(user.must_change_password)
        self.assertEqual(user.token_version, 0)
        self.assertEqual(user.password_hash.startswith("$2"), True)  # bcrypt 哈希, 非明文

    def test_create_writes_success_audit(self):
        self._create()
        logs = self._logs("CREATE_USER")
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log.result, "SUCCESS")
        self.assertEqual(log.module, "USER")
        self.assertEqual(log.method, "POST")
        self.assertEqual(log.path, "/api/v1/admin/users/TEACHER")
        self.assertEqual(log.ip, "10.0.0.9")
        self.assertEqual(log.username, "admin")
        self.assertEqual(log.user_id, 1000)
        self.assertIn("teacher_new", log.detail or "")

    def test_duplicate_username_writes_fail_audit(self):
        self._create()
        with self.assertRaises(BusinessException):
            self._create(teacher_body(username="teacher_new", password="abc12345"))
        fails = [row for row in self._logs("CREATE_USER") if row.result == "FAIL"]
        self.assertEqual(len(fails), 1)
        self.assertIn("用户名已存在", fails[0].detail or "")

    def test_role_mismatch_rejected(self):
        body = UserCreateRequest(username="s1", password="abc12345", real_name="学生甲", role="STUDENT")
        with self.assertRaises(BusinessException):
            create_user("TEACHER", body, make_request(), ADMIN, self.db)
        fails = [row for row in self._logs("CREATE_USER") if row.result == "FAIL"]
        self.assertEqual(len(fails), 1)
        self.assertIn("路径角色与请求体不一致", fails[0].detail or "")


class UpdateUserTest(_DbCase):
    """D22: 改密/改状态/改资料都必须留下日志, 改密使旧会话失效."""

    def setUp(self):
        super().setUp()
        self._create()
        self.user = self.db.query(SysUser).filter(SysUser.username == "teacher_new").one()

    def test_password_reset_bumps_version_and_audits(self):
        update_user("TEACHER", self.user.id, UserUpdateRequest(password="reset12345"),
                    make_request("PUT", f"/api/v1/admin/users/TEACHER/{self.user.id}"), ADMIN, self.db)
        self.db.expire_all()
        user = self.db.get(SysUser, self.user.id)
        self.assertEqual(user.token_version, 1)
        self.assertTrue(user.must_change_password)
        self.assertEqual(len(self._logs("UPDATE_USER")), 1)
        self.assertEqual(len(self._logs("RESET_PASSWORD")), 1)
        self.assertEqual(self._logs("RESET_PASSWORD")[0].result, "SUCCESS")

    def test_status_change_audited(self):
        update_user("TEACHER", self.user.id, UserUpdateRequest(status=0),
                    make_request("PUT", f"/api/v1/admin/users/TEACHER/{self.user.id}"), ADMIN, self.db)
        self.db.expire_all()
        self.assertEqual(self.db.get(SysUser, self.user.id).status, 0)
        self.assertEqual(len(self._logs("UPDATE_STATUS")), 1)

    def test_missing_user_writes_fail_audit(self):
        with self.assertRaises(ResourceNotFoundException):
            update_user("TEACHER", 999999, UserUpdateRequest(real_name="丙"),
                        make_request("PUT", "/api/v1/admin/users/TEACHER/999999"), ADMIN, self.db)
        fails = [row for row in self._logs("UPDATE_USER") if row.result == "FAIL"]
        self.assertEqual(len(fails), 1)
        self.assertIn("用户不存在", fails[0].detail or "")
        self.assertEqual(fails[0].method, "PUT")


class DeleteUserTest(_DbCase):
    def setUp(self):
        super().setUp()
        self._create()
        self.user = self.db.query(SysUser).filter(SysUser.username == "teacher_new").one()

    def test_soft_delete_and_audit(self):
        delete_user("TEACHER", self.user.id,
                    make_request("DELETE", f"/api/v1/admin/users/TEACHER/{self.user.id}"), ADMIN, self.db)
        self.db.expire_all()
        user = self.db.get(SysUser, self.user.id)
        self.assertIsNotNone(user.deleted_at)
        self.assertEqual(user.token_version, 1)
        logs = self._logs("DELETE_USER")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].result, "SUCCESS")
        self.assertEqual(logs[0].method, "DELETE")

    def test_delete_self_rejected_and_audited(self):
        with self.assertRaises(BusinessException):
            delete_user("ADMIN", ADMIN.id, make_request("DELETE", f"/api/v1/admin/users/ADMIN/{ADMIN.id}"),
                        ADMIN, self.db)
        fails = [row for row in self._logs("DELETE_USER") if row.result == "FAIL"]
        self.assertEqual(len(fails), 1)
        self.assertIn("不能删除自己", fails[0].detail or "")


class ListConsistencyTest(_DbCase):
    """D25: items 与 total 共用同一个条件构造器, 不再漂移."""

    def test_total_matches_items_with_status_filter(self):
        for index in range(3):
            self._create(teacher_body(username=f"t{index}", password="abc12345"))
        active = self.db.query(SysUser).filter(SysUser.username == "t0").one()
        update_user("TEACHER", active.id, UserUpdateRequest(status=0),
                    make_request("PUT", f"/api/v1/admin/users/TEACHER/{active.id}"), ADMIN, self.db)

        page = self._payload(list_users("TEACHER", 1, 20, None, 1, ADMIN, self.db))["data"]
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(all(item["status"] == 1 for item in page["items"]))
        self.assertTrue(all(item["must_change_password"] is True for item in page["items"]))

        unfiltered = self._payload(list_users("TEACHER", 1, 20, None, None, ADMIN, self.db))["data"]
        self.assertEqual(unfiltered["total"], 3)
        self.assertEqual(len(unfiltered["items"]), 3)

    def test_keyword_and_role_are_consistent(self):
        self._create(teacher_body(username="zhang_san", password="abc12345"))
        self._create(teacher_body(username="li_si", password="abc12345"))
        page = self._payload(list_users("TEACHER", 1, 20, "zhang", None, ADMIN, self.db))["data"]
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["username"], "zhang_san")
        # 无匹配关键字时 items 与 total 同时为 0
        empty = self._payload(list_users("TEACHER", 1, 20, "nobody", None, ADMIN, self.db))["data"]
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["items"], [])

    def test_deleted_user_excluded_from_both(self):
        self._create(teacher_body(username="to_delete", password="abc12345"))
        user = self.db.query(SysUser).filter(SysUser.username == "to_delete").one()
        delete_user("TEACHER", user.id,
                    make_request("DELETE", f"/api/v1/admin/users/TEACHER/{user.id}"), ADMIN, self.db)
        page = self._payload(list_users("TEACHER", 1, 20, None, None, ADMIN, self.db))["data"]
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["items"], [])


class AuditQueryTest(_DbCase):
    """D23: 历史 result 为空时显示 UNKNOWN, 不伪造 SUCCESS."""

    def test_null_result_rendered_as_unknown(self):
        self.db.add(OperationLog(user_id=1000, username="admin", module="LEGACY", action="OLD_ACTION",
                                 result=None, detail="历史数据"))
        self.db.add(OperationLog(user_id=1000, username="admin", module="LEGACY", action="NEW_ACTION",
                                 result="FAIL", detail="新数据"))
        self.db.commit()

        page = self._payload(list_operation_logs(1, 20, "LEGACY", None, None, None, None, ADMIN, self.db))["data"]
        results = {item["action"]: item["result"] for item in page["items"]}
        self.assertEqual(results["OLD_ACTION"], "UNKNOWN")
        self.assertEqual(results["NEW_ACTION"], "FAIL")


if __name__ == "__main__":
    unittest.main()
