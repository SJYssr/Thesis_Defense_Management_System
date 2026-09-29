"""Announcement 公告模块 + Notification 通知中心."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import Announcement, Notification
from app.models.user import SysUser
router = APIRouter(prefix="/announcements", tags=["Announcement"])
admin_router = APIRouter(prefix="/admin/announcements", tags=["Announcement-Admin"])
notification_router = APIRouter(prefix="/notifications", tags=["Notification"])

PRIORITY_CN = {"NORMAL": "普通", "IMPORTANT": "重要", "URGENT": "紧急"}


def _item(a: Announcement, publisher_name: str | None = None) -> dict:
    return {
        "id": a.id, "title": a.title, "content": a.content, "priority": a.priority,
        "priority_cn": PRIORITY_CN.get(a.priority, a.priority), "status": a.status,
        "publisher_name": publisher_name, "publish_time": a.publish_time.isoformat() if a.publish_time else None,
        "expire_time": a.expire_time.isoformat() if a.expire_time else None,
    }


@router.get("")
def list_announcements(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conds = [Announcement.status == "PUBLISHED"]
    total = db.execute(select(func.count(Announcement.id)).where(*conds)).scalar()
    rows = db.execute(select(Announcement).where(*conds).order_by(Announcement.publish_time.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_item(a) for a in rows], total, page, page_size)


@router.get("/{announcement_id}")
def get_announcement(announcement_id: int, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    if a is None or a.status != "PUBLISHED":
        raise ResourceNotFoundException("公告不存在")
    publisher = db.get(SysUser, a.publisher_id)
    return ResponseUtil.success(_item(a, publisher.real_name if publisher else None))


# ---------- 管理员 ----------

class AnnouncementRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    content: str = Field(min_length=2)
    priority: str = Field(default="NORMAL", pattern="^(NORMAL|IMPORTANT|URGENT)$")
    expire_time: datetime | None = None


@admin_router.get("")
def admin_list(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: str | None = None,
               _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if status:
        conds.append(Announcement.status == status)
    total = db.execute(select(func.count(Announcement.id)).where(*conds)).scalar()
    rows = db.execute(select(Announcement).where(*conds).order_by(Announcement.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return ResponseUtil.page([_item(a) for a in rows], total, page, page_size)


@admin_router.post("")
def create_announcement(body: AnnouncementRequest, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    a = Announcement(title=body.title, content=body.content, priority=body.priority,
                     publisher_id=user.id, publish_time=datetime.now(), expire_time=body.expire_time)
    db.add(a)
    db.commit()
    return ResponseUtil.success({"id": a.id}, message="公告已发布")


@admin_router.put("/{announcement_id}")
def update_announcement(announcement_id: int, body: AnnouncementRequest, _: CurrentUser = Depends(require_admin),
                        db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    if a is None:
        raise ResourceNotFoundException("公告不存在")
    a.title, a.content, a.priority, a.expire_time = body.title, body.content, body.priority, body.expire_time
    db.commit()
    return ResponseUtil.success(message="公告已更新")


@admin_router.delete("/{announcement_id}")
def delete_announcement(announcement_id: int, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    if a is None:
        raise ResourceNotFoundException("公告不存在")
    a.status = "OFFLINE"
    db.commit()
    return ResponseUtil.success(message="公告已下线")


# ---------- 通知中心 ----------

@notification_router.get("")
def my_notifications(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     unread_only: bool = False, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conds = [Notification.user_id == user.id]
    if unread_only:
        conds.append(Notification.is_read == 0)
    total = db.execute(select(func.count(Notification.id)).where(*conds)).scalar()
    rows = db.execute(select(Notification).where(*conds).order_by(Notification.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    unread = db.execute(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.is_read == 0)).scalar()
    items = [{
        "id": n.id, "type": n.type, "title": n.title, "content": n.content,
        "business_type": n.business_type, "business_id": n.business_id,
        "is_read": n.is_read, "created_at": n.created_at.isoformat() if n.created_at else None,
    } for n in rows]
    return ResponseUtil.success({"items": items, "page": page, "page_size": page_size, "total": total, "unread": unread})


@notification_router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise ResourceNotFoundException("通知不存在")
    if n.is_read == 0:
        n.is_read = 1
        n.read_at = datetime.now()
        db.commit()
    return ResponseUtil.success(message="已标记为已读")


@notification_router.post("/read-all")
def mark_all_read(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == 0).update(
        {"is_read": 1, "read_at": datetime.now()}, synchronize_session=False)
    db.commit()
    return ResponseUtil.success(message="全部已读")
