"""User 模块: 管理员对教师/学生/管理员的管理及院系-专业-班级维护."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.core.security import hash_password
from app.db.mysql import get_db
from app.models.user import AdminProfile, ClassInfo, College, Major, StudentProfile, SysUser, TeacherProfile

router = APIRouter(prefix="/admin/users", tags=["User-Admin"])
org_router = APIRouter(prefix="/org", tags=["Org"])


# ---------- schema ----------

class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=64, default="123456")
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


class UserUpdateRequest(BaseModel):
    real_name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: int | None = Field(default=None, ge=0, le=1)
    password: str | None = Field(default=None, min_length=6, max_length=64)
    title: str | None = None
    department: str | None = None
    research_direction: str | None = None
    max_students: int | None = None
    college_id: int | None = None
    major_id: int | None = None
    class_id: int | None = None
    grade: str | None = None


# ---------- 用户管理 ----------

def _user_list_query(db: Session, role: str, keyword: str | None, status: int | None):
    conds = [SysUser.role == role, SysUser.deleted_at.is_(None)]
    if keyword:
        conds.append(or_(SysUser.username.contains(keyword), SysUser.real_name.contains(keyword)))
    if status is not None:
        conds.append(SysUser.status == status)
    return db.execute(select(SysUser).where(*conds).order_by(SysUser.id.desc()))


@router.get("/{role}")
def list_users(role: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               keyword: str | None = None, status: int | None = None,
               _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if role not in ("ADMIN", "TEACHER", "STUDENT"):
        raise BusinessException("角色类型错误")
    base_conds = [SysUser.role == role, SysUser.deleted_at.is_(None)]
    if keyword:
        base_conds.append(or_(SysUser.username.contains(keyword), SysUser.real_name.contains(keyword)))
    if status is not None:
        base_conds.append(SysUser.status == status)
    total = db.execute(select(func.count(SysUser.id)).where(*base_conds)).scalar()
    users = db.execute(
        select(SysUser).where(*base_conds).order_by(SysUser.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    items = []
    for u in users:
        item = {"id": u.id, "username": u.username, "real_name": u.real_name, "role": u.role,
                "phone": u.phone, "email": u.email, "status": u.status,
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
def create_user(role: str, body: UserCreateRequest, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if role != body.role:
        raise BusinessException("路径角色与请求体不一致")
    exists = db.execute(select(SysUser.id).where(SysUser.username == body.username)).scalar_one_or_none()
    if exists:
        raise BusinessException("用户名已存在")
    user = SysUser(username=body.username, password_hash=hash_password(body.password),
                   real_name=body.real_name, role=body.role, phone=body.phone, email=body.email)
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
    return ResponseUtil.success({"id": user.id}, message="创建成功")


@router.put("/{role}/{user_id}")
def update_user(role: str, user_id: int, body: UserUpdateRequest,
                _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(SysUser, user_id)
    if user is None or user.role != role or user.deleted_at is not None:
        raise ResourceNotFoundException("用户不存在")
    data = body.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for field in ("real_name", "phone", "email", "status"):
        if field in data:
            setattr(user, field, data[field])
    if password:
        user.password_hash = hash_password(password)
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
    return ResponseUtil.success(message="更新成功")


@router.delete("/{role}/{user_id}")
def delete_user(role: str, user_id: int, current: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current.id:
        raise BusinessException("不能删除自己")
    user = db.get(SysUser, user_id)
    if user is None or user.deleted_at is not None:
        raise ResourceNotFoundException("用户不存在")
    user.deleted_at = datetime.now()  # 软删除
    db.commit()
    return ResponseUtil.success(message="删除成功")


# ---------- 院系/专业/班级 ----------

@org_router.get("/colleges")
def list_colleges(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(College).order_by(College.id)).scalars().all()
    return ResponseUtil.success([{"id": c.id, "name": c.name, "code": c.code} for c in rows])


@org_router.get("/majors")
def list_majors(college_id: int | None = None, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
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
