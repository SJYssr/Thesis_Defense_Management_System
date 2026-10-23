"""Auth 模块: 登录/登出/刷新令牌/个人资料/修改密码.

D13/D14: 登出与改密都会
  1) 删除 Redis 中的 refresh 记录;
  2) 把 access jti 写入黑名单(仅作快速通道);
  3) sys_user.token_version += 1 —— 这是权威失效手段, Redis 故障也照样生效.
"""
import logging
from datetime import datetime

import redis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationException, BusinessException
from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.db.mysql import get_db
from app.db.redis import RedisKeys, redis_client
from app.models.system import LoginLog
from app.models.user import StudentProfile, SysUser, TeacherProfile

logger = logging.getLogger("app")

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


def _profile_payload(db: Session, user: SysUser) -> dict:
    data: dict = {
        "id": user.id,
        "username": user.username,
        "real_name": user.real_name,
        "role": user.role,
        "avatar": user.avatar,
        # D21: 初始密码标记(最小实现: 只回传, 前端自行引导改密)
        "must_change_password": bool(user.must_change_password),
    }
    if user.role == "TEACHER":
        tp = db.execute(select(TeacherProfile).where(TeacherProfile.user_id == user.id)).scalar_one_or_none()
        if tp:
            data["profile_id"] = tp.id
            data["title"] = tp.title
            data["department"] = tp.department
    elif user.role == "STUDENT":
        sp = db.execute(select(StudentProfile).where(StudentProfile.user_id == user.id)).scalar_one_or_none()
        if sp:
            data["profile_id"] = sp.id
            data["student_no"] = sp.student_no
            data["grade"] = sp.grade
    return data


def _cache_refresh_jti(user_id: int, jti: str) -> None:
    """记录当前有效 refresh jti; Redis 故障只告警, 权威失效点是 token_version."""
    try:
        redis_client.set(RedisKeys.REFRESH.format(user_id=user_id), jti, ex=settings_refresh_ttl())
    except redis.RedisError as exc:
        logger.warning("Redis 不可用, 未能写入 refresh 记录(user_id=%s): %s", user_id, exc)


def _issue_tokens(user: SysUser) -> dict:
    version = int(user.token_version or 0)
    access = create_access_token(user.id, user.role, version)
    refresh = create_refresh_token(user.id, user.role, version)
    refresh_payload = decode_token(refresh, expected_type="refresh")
    _cache_refresh_jti(user.id, refresh_payload["jti"])
    return {"access_token": access, "refresh_token": refresh}


def settings_refresh_ttl() -> int:
    from app.core.config import settings

    return settings.refresh_token_expire_days * 86400


def _revoke_user_tokens(db: Session, user_id: int, access_payload: dict | None = None) -> None:
    """D13: 黑名单 jti + 删除 refresh 记录 + token_version 自增(权威失效)."""
    if access_payload:
        jti = access_payload.get("jti")
        if jti:
            ttl = max(int(access_payload.get("exp", 0) - datetime.now().timestamp()), 1)
            try:
                redis_client.set(RedisKeys.BLACKLIST.format(jti=jti), "1", ex=ttl)
            except redis.RedisError as exc:
                logger.warning("Redis 不可用, 未能写入令牌黑名单(jti=%s): %s", jti, exc)
    try:
        redis_client.delete(RedisKeys.REFRESH.format(user_id=user_id))
    except redis.RedisError as exc:
        logger.warning("Redis 不可用, 未能删除 refresh 记录(user_id=%s): %s", user_id, exc)

    user = db.get(SysUser, user_id)
    if user is not None:
        user.token_version = int(user.token_version or 0) + 1
        db.commit()


def _check_login_rate(ip: str) -> None:
    key = RedisKeys.RATE_LOGIN.format(ip=ip)
    current = redis_client.incr(key)
    if current == 1:
        redis_client.expire(key, 60)
    if current > 10:
        raise BusinessException("登录尝试过于频繁,请1分钟后再试", 42901)


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)

    user = db.execute(select(SysUser).where(SysUser.username == body.username)).scalar_one_or_none()
    ok = user is not None and user.deleted_at is None and verify_password(body.password, user.password_hash)
    db.add(LoginLog(user_id=user.id if user else None, username=body.username, ip=ip,
                    user_agent=(request.headers.get("user-agent") or "")[:250],
                    success=1 if ok else 0, message="登录成功" if ok else "用户名或密码错误"))
    if not ok:
        db.commit()
        raise AuthenticationException("用户名或密码错误")
    if user.status != 1:
        db.commit()
        raise AuthenticationException("账号已被禁用,请联系管理员")

    user.last_login_time = datetime.now()
    user.last_login_ip = ip
    db.commit()

    tokens = _issue_tokens(user)
    tokens["user"] = _profile_payload(db, user)
    return ResponseUtil.success(tokens)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user_id = int(payload["sub"])
    user = db.execute(select(SysUser).where(SysUser.id == user_id)).scalar_one_or_none()
    if user is None or user.status != 1 or user.deleted_at is not None:
        raise AuthenticationException("用户不可用")
    # D14: refresh 令牌同样受 token_version 约束(登出/改密后旧 refresh 立即失效)
    decode_token(body.refresh_token, expected_type="refresh", expected_token_version=int(user.token_version or 0))

    try:
        valid_jti = redis_client.get(RedisKeys.REFRESH.format(user_id=user_id))
    except redis.RedisError as exc:
        logger.warning("Redis 不可用, 跳过 refresh jti 校验(user_id=%s): %s", user_id, exc)
        valid_jti = None
    else:
        if valid_jti is None or valid_jti.decode() != payload["jti"]:
            raise AuthenticationException("刷新令牌已失效,请重新登录")
    return ResponseUtil.success(_issue_tokens(user))


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """D13: 登出 = 删除 refresh + 拉黑 access jti + token_version += 1."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthenticationException("缺少访问令牌")
    payload = decode_token(auth[7:].strip(), expected_type="access")
    _revoke_user_tokens(db, int(payload["sub"]), access_payload=payload)
    return ResponseUtil.success(message="已退出登录")


@router.get("/profile")
def get_profile(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    sys_user = db.execute(select(SysUser).where(SysUser.id == user.id)).scalar_one()
    return ResponseUtil.success(_profile_payload(db, sys_user))


class UpdateProfileRequest(BaseModel):
    real_name: str | None = None
    phone: str | None = None
    email: str | None = None
    avatar: str | None = None


@router.put("/profile")
def update_profile(body: UpdateProfileRequest, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    sys_user = db.execute(select(SysUser).where(SysUser.id == user.id)).scalar_one()
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(sys_user, field, value)
    db.commit()
    return ResponseUtil.success(_profile_payload(db, sys_user))


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=64)


@router.put("/password")
def change_password(body: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    sys_user = db.execute(select(SysUser).where(SysUser.id == user.id)).scalar_one()
    if not verify_password(body.old_password, sys_user.password_hash):
        raise BusinessException("原密码错误")
    sys_user.password_hash = hash_password(body.new_password)
    sys_user.must_change_password = False  # 已自行改密, 清掉初始密码标记
    # D14: 改密后 token_version += 1, 所有旧 access/refresh 立即失效
    sys_user.token_version = int(sys_user.token_version or 0) + 1
    db.commit()
    try:
        redis_client.delete(RedisKeys.REFRESH.format(user_id=user.id))
    except redis.RedisError as exc:
        logger.warning("Redis 不可用, 未能删除 refresh 记录(user_id=%s): %s", user.id, exc)
    return ResponseUtil.success(message="密码修改成功,请重新登录")
