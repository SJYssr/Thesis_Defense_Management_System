"""统一 API 响应: {code, message, data} 及分页结构.

D11: 错误响应不再一律 200, 由业务 code 映射到真实 HTTP 状态码, 传输层可区分.
响应体结构保持 {code, message, data} 不变, 调用方仍可只读 body.
"""
from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")

# 显式映射: 语义明确、且不能由 code//100 推导的业务码
EXPLICIT_HTTP_STATUS: dict[int, int] = {
    40100: 401,  # 认证失败/令牌失效
    40300: 403,  # 权限不足
    40400: 404,  # 资源不存在
    40900: 409,  # 数据冲突
    42200: 422,  # 参数校验失败(显式业务码)
    42901: 429,  # 登录限流
    50000: 500,  # 服务器内部错误
    50001: 500,  # AI 服务失败
}


def http_status_for_code(code: int) -> int:
    """业务码 -> HTTP 状态码. 40100->401 / 40300->403 / 40400->404 / 400xx->400 / 42200->422 / 500xx->500."""
    if code in EXPLICIT_HTTP_STATUS:
        return EXPLICIT_HTTP_STATUS[code]
    if code >= 40000:
        derived = code // 100  # 40001 -> 400, 40010 -> 400, 42201 -> 422, 500xx -> 500
        if 400 <= derived <= 599:
            return derived
        return 400
    if 400 <= code <= 599:
        # 兼容历史调用方直接传 HTTP 状态码(如 ResponseUtil.error(msg, 400))
        return code
    return 400


class PageData(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class ResponseUtil:
    @staticmethod
    def success(data: Any = None, message: str = "success") -> JSONResponse:
        return JSONResponse({"code": 0, "message": message, "data": data})

    @staticmethod
    def error(message: str, code: int = 400) -> JSONResponse:
        """错误响应: body 结构不变, HTTP 状态码按 code 映射."""
        return JSONResponse(
            {"code": code, "message": message, "data": None},
            status_code=http_status_for_code(code),
        )

    @staticmethod
    def page(items: list[Any], total: int, page: int, page_size: int) -> JSONResponse:
        return ResponseUtil.success({"items": items, "page": page, "page_size": page_size, "total": total})


def page_dict(items: list[Any], total: int, page: int, page_size: int) -> dict:
    """非 JSONResponse 场景(如嵌套在 service 返回值中)的分页结构."""
    return {"items": items, "page": page, "page_size": page_size, "total": total}
