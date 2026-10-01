"""Read-only audit trail for administrators."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.system import OperationLog
from app.models.user import SysUser

router = APIRouter(prefix="/admin/operation-logs", tags=["Audit"])


@router.get("")
def list_operation_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    module: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    conditions = []
    if module:
        conditions.append(OperationLog.module == module)
    if action:
        conditions.append(OperationLog.action == action)
    if keyword:
        conditions.append(or_(OperationLog.username.contains(keyword), SysUser.username.contains(keyword),
                              SysUser.real_name.contains(keyword), OperationLog.detail.contains(keyword)))
    if from_date:
        conditions.append(OperationLog.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        conditions.append(OperationLog.created_at < datetime.combine(to_date + timedelta(days=1), time.min))

    source = select(OperationLog).outerjoin(SysUser, SysUser.id == OperationLog.user_id).where(*conditions)
    total = db.execute(select(func.count()).select_from(source.subquery())).scalar_one()
    rows = db.execute(source.order_by(OperationLog.id.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).scalars().all()
    users = {u.id: u for u in db.execute(select(SysUser).where(SysUser.id.in_(
        [row.user_id for row in rows if row.user_id is not None]))).scalars().all()} if rows else {}
    return ResponseUtil.page([{
        "id": row.id,
        "user_id": row.user_id,
        "username": row.username or (users[row.user_id].username if row.user_id in users else None),
        "real_name": users[row.user_id].real_name if row.user_id in users else None,
        "module": row.module,
        "action": row.action,
        "method": row.method,
        "path": row.path,
        "detail": row.detail,
        "result": row.result or "SUCCESS",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows], total, page, page_size)
