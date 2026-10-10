"""Read-only audit trail for administrators: 操作日志 + AI 调用日志(roadmap 七十七章)."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIUsageLog
from app.models.system import OperationLog
from app.models.user import SysUser

router = APIRouter(prefix="/admin/operation-logs", tags=["Audit"])
ai_router = APIRouter(prefix="/admin/ai-usage-logs", tags=["Audit-AI"])

AI_SCENE_LABELS = {
    "GENERATE_QUESTIONS": "生成问题",
    "DEFENSE_QA": "回答评价",
    "FOLLOW_UP": "动态追问",
    "PROGRESS_ANALYSIS": "进度分析",
    "PPT_OUTLINE": "PPT大纲",
    "QA": "知识问答",
    "QUERY_REWRITE": "问题改写",
}


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


def _ai_usage_conditions(scene: str | None, keyword: str | None, from_date: date | None, to_date: date | None):
    conditions = []
    if scene:
        conditions.append(AIUsageLog.scene == scene)
    if keyword:
        conditions.append(or_(SysUser.username.contains(keyword), SysUser.real_name.contains(keyword),
                              AIUsageLog.model.contains(keyword)))
    if from_date:
        conditions.append(AIUsageLog.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        conditions.append(AIUsageLog.created_at < datetime.combine(to_date + timedelta(days=1), time.min))
    return conditions


@ai_router.get("")
def list_ai_usage_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scene: str | None = None,
    keyword: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """AI 调用明细分页查询 + 汇总(调用次数/Token/耗时), 供管理端"AI调用日志"页使用."""
    conditions = _ai_usage_conditions(scene, keyword, from_date, to_date)
    source = select(AIUsageLog).outerjoin(SysUser, SysUser.id == AIUsageLog.user_id).where(*conditions)
    total = db.execute(select(func.count()).select_from(source.subquery())).scalar_one()
    rows = db.execute(source.order_by(AIUsageLog.id.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).scalars().all()
    users = {u.id: u for u in db.execute(select(SysUser).where(SysUser.id.in_(
        [row.user_id for row in rows if row.user_id is not None]))).scalars().all()} if rows else {}

    summary_source = select(
        func.count(AIUsageLog.id),
        func.coalesce(func.sum(AIUsageLog.input_tokens), 0),
        func.coalesce(func.sum(AIUsageLog.output_tokens), 0),
        func.coalesce(func.avg(AIUsageLog.latency_ms), 0),
    ).outerjoin(SysUser, SysUser.id == AIUsageLog.user_id).where(*conditions)
    calls, in_tokens, out_tokens, avg_latency = db.execute(summary_source).one()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_calls = db.execute(select(func.count(AIUsageLog.id)).where(
        AIUsageLog.created_at >= today_start)).scalar_one()

    return ResponseUtil.success({
        "list": [{
            "id": row.id,
            "user_id": row.user_id,
            "username": users[row.user_id].username if row.user_id in users else None,
            "real_name": users[row.user_id].real_name if row.user_id in users else None,
            "scene": row.scene,
            "scene_label": AI_SCENE_LABELS.get(row.scene or "", row.scene),
            "model": row.model,
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "latency_ms": row.latency_ms or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "calls": calls,
            "input_tokens": int(in_tokens),
            "output_tokens": int(out_tokens),
            "avg_latency_ms": round(float(avg_latency)),
            "today_calls": today_calls,
        },
    })
