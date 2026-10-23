"""D12/D13/D14/D15: 密钥必填校验、登出/改密的令牌版本失效、Redis 故障降级."""
import unittest
from datetime import datetime

import redis
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.config import SECRET_KEY_MIN_LENGTH, Settings, settings
from app.core.exceptions import AuthenticationException
from app.core.permissions import CurrentUser, get_current_user
from app.core.security import (
    TOKEN_VERSION_CLAIM,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.base import Base
from app.db.redis import RedisKeys
from app.models import all as _all_models  # noqa: F401
from app.models.user import SysUser
from app.modules.auth import router as auth_router
from app.modules.auth.router import ChangePasswordRequest, change_password, logout

EXAMPLE_SECRET = "graduation-system-secret-key-change-me-in-production"


def make_request(method: str = "POST", path: str = "/api/v1/auth/logout", token: str | None = None,
                 ip: str = "10.0.0.9") -> Request:
    headers = [(b"user-agent", b"unittest")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({
        "type": "http", "method": method, "path": path, "headers": headers,
        "client": (ip, 5555), "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "root_path": "",
    })


class FakeRedis:
    """最小内存实现, 用于断言黑名单/refresh 键的读写."""

    def __init__(self):
        self.store = {}
        self.deleted = []

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)
        return 1

    def exists(self, key):
        return 1 if key in self.store else 0

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, ttl):
        return True


class BrokenRedis:
    """模拟 Redis 全链路不可用."""

    def __getattr__(self, name):
        def _raise(*_args, **_kwargs):
            raise redis.RedisError("redis down")
        return _raise


class SecretKeyConfigTest(unittest.TestCase):
    """D12: secret_key 必填 + 拒绝示例串 + 长度下限."""

    def test_missing_secret_key_fails_startup(self):
        with self.assertRaises(ValidationError) as ctx:
            Settings(_env_file=None)
        self.assertIn("secret_key", str(ctx.exception))

    def test_short_secret_key_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Settings(_env_file=None, secret_key="a" * (SECRET_KEY_MIN_LENGTH - 1))
        self.assertIn("SECRET_KEY 长度必须", str(ctx.exception))

    def test_example_secret_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Settings(_env_file=None, secret_key=EXAMPLE_SECRET)
        self.assertIn("示例串", str(ctx.exception))

    def test_strong_secret_accepted(self):
        strong = "Q7f3n2Wc9vLp0sXz5Tb8Yh1Kd4Rg6Mj2Ue8Aw3Ni7Oc"
        self.assertGreaterEqual(len(strong), SECRET_KEY_MIN_LENGTH)
        self.assertEqual(Settings(_env_file=None, secret_key=strong).secret_key, strong)

    def test_loaded_env_secret_is_not_example(self):
        self.assertGreaterEqual(len(settings.secret_key), SECRET_KEY_MIN_LENGTH)
        self.assertNotEqual(settings.secret_key, EXAMPLE_SECRET)

    def test_upload_dir_is_absolute(self):
        from pathlib import Path

        self.assertTrue(Path(settings.upload_dir).is_absolute())


class TokenVersionClaimTest(unittest.TestCase):
    """D14: 签发的 payload 带 token_version, decode 校验版本."""

    def test_access_token_carries_version(self):
        payload = decode_token(create_access_token(7, "ADMIN", 3))
        self.assertEqual(payload[TOKEN_VERSION_CLAIM], 3)
        self.assertEqual(int(payload["sub"]), 7)

    def test_default_version_is_zero(self):
        self.assertEqual(decode_token(create_access_token(7, "ADMIN"))[TOKEN_VERSION_CLAIM], 0)

    def test_refresh_token_carries_version(self):
        payload = decode_token(create_refresh_token(7, "ADMIN", 5), expected_type="refresh")
        self.assertEqual(payload[TOKEN_VERSION_CLAIM], 5)

    def test_version_mismatch_rejected(self):
        token = create_access_token(7, "ADMIN", 1)
        with self.assertRaises(AuthenticationException) as ctx:
            decode_token(token, expected_token_version=2)
        self.assertIn("重新登录", ctx.exception.message)

    def test_version_match_accepted(self):
        payload = decode_token(create_access_token(7, "ADMIN", 4), expected_token_version=4)
        self.assertEqual(payload[TOKEN_VERSION_CLAIM], 4)


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        @event.listens_for(self.db, "before_flush")
        def _assign_ids(session, *_):
            # SQLite 下 BigInteger 主键不自增, 统一显式分配
            for item in session.new:
                if hasattr(item, "id") and getattr(item, "id", None) is None:
                    _assign_ids.next_id = getattr(_assign_ids, "next_id", 900) + 1
                    item.id = _assign_ids.next_id

        self.user = SysUser(id=1001, username="teacher_a", password_hash=hash_password("oldpass1234"),
                            real_name="张三", role="TEACHER", status=1, token_version=0,
                            must_change_password=True)
        self.db.add(self.user)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()


