"""用户与组织模型: sys_user / 三种 profile / 院系-专业-班级."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SysUser(Base, TimestampMixin):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="登录名")
    password_hash: Mapped[str] = mapped_column(String(128), comment="密码哈希")
    real_name: Mapped[str] = mapped_column(String(64), comment="姓名")
    role: Mapped[str] = mapped_column(String(16), index=True, comment="ADMIN/TEACHER/STUDENT")
    avatar: Mapped[str | None] = mapped_column(String(255), comment="头像")
    phone: Mapped[str | None] = mapped_column(String(20), comment="手机号")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱")
    status: Mapped[int] = mapped_column(SmallInteger, default=1, comment="0禁用 1启用")
    last_login_time: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_ip: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, comment="软删除")
    # D14: 登出/改密后自增, 使已签发的 access/refresh 令牌立即失效
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="令牌版本号"
    )
    # D21: 管理员新建账号后置 1, 提示用户修改初始密码
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", comment="是否需要修改初始密码"
    )


class AdminProfile(Base, TimestampMixin):
    __tablename__ = "admin_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), unique=True, index=True)
    admin_no: Mapped[str | None] = mapped_column(String(32))
    department: Mapped[str | None] = mapped_column(String(128))
    remark: Mapped[str | None] = mapped_column(String(255))


class TeacherProfile(Base, TimestampMixin):
    __tablename__ = "teacher_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), unique=True, index=True)
    teacher_no: Mapped[str] = mapped_column(String(32), unique=True, comment="工号")
    title: Mapped[str | None] = mapped_column(String(64), comment="职称")
    department: Mapped[str | None] = mapped_column(String(128), comment="院系")
    research_direction: Mapped[str | None] = mapped_column(String(255), comment="研究方向")
    introduction: Mapped[str | None] = mapped_column(Text, comment="简介")
    max_students: Mapped[int] = mapped_column(Integer, default=8, comment="最多指导学生数")


class StudentProfile(Base, TimestampMixin):
    __tablename__ = "student_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), unique=True, index=True)
    student_no: Mapped[str] = mapped_column(String(32), unique=True, comment="学号")
    college_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("college.id"), index=True)
    major_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("major.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("class_info.id"))
    grade: Mapped[str | None] = mapped_column(String(16), comment="年级")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class College(Base, TimestampMixin):
    __tablename__ = "college"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, comment="学院名称")
    code: Mapped[str | None] = mapped_column(String(32))


class Major(Base, TimestampMixin):
    __tablename__ = "major"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    college_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("college.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), comment="专业名称")
    code: Mapped[str | None] = mapped_column(String(32))


class ClassInfo(Base, TimestampMixin):
    __tablename__ = "class_info"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    major_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("major.id"), index=True)
    grade: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
