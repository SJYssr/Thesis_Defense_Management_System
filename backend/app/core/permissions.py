"""认证与权限: RBAC 依赖 + 对象级数据权限(教师只能访问自己题目的学生).

- require_roles: 接口级角色控制
- PermissionService: 数据级权限(teacher → own topics / student → self / admin → all)

D15: Redis 仅作为"登出黑名单"的快速通道, 故障时降级放行;
令牌有效性最终由 sys_user.token_version 的数据库校验兜底, 不能因 Redis 抖动拒绝合法用户.
"""
import logging
from dataclasses import dataclass
from datetime import datetime

import redis
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationException, PermissionDeniedException
from app.core.security import TOKEN_VERSION_CLAIM, decode_token
from app.db.mysql import get_db
from app.db.redis import RedisKeys, redis_client
from app.models.business import GraduationProcess, GraduationTopic, TopicSelection
from app.models.enums import Role
from app.models.user import StudentProfile, SysUser, TeacherProfile

logger = logging.getLogger("app")


@dataclass
class CurrentUser:
    id: int
    username: str
    real_name: str
    role: str
    profile_id: int | None  # TEACHER→teacher_profile.id, STUDENT→student_profile.id
    avatar: str | None = None
    must_change_password: bool = False


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthenticationException("缺少访问令牌")
    return auth[7:].strip()


def _is_blacklisted(jti: str | None) -> bool:
    """D15: Redis 故障时降级为"未命中", 不阻断业务; 令牌失效由 token_version 兜底."""
    if not jti:
        return False
    try:
        return bool(redis_client.exists(RedisKeys.BLACKLIST.format(jti=jti)))
    except redis.RedisError as exc:
        logger.warning("Redis 不可用, 跳过令牌黑名单校验(降级放行): %s", exc)
        return False


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = _extract_token(request)
    payload = decode_token(token, expected_type="access")

    user = db.execute(select(SysUser).where(SysUser.id == int(payload["sub"]))).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise AuthenticationException("用户不存在")
    if user.status != 1:
        raise AuthenticationException("账号已被禁用")

    # D14: token_version 与数据库不一致 → 已登出/已改密, 立即失效(不依赖 Redis)
    if int(payload.get(TOKEN_VERSION_CLAIM, 0)) != int(user.token_version or 0):
        raise AuthenticationException("令牌已失效,请重新登录")

    # 黑名单校验(登出后的 token 立即失效); Redis 故障降级
    if _is_blacklisted(payload.get("jti")):
        raise AuthenticationException("令牌已注销")

    profile_id = None
    if user.role == Role.TEACHER.value:
        tp = db.execute(select(TeacherProfile).where(TeacherProfile.user_id == user.id)).scalar_one_or_none()
        profile_id = tp.id if tp else None
    elif user.role == Role.STUDENT.value:
        sp = db.execute(select(StudentProfile).where(StudentProfile.user_id == user.id)).scalar_one_or_none()
        profile_id = sp.id if sp else None

    return CurrentUser(
        id=user.id,
        username=user.username,
        real_name=user.real_name,
        role=user.role,
        profile_id=profile_id,
        avatar=user.avatar,
        must_change_password=bool(user.must_change_password),
    )


def require_roles(*roles: str):
    """接口级 RBAC: Depends(require_roles(Role.TEACHER, Role.ADMIN))."""

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise PermissionDeniedException("没有权限执行此操作")
        return user

    return dependency


require_admin = require_roles(Role.ADMIN.value)
require_teacher = require_roles(Role.TEACHER.value)
require_student = require_roles(Role.STUDENT.value)
require_teacher_or_admin = require_roles(Role.TEACHER.value, Role.ADMIN.value)


class PermissionService:
    """对象级数据权限: 不止控制菜单, 必须控制到具体行(roadmap 第五十五/五十六章)."""

    @staticmethod
    def ensure_teacher_owns_topic(db: Session, user: CurrentUser, topic_id: int) -> None:
        if user.role == Role.ADMIN.value:
            return
        topic = db.execute(
            select(GraduationTopic.id).where(GraduationTopic.id == topic_id, GraduationTopic.teacher_id == user.profile_id)
        ).scalar_one_or_none()
        if topic is None:
            raise PermissionDeniedException("无权访问该题目(仅出题教师与管理员可操作)")

    @staticmethod
    def teacher_student_ids(db: Session, teacher_profile_id: int) -> list[int]:
        """教师名下已通过选题的学生 id 列表(教师可见学生的唯一来源)."""
        rows = db.execute(
            select(TopicSelection.student_id).where(
                TopicSelection.teacher_id == teacher_profile_id,
                TopicSelection.status == "APPROVED",
                TopicSelection.active_flag == 1,
            )
        ).scalars().all()
        return list(rows)

    @classmethod
    def ensure_teacher_can_access_student(cls, db: Session, user: CurrentUser, student_id: int) -> None:
        if user.role == Role.ADMIN.value:
            return
        if student_id not in cls.teacher_student_ids(db, user.profile_id):
            raise PermissionDeniedException("无权访问该学生(该学生未选择您的题目)")

    @staticmethod
    def ensure_student_self(user: CurrentUser, student_id: int) -> None:
        if user.role == Role.STUDENT.value and user.profile_id != student_id:
            raise PermissionDeniedException("学生只能访问自己的数据")

    @staticmethod
    def get_student_id_for_current_user(db: Session, user: CurrentUser) -> int:
        """当前学生用户的 student_profile.id; 教师/管理员需显式指定 student_id."""
        if user.role == Role.STUDENT.value:
            return user.profile_id
        raise PermissionDeniedException("需要学生身份")


def now_cn() -> datetime:
    return datetime.now()
