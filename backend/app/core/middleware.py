"""请求上下文中间件: 追踪号 + 请求绑定 + 慢请求告警.

为什么需要它:
`app/shared/audit.py` 的约定是"业务层调用 audit.record 时可以不显式传 request",
缺省值来自 `_CURRENT_REQUEST` 这个 ContextVar; 而 `audit.record` 落库时的
request_id 列、method/path/ip 也都从 request 上取。没有中间件登记请求时:

- `_CURRENT_REQUEST` 恒为 None, 不传 request 的调用日志里 method/path/ip 全空;
- `request_id` 只从入站 header 取, 而没有任何组件写这个 header, 于是恒为 NULL。

结果就是 operation_log 里多条日志无法串成"同一次请求", 排障时只能靠时间戳猜。
本中间件补上这一环: 沿用入站 x-request-id(网关/前端传的), 没有就新生成一个,
落到 request.state 供审计读取, 并回写响应头便于前端报错时附带给运维。
"""
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

from app.shared import audit

logger = logging.getLogger("app")

# 超过这个耗时就打 warning。阈值取 1s: 本系统绝大多数接口是单表读写,
# 正常都在几十毫秒内, 超过 1s 基本等于"慢查询或外部依赖抖动", 值得留痕。
SLOW_REQUEST_MS = 1000

HEADER = "x-request-id"


def attach_request_context(app: FastAPI) -> None:
    """把请求上下文中间件挂到 app 上(独立成函数, 测试可挂到自建 app 上复用同一实现)."""

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        request_id = request.headers.get(HEADER) or uuid4().hex
        request.state.request_id = request_id
        token = audit.bind_request(request)
        started = perf_counter()
        try:
            response = await call_next(request)
        finally:
            audit.reset_request(token)

        elapsed_ms = (perf_counter() - started) * 1000
        response.headers[HEADER] = request_id
        if elapsed_ms >= SLOW_REQUEST_MS:
            logger.warning(
                "慢请求 %.0fms %s %s rid=%s", elapsed_ms, request.method, request.url.path, request_id
            )
        return response
