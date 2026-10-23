"""统一审计记录入口(D22): 一处写 OperationLog, 自动补 result/method/path/ip.

用法:
    audit.record(db, actor, "USER", "CREATE_USER", detail={"username": "zhangsan"})
    audit.record(db, actor, "USER", "CREATE_USER", "FAIL", request=request, detail={"reason": str(exc)})

设计要点:
- 签名固定为 record(db, actor, module, action, result="SUCCESS", **fields), 便于统一接入;
- method/path/ip 优先取显式字段, 其次取 request, 最后取 request 上下文绑定;
- 其余业务字段统一序列化进 detail(JSON), 不新增表列;
- 审计写入自身失败只记 warning, 不改变业务结果(审计不应成为单点故障).
"""
import json
import logging
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.system import OperationLog

logger = logging.getLogger("app")

RESULT_SUCCESS = "SUCCESS"
RESULT_FAIL = "FAIL"

# detail 列长度保护(Text 列, 但避免超长日志撑爆接口)
MAX_DETAIL_LENGTH = 4000

_CURRENT_REQUEST: ContextVar[Request | None] = ContextVar("audit_current_request", default=None)


def bind_request(request: Request):
    """登记当前请求, 使 record 无需显式传 request 也能补 method/path/ip."""
    return _CURRENT_REQUEST.set(request)


def reset_request(token) -> None:
    _CURRENT_REQUEST.reset(token)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def _actor_ids(actor: Any) -> tuple[int | None, str | None]:
    if actor is None:
        return None, None
    user_id = getattr(actor, "id", None)
    username = getattr(actor, "username", None)
    return user_id, username


def _dump_detail(detail: Any, extra: dict[str, Any]) -> str | None:
    if detail is None and not extra:
        return None
    payload: Any = {}
    if detail is not None:
        if isinstance(detail, str):
            payload = {"detail": detail} if extra else detail
        else:
            payload = dict(detail) if isinstance(detail, dict) else {"detail": detail}
            payload.update(extra)
    else:
        payload = extra
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(payload)
    return text[:MAX_DETAIL_LENGTH]


def record(
    db: Session,
    actor: Any,
    module: str,
    action: str,
    result: str = RESULT_SUCCESS,
    **fields: Any,
) -> OperationLog | None:
    """写入一条操作日志.

    fields 可含: request / method / path / ip / detail / request_id / commit 及任意业务字段.
    """
    request = fields.pop("request", None) or _CURRENT_REQUEST.get()
    commit = fields.pop("commit", True)
    detail = fields.pop("detail", None)
    actor_id, actor_name = _actor_ids(actor)
    user_id = fields.pop("user_id", None) or fields.pop("actor_id", None) or actor_id
    username = fields.pop("username", None) or actor_name

    entry = OperationLog(
        user_id=user_id,
        username=username,
        module=module,
        action=action,
        result=result,
        method=(fields.pop("method", None) or (request.method if request is not None else None)),
        path=(fields.pop("path", None) or (request.url.path if request is not None else None)),
        request_id=fields.pop("request_id", None) or (
            request.headers.get("x-request-id") if request is not None else None
        ),
        ip=(fields.pop("ip", None) or _client_ip(request)),
        detail=_dump_detail(detail, fields),
    )

    try:
        entry.username = entry.username or "anonymous"
        db.add(entry)
        if commit:
            db.commit()
        return entry
    except SQLAlchemyError as exc:  # 审计失败不影响主流程
        logger.warning("写入操作日志失败(module=%s action=%s): %s", module, action, exc)
        if commit:
            db.rollback()
        return None
