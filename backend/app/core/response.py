"""统一 API 响应: {code, message, data} 及分页结构."""
from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


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
        return JSONResponse({"code": code, "message": message, "data": None}, status_code=200)

    @staticmethod
    def page(items: list[Any], total: int, page: int, page_size: int) -> JSONResponse:
        return ResponseUtil.success({"items": items, "page": page, "page_size": page_size, "total": total})


def page_dict(items: list[Any], total: int, page: int, page_size: int) -> dict:
    """非 JSONResponse 场景(如嵌套在 service 返回值中)的分页结构."""
    return {"items": items, "page": page, "page_size": page_size, "total": total}
