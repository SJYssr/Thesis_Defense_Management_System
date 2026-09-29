"""统一事件助手: 审核记录 + 站内通知, 供各业务模块复用."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.system import Notification, ReviewRecord


def add_review_record(
    db: Session,
    *,
    business_type: str,
    business_id: int,
    student_id: int,
    action: str,
    after_status: str,
    before_status: str | None = None,
    reviewer_id: int | None = None,
    reviewer_role: str | None = None,
    comment: str | None = None,
) -> ReviewRecord:
    record = ReviewRecord(
        business_type=business_type,
        business_id=business_id,
        student_id=student_id,
        action=action,
        before_status=before_status,
        after_status=after_status,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        comment=comment,
    )
    db.add(record)
    return record


def push_notification(
    db: Session,
    *,
    user_id: int,
    type: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
) -> Notification:
    n = Notification(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        business_type=business_type,
        business_id=business_id,
    )
    db.add(n)
    return n


def now() -> datetime:
    return datetime.now()
