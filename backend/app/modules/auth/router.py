"""Auth 模块: 登录/登出/刷新令牌/个人资料/修改密码."""
from datetime import datetime

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

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


def _profile_payload(db: Session, user: SysUser) -> dict:
    data: dict = {"id": user.id, "username": user.username, "real_name": user.real_name, "role": user.role, "avatar": user.avatar}
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


def _issue_tokens(user: SysUser) -> dict:
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id, user.role)
    refresh_payload = decode_token(refresh, expected_type="refresh")
    # 记录当前有效 refresh token 版本(jti), 改密/登出可使其失效
    redis_client.set(
        RedisKeys.REFRESH.format(user_id=user.id),
        refresh_payload["jti"],
        ex=settings_refresh_ttl(),
    )
    return {"access_token": access, "refresh_token": refresh}


def settings_refresh_ttl() -> int:
    from app.core.config import settings

    return settings.refresh_token_expire_days * 86400


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
    valid_jti = redis_client.get(RedisKeys.REFRESH.format(user_id=user_id))
    if valid_jti is None or valid_jti.decode() != payload["jti"]:
        raise AuthenticationException("刷新令牌已失效,请重新登录")
    user = db.execute(select(SysUser).where(SysUser.id == user_id)).scalar_one_or_none()
    if user is None or user.status != 1:
        raise AuthenticationException("用户不可用")
    return ResponseUtil.success(_issue_tokens(user))


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_token(auth[7:].strip())
            ttl = max(int(payload.get("exp", 0) - datetime.now().timestamp()), 1)
            redis_client.set(RedisKeys.BLACKLIST.format(jti=payload["jti"]), "1", ex=ttl)
        except AuthenticationException:
            pass
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
    # 使旧 refresh token 全部失效
    redis_client.delete(RedisKeys.REFRESH.format(user_id=user.id))
    db.commit()
    return ResponseUtil.success(message="密码修改成功,请重新登录")
