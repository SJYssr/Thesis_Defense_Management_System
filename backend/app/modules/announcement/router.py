"""Announcement 公告模块 + Notification 通知中心."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, get_current_user, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import Announcement, Notification
from app.models.user import SysUser
from app.shared import audit
from app.shared import state_machine as sm
from app.shared.query import paginate

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


def _publisher_names(db: Session, rows: list[Announcement]) -> dict[int, str]:
    """批量取发布人姓名, 避免逐行查库。

    此前列表页从不对 publisher_name 求值, 管理端"发布人"列恒为空。
    """
    ids = {a.publisher_id for a in rows if a.publisher_id}
    if not ids:
        return {}
    return dict(db.execute(select(SysUser.id, SysUser.real_name).where(SysUser.id.in_(ids))).all())


def _visible_conditions(now: datetime | None = None) -> list:
    """对使用者可见的公告 = 已发布 且 未过期(D54; expire_time 为空视为长期有效).

    列表与详情共用同一组条件, 避免"列表看不到、详情还能打开"的口径分裂。
    """
    moment = now or datetime.now()
    return [
        Announcement.status == "PUBLISHED",
        or_(Announcement.expire_time.is_(None), Announcement.expire_time > moment),
    ]


def _is_expired(a: Announcement, now: datetime | None = None) -> bool:
    expire = a.expire_time
    return expire is not None and expire <= (now or datetime.now())


@router.get("")
def list_announcements(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                       _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rows, total = paginate(db, select(Announcement).order_by(Announcement.publish_time.desc()),
                           page=page, size=page_size, filters=_visible_conditions())
    names = _publisher_names(db, rows)
    return ResponseUtil.page([_item(a, names.get(a.publisher_id)) for a in rows], total, page, page_size)


@router.get("/{announcement_id}")
def get_announcement(announcement_id: int, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    # D54: 过期公告与未发布公告同等待遇
    if a is None or a.status != "PUBLISHED" or _is_expired(a):
        raise ResourceNotFoundException("公告不存在")
    publisher = db.get(SysUser, a.publisher_id)
    return ResponseUtil.success(_item(a, publisher.real_name if publisher else None))


# ---------- 管理员 ----------

class AnnouncementRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    content: str = Field(min_length=2)
    priority: str = Field(default="NORMAL", pattern="^(NORMAL|IMPORTANT|URGENT)$")
    expire_time: datetime | None = None
    # D55: 引入草稿态; 默认 PUBLISHED 保留"创建即发布"的既有调用行为
    status: Literal["DRAFT", "PUBLISHED"] = "PUBLISHED"


@admin_router.get("")
def admin_list(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: str | None = None,
               _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if status:
        conds.append(Announcement.status == status)
    rows, total = paginate(db, select(Announcement).order_by(Announcement.id.desc()),
                           page=page, size=page_size, filters=conds)
    names = _publisher_names(db, rows)
    return ResponseUtil.page([_item(a, names.get(a.publisher_id)) for a in rows], total, page, page_size)


@admin_router.post("")
def create_announcement(body: AnnouncementRequest, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    # D55: 先落草稿; 仅当请求为 PUBLISHED 才走状态机 DRAFT→PUBLISHED,
    # publish_time / publisher_id 由状态机 publish 的副作用写入, 不再手写。
    a = Announcement(title=body.title, content=body.content, priority=body.priority,
                     publisher_id=user.id, status="DRAFT", expire_time=body.expire_time)
    db.add(a)
    db.flush()
    if body.status == "PUBLISHED":
        sm.transition(db, sm.ANNOUNCEMENT, a, "publish", user)
    audit.record(db, user, "ANNOUNCEMENT", "CREATE_ANNOUNCEMENT", commit=False,
                 detail={"announcement_id": a.id, "title": a.title, "status": a.status})
    db.commit()
    return ResponseUtil.success({"id": a.id},
                                message="公告已发布" if a.status == "PUBLISHED" else "公告草稿已保存")


@admin_router.put("/{announcement_id}")
def update_announcement(announcement_id: int, body: AnnouncementRequest, user: CurrentUser = Depends(require_admin),
                        db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    if a is None:
        raise ResourceNotFoundException("公告不存在")
    if "status" in body.model_fields_set and body.status != a.status:
        # 只有显式传 status 才算状态意图(默认值不该把"改标题"变成"重新发布");
        # 状态列只能经状态机改; 转移表里只有 DRAFT→PUBLISHED, 退回草稿不在表内
        if body.status != "PUBLISHED":
            raise BusinessException("公告已发布, 不能退回草稿(下线请调用下线接口)", sm.INVALID_TRANSITION)
        sm.transition(db, sm.ANNOUNCEMENT, a, "publish", user)
    a.title, a.content, a.priority, a.expire_time = body.title, body.content, body.priority, body.expire_time
    # D55: 更新必须留痕(audit 统一补 method/path/ip, 与业务字段同事务提交)
    audit.record(db, user, "ANNOUNCEMENT", "UPDATE_ANNOUNCEMENT", commit=False,
                 detail={"announcement_id": a.id, "title": a.title, "status": a.status,
                         "fields": ["title", "content", "priority", "expire_time"]})
    db.commit()
    return ResponseUtil.success(message="公告已更新")


@admin_router.delete("/{announcement_id}")
def delete_announcement(announcement_id: int, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Announcement, announcement_id)
    if a is None:
        raise ResourceNotFoundException("公告不存在")
    # D53: 路径与函数名保留(前端调 delete), 语义是下线 → 状态机 ANNOUNCEMENT.offline 并留痕
    sm.transition(db, sm.ANNOUNCEMENT, a, "offline", user)
    audit.record(db, user, "ANNOUNCEMENT", "OFFLINE_ANNOUNCEMENT", commit=False,
                 detail={"announcement_id": a.id, "title": a.title})
    db.commit()
    return ResponseUtil.success(message="公告已下线")


# ---------- 通知中心 ----------

@notification_router.get("")
def my_notifications(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     unread_only: bool = False, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conds = [Notification.user_id == user.id]
    if unread_only:
        conds.append(Notification.is_read == 0)
    rows, total = paginate(db, select(Notification).order_by(Notification.id.desc()),
                           page=page, size=page_size, filters=conds)
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
    # D56: 改用 ORM 语句式 update(默认同步策略会把已加载实例一起改成已读),
    # 不再用旧式 query().update(synchronize_session=False) —— 后者会让同一请求内再查仍是未读。
    db.execute(update(Notification)
               .where(Notification.user_id == user.id, Notification.is_read == 0)
               .values(is_read=1, read_at=datetime.now()))
    db.commit()
    return ResponseUtil.success(message="全部已读")
