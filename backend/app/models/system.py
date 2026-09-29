"""文件/审核记录/公告/通知/提醒/日志模型."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SysFile(Base, TimestampMixin):
    __tablename__ = "sys_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    original_name: Mapped[str] = mapped_column(String(255), comment="原始文件名")
    stored_name: Mapped[str] = mapped_column(String(128), comment="存储文件名(uuid)")
    extension: Mapped[str | None] = mapped_column(String(32))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_type: Mapped[str] = mapped_column(String(16), default="LOCAL", comment="LOCAL/MINIO/OSS")
    storage_path: Mapped[str] = mapped_column(String(512), comment="相对路径 2026/09/uuid.docx")
    sha256: Mapped[str | None] = mapped_column(String(64))
    uploader_id: Mapped[int | None] = mapped_column(BigInteger, comment="上传人 sys_user.id")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class TopicAttachment(Base):
    __tablename__ = "topic_attachment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_topic.id"), index=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_file.id"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReviewRecord(Base, TimestampMixin):
    """统一审核记录: 完整追踪每次提交/审核动作, 支撑时间轴展示."""

    __tablename__ = "review_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    business_type: Mapped[str] = mapped_column(String(32), index=True, comment="TOPIC_SELECTION/OPENING_REPORT/...")
    business_id: Mapped[int] = mapped_column(BigInteger, index=True)
    student_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, comment="审核人 sys_user.id, 学生提交时为空")
    reviewer_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16), comment="SUBMIT/APPROVE/REJECT/RETURN/RESUBMIT")
    comment: Mapped[str | None] = mapped_column(String(1000))
    before_status: Mapped[str | None] = mapped_column(String(24))
    after_status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class Announcement(Base, TimestampMixin):
    __tablename__ = "announcement"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL", comment="NORMAL/IMPORTANT/URGENT")
    status: Mapped[str] = mapped_column(String(16), default="PUBLISHED", comment="DRAFT/PUBLISHED/OFFLINE")
    publisher_id: Mapped[int] = mapped_column(BigInteger)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime)
    expire_time: Mapped[datetime | None] = mapped_column(DateTime)


class Notification(Base, TimestampMixin):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="接收人 sys_user.id")
    type: Mapped[str] = mapped_column(String(32), default="SYSTEM", comment="ANNOUNCEMENT/REVIEW_RESULT/REMINDER/SYSTEM")
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str | None] = mapped_column(Text)
    business_type: Mapped[str | None] = mapped_column(String(32))
    business_id: Mapped[int | None] = mapped_column(BigInteger)
    is_read: Mapped[int] = mapped_column(SmallInteger, default=0, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminder"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, comment="发送教师 sys_user.id")
    receiver_id: Mapped[int] = mapped_column(BigInteger, comment="接收学生 sys_user.id")
    student_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str | None] = mapped_column(Text)
    related_business_type: Mapped[str | None] = mapped_column(String(32))
    related_business_id: Mapped[int | None] = mapped_column(BigInteger)


class OperationLog(Base):
    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    module: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(String(16))
    path: Mapped[str | None] = mapped_column(String(255))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(16), comment="SUCCESS/FAIL")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class LoginLog(Base):
    __tablename__ = "login_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    success: Mapped[int] = mapped_column(SmallInteger, default=1)
    message: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
