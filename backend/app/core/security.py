"""JWT 令牌与密码哈希: access/refresh 双令牌, Redis 管理 refresh 状态与黑名单.

D14: payload 携带 token_version; 版本与 sys_user.token_version 不一致即视为旧令牌(401).
改密/登出时 sys_user.token_version += 1, 旧 access token 立即失效(不依赖 Redis).
"""
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import AuthenticationException

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
TOKEN_VERSION_CLAIM = "token_version"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(
    user_id: int, role: str, token_type: str, expires_delta: timedelta, token_version: int = 0
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
        TOKEN_VERSION_CLAIM: int(token_version or 0),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: int, role: str, token_version: int = 0) -> str:
    return _create_token(
        user_id, role, "access", timedelta(minutes=settings.access_token_expire_minutes), token_version
    )


def create_refresh_token(user_id: int, role: str, token_version: int = 0) -> str:
    return _create_token(
        user_id, role, "refresh", timedelta(days=settings.refresh_token_expire_days), token_version
    )


def decode_token(token: str, expected_type: str = "access", *, expected_token_version: int | None = None) -> dict:
    """解码并校验令牌; 传入 expected_token_version 时版本不一致直接 401.

    未携带 token_version 的历史令牌按 0 处理(签发时默认值也是 0).
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise AuthenticationException("令牌无效或已过期") from exc
    if payload.get("type") != expected_type:
        raise AuthenticationException("令牌类型错误")

    raw_version = payload.get(TOKEN_VERSION_CLAIM, 0)
    try:
        payload[TOKEN_VERSION_CLAIM] = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise AuthenticationException("令牌版本信息异常") from exc

    if expected_token_version is not None and payload[TOKEN_VERSION_CLAIM] != int(expected_token_version):
        raise AuthenticationException("令牌已失效,请重新登录")
    return payload