class LogoutRevocationTest(_DbCase):
    """D13: 登出 = 删 refresh + 拉黑 jti + token_version + 1."""

    def test_logout_revokes_everything(self):
        fake = FakeRedis()
        token = create_access_token(self.user.id, self.user.role, 0)
        payload = decode_token(token)
        original_patch = auth_router.redis_client
        auth_router.redis_client = fake
        try:
            logout(make_request(token=token), self.db)
        finally:
            auth_router.redis_client = original_patch

        self.assertIn(RedisKeys.REFRESH.format(user_id=self.user.id), fake.deleted)
        self.assertTrue(fake.exists(RedisKeys.BLACKLIST.format(jti=payload["jti"])))

        self.db.expire_all()
        refreshed = self.db.get(SysUser, self.user.id)
        self.assertEqual(refreshed.token_version, 1)
        # 旧 access token 因版本不一致立即失效
        with self.assertRaises(AuthenticationException):
            decode_token(token, expected_token_version=refreshed.token_version)

    def test_logout_without_token_rejected(self):
        with self.assertRaises(AuthenticationException) as ctx:
            logout(make_request(), self.db)
        self.assertIn("缺少访问令牌", ctx.exception.message)


class ChangePasswordRevocationTest(_DbCase):
    """D14: 改密后 token_version + 1 + 清 must_change_password + 删 refresh."""

    def test_change_password_bumps_version(self):
        fake = FakeRedis()
        original_patch = auth_router.redis_client
        auth_router.redis_client = fake
        try:
            current = CurrentUser(id=self.user.id, username=self.user.username, real_name=self.user.real_name,
                                  role=self.user.role, profile_id=1, must_change_password=True)
            body = ChangePasswordRequest(old_password="oldpass1234", new_password="newpass1234")
            change_password(body, current, self.db)
        finally:
            auth_router.redis_client = original_patch

        self.db.expire_all()
        user = self.db.get(SysUser, self.user.id)
        self.assertTrue(verify_password("newpass1234", user.password_hash))
        self.assertEqual(user.token_version, 1)
        self.assertFalse(user.must_change_password)
        self.assertIn(RedisKeys.REFRESH.format(user_id=self.user.id), fake.deleted)

    def test_wrong_old_password_rejected(self):
        current = CurrentUser(id=self.user.id, username=self.user.username, real_name=self.user.real_name,
                              role=self.user.role, profile_id=1)
        with self.assertRaises(Exception) as ctx:
            change_password(ChangePasswordRequest(old_password="bad", new_password="newpass1234"), current, self.db)
        self.assertEqual(getattr(ctx.exception, "code", None), 400)


class CurrentUserGuardTest(_DbCase):
    """D14 + D15: token_version 兜底校验 / Redis 故障降级放行 / 黑名单拦截."""

    def test_stale_version_token_rejected(self):
        token = create_access_token(self.user.id, self.user.role, 0)
        self.db.query(SysUser).filter(SysUser.id == self.user.id).update({"token_version": 2})
        self.db.commit()
        with self.assertRaises(AuthenticationException) as ctx:
            get_current_user(make_request("GET", "/api/v1/auth/profile", token), self.db)
        self.assertIn("令牌已失效", ctx.exception.message)

    def test_valid_token_returns_current_user(self):
        from unittest.mock import patch

        token = create_access_token(self.user.id, self.user.role, 0)
        with patch("app.core.permissions.redis_client", FakeRedis()):
            user = get_current_user(make_request("GET", "/api/v1/auth/profile", token), self.db)
        self.assertEqual(user.id, self.user.id)
        self.assertEqual(user.role, "TEACHER")
        self.assertTrue(user.must_change_password)

    def test_redis_failure_degrades_open(self):
        from unittest.mock import patch

        token = create_access_token(self.user.id, self.user.role, 0)
        with patch("app.core.permissions.redis_client", BrokenRedis()):
            with self.assertLogs("app", level="WARNING") as logs:
                user = get_current_user(make_request("GET", "/api/v1/auth/profile", token), self.db)
        self.assertEqual(user.id, self.user.id)
        self.assertTrue(any("Redis 不可用" in line for line in logs.output))

    def test_blacklisted_jti_rejected(self):
        from unittest.mock import patch

        token = create_access_token(self.user.id, self.user.role, 0)
        jti = decode_token(token)["jti"]
        fake = FakeRedis()
        fake.set(RedisKeys.BLACKLIST.format(jti=jti), "1")
        with patch("app.core.permissions.redis_client", fake):
            with self.assertRaises(AuthenticationException) as ctx:
                get_current_user(make_request("GET", "/api/v1/auth/profile", token), self.db)
        self.assertIn("已注销", ctx.exception.message)

    def test_disabled_account_rejected(self):
        from unittest.mock import patch

        self.db.query(SysUser).filter(SysUser.id == self.user.id).update({"status": 0})
        self.db.commit()
        token = create_access_token(self.user.id, self.user.role, 0)
        with patch("app.core.permissions.redis_client", FakeRedis()):
            with self.assertRaises(AuthenticationException) as ctx:
                get_current_user(make_request("GET", "/api/v1/auth/profile", token), self.db)
        self.assertIn("禁用", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
