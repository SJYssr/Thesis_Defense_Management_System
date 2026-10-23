"""User 模块: 管理员对教师/学生/管理员的管理及院系-专业-班级维护.

D21: 新建账号必须显式给出强密码(>=8 位且含字母+数字), 并打上 must_change_password 标记;
D22: create/update/password/status/delete 全部写入操作日志(成功 SUCCESS / 失败 FAIL);
D25: 删除死代码 _user_list_query, 列表 items 与 total 复用同一个条件构造器.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.core.security import hash_password
from app.db.mysql import get_db
from app.models.user import AdminProfile, ClassInfo, College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared import audit

router = APIRouter(prefix="/admin/users", tags=["User-Admin"])
org_router = APIRouter(prefix="/org", tags=["Org"])

PASSWORD_MIN_LENGTH = 8


def _validate_password_strength(value: str) -> str:
    """D21: 密码必须同时包含字母与数字, 避免弱口令批量下发."""
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        raise ValueError("密码必须同时包含字母和数字")
    return value


# ---------- schema ----------

class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    # D21: 去掉默认值 "123456", 必填且强约束
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=64)
    real_name: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern="^(ADMIN|TEACHER|STUDENT)$")
    phone: str | None = None
    email: str | None = None
    # 教师/学生扩展
    teacher_no: str | None = None
    title: str | None = None
    department: str | None = None
    research_direction: str | None = None
    max_students: int = 8
    student_no: str | None = None
    college_id: int | None = None
    major_id: int | None = None
    class_id: int | None = None
    grade: str | None = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _validate_password_strength(value)


class UserUpdateRequest(BaseModel):
    real_name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: int | None = Field(default=None, ge=0, le=1)
    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=64)
    title: str | None = None
    department: str | None = None
    research_direction: str | None = None
    max_students: int | None = None
    college_id: int | None = None
    major_id: int | None = None
    class_id: int | None = None
    grade: str | None = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_password_strength(value)


# ---------- 用户管理 ----------

def _user_conditions(role: str, keyword: str | None, status: int | None) -> list:
    """D25: 列表 items 与 total 的唯一条件来源, 避免两处条件漂移."""
    conds = [SysUser.role == role, SysUser.deleted_at.is_(None)]
    if keyword:
        conds.append(or_(SysUser.username.contains(keyword), SysUser.real_name.contains(keyword)))
    if status is not None:
        conds.append(SysUser.status == status)
    return conds


@router.get("/{role}")
def list_users(role: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               keyword: str | None = None, status: int | None = None,
               _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if role not in ("ADMIN", "TEACHER", "STUDENT"):
        raise BusinessException("角色类型错误")
    conds = _user_conditions(role, keyword, status)
    total = db.execute(select(func.count(SysUser.id)).where(*conds)).scalar()
    users = db.execute(
        select(SysUser).where(*conds).order_by(SysUser.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    items = []
    for u in users:
        item = {"id": u.id, "username": u.username, "real_name": u.real_name, "role": u.role,
                "phone": u.phone, "email": u.email, "status": u.status,
                "must_change_password": bool(u.must_change_password),
                "last_login_time": u.last_login_time.isoformat() if u.last_login_time else None,
                "created_at": u.created_at.isoformat() if u.created_at else None}
        if role == "TEACHER":
            tp = db.execute(select(TeacherProfile).where(TeacherProfile.user_id == u.id)).scalar_one_or_none()
            if tp:
                item.update({"profile_id": tp.id, "teacher_no": tp.teacher_no, "title": tp.title,
                             "department": tp.department, "research_direction": tp.research_direction,
                             "max_students": tp.max_students})
        elif role == "STUDENT":
            sp = db.execute(select(StudentProfile).where(StudentProfile.user_id == u.id)).scalar_one_or_none()
            if sp:
                item.update({"profile_id": sp.id, "student_no": sp.student_no, "grade": sp.grade,
                             "college_id": sp.college_id, "major_id": sp.major_id, "class_id": sp.class_id})
        items.append(item)
    return ResponseUtil.page(items, total, page, page_size)


@router.post("/{role}")
def create_user(role: str, body: UserCreateRequest, request: Request,
                current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if role != body.role:
        audit.record(db, current, "USER", "CREATE_USER", audit.RESULT_FAIL, request=request,
                     detail={"role": role, "username": body.username, "reason": "路径角色与请求体不一致"})
        raise BusinessException("路径角色与请求体不一致")
    exists = db.execute(select(SysUser.id).where(SysUser.username == body.username)).scalar_one_or_none()
    if exists:
        audit.record(db, current, "USER", "CREATE_USER", audit.RESULT_FAIL, request=request,
                     detail={"role": role, "username": body.username, "reason": "用户名已存在"})
        raise BusinessException("用户名已存在")

    try:
        user = SysUser(username=body.username, password_hash=hash_password(body.password),
                       real_name=body.real_name, role=body.role, phone=body.phone, email=body.email,
                       must_change_password=True)
        db.add(user)
        db.flush()
        if body.role == "TEACHER":
            if not body.teacher_no:
                raise BusinessException("教师工号不能为空")
            db.add(TeacherProfile(user_id=user.id, teacher_no=body.teacher_no, title=body.title,
                                  department=body.department, research_direction=body.research_direction,
                                  max_students=body.max_students))
        elif body.role == "STUDENT":
            if not body.student_no:
                raise BusinessException("学生学号不能为空")
            db.add(StudentProfile(user_id=user.id, student_no=body.student_no, college_id=body.college_id,
                                  major_id=body.major_id, class_id=body.class_id, grade=body.grade))
        elif body.role == "ADMIN":
            db.add(AdminProfile(user_id=user.id, department=body.department))
        db.commit()
    except Exception as exc:
        db.rollback()
        audit.record(db, current, "USER", "CREATE_USER", audit.RESULT_FAIL, request=request,
                     detail={"role": role, "username": body.username, "reason": str(exc)})
        raise

    audit.record(db, current, "USER", "CREATE_USER", audit.RESULT_SUCCESS, request=request,
                 detail={"user_id": user.id, "username": body.username, "role": body.role})
    return ResponseUtil.success({"id": user.id, "must_change_password": True}, message="创建成功")


@router.put("/{role}/{user_id}")
def update_user(role: str, user_id: int, body: UserUpdateRequest, request: Request,
                current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(SysUser, user_id)
    if user is None or user.role != role or user.deleted_at is not None:
        audit.record(db, current, "USER", "UPDATE_USER", audit.RESULT_FAIL, request=request,
                     detail={"user_id": user_id, "role": role, "reason": "用户不存在"})
        raise ResourceNotFoundException("用户不存在")

    data = body.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    status_changed = "status" in data and data["status"] != user.status
    try:
        for field in ("real_name", "phone", "email", "status"):
            if field in data:
                setattr(user, field, data[field])
        if password:
            user.password_hash = hash_password(password)
            # 管理员重置口令: 旧会话立即失效, 并要求用户下次登录后修改
            user.token_version = int(user.token_version or 0) + 1
            user.must_change_password = True
        if role == "TEACHER":
            tp = db.execute(select(TeacherProfile).where(TeacherProfile.user_id == user_id)).scalar_one_or_none()
            if tp:
                for field in ("title", "department", "research_direction", "max_students"):
                    if field in data:
                        setattr(tp, field, data[field])
        elif role == "STUDENT":
            sp = db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id)).scalar_one_or_none()
            if sp:
                for field in ("college_id", "major_id", "class_id", "grade"):
                    if field in data:
                        setattr(sp, field, data[field])
        db.commit()
    except Exception as exc:
        db.rollback()
        audit.record(db, current, "USER", "UPDATE_USER", audit.RESULT_FAIL, request=request,
                     detail={"user_id": user_id, "role": role, "reason": str(exc)})
        raise

    audit.record(db, current, "USER", "UPDATE_USER", audit.RESULT_SUCCESS, request=request,
                 detail={"user_id": user_id, "role": role, "fields": sorted(data.keys())})
    if password:
        audit.record(db, current, "USER", "RESET_PASSWORD", audit.RESULT_SUCCESS, request=request,
                     detail={"user_id": user_id, "username": user.username})
    if status_changed:
        audit.record(db, current, "USER", "UPDATE_STATUS", audit.RESULT_SUCCESS, request=request,
                     detail={"user_id": user_id, "username": user.username, "status": user.status})
    return ResponseUtil.success(message="更新成功")


@router.delete("/{role}/{user_id}")
def delete_user(role: str, user_id: int, request: Request,
                current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current.id:
        audit.record(db, current, "USER", "DELETE_USER", audit.RESULT_FAIL, request=request,
                     detail={"user_id": user_id, "reason": "不能删除自己"})
        raise BusinessException("不能删除自己")
    user = db.get(SysUser, user_id)
    if user is None or user.deleted_at is not None:
        audit.record(db, current, "USER", "DELETE_USER", audit.RESULT_FAIL, request=request,
                     detail={"user_id": user_id, "role": role, "reason": "用户不存在"})
        raise ResourceNotFoundException("用户不存在")
    try:
        user.deleted_at = datetime.now()  # 软删除
        # 已注销账号的令牌立即失效
        user.token_version = int(user.token_version or 0) + 1
        db.commit()
    except Exception as exc:
        db.rollback()
        audit.record(db, current, "USER", "DELETE_USER", audit.RESULT_FAIL, request=request,
                     detail={"user_id": user_id, "reason": str(exc)})
        raise
    audit.record(db, current, "USER", "DELETE_USER", audit.RESULT_SUCCESS, request=request,
                 detail={"user_id": user_id, "username": user.username, "role": role})
    return ResponseUtil.success(message="删除成功")


# ---------- 院系/专业/班级 ----------

@org_router.get("/colleges")
def list_colleges(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(College).order_by(College.id)).scalars().all()
    return ResponseUtil.success([{"id": c.id, "name": c.name, "code": c.code} for c in rows])


@org_router.get("/majors")
def list_majors(college_id: int | None = None, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conds = []
    if college_id:
        conds.append(Major.college_id == college_id)
    rows = db.execute(select(Major).where(*conds).order_by(Major.id)).scalars().all()
    return ResponseUtil.success([{"id": m.id, "name": m.name, "code": m.code, "college_id": m.college_id} for m in rows])


@org_router.get("/classes")
def list_classes(major_id: int | None = None, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if major_id:
        conds.append(ClassInfo.major_id == major_id)
    rows = db.execute(select(ClassInfo).where(*conds).order_by(ClassInfo.id)).scalars().all()
    return ResponseUtil.success([{"id": c.id, "name": c.name, "grade": c.grade, "major_id": c.major_id} for c in rows])
