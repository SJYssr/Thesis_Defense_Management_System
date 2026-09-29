"""统一异常体系: 业务代码只抛业务异常, 由全局 handler 统一转成标准响应."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import ResponseUtil


class BusinessException(Exception):
    """业务异常基类, 子类只需定义 code / message."""

    code = 400
    message = "业务处理失败"

    def __init__(self, message: str | None = None, code: int | None = None):
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


class AuthenticationException(BusinessException):
    code = 40100
    message = "认证失败或登录已过期"


class PermissionDeniedException(BusinessException):
    code = 40300
    message = "没有权限执行此操作"


class ResourceNotFoundException(BusinessException):
    code = 40400
    message = "资源不存在"


class StageValidationException(BusinessException):
    """毕业设计阶段校验失败, 如开题未通过时提交中期材料."""
    code = 40001
    message = "当前毕业设计阶段不允许此操作"


class ConflictException(BusinessException):
    code = 40900
    message = "数据冲突, 请刷新后重试"


class AIServiceException(BusinessException):
    code = 50001
    message = "AI 服务处理失败"


class FileException(BusinessException):
    code = 40010
    message = "文件处理失败"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessException)
    async def business_handler(request: Request, exc: BusinessException):
        return ResponseUtil.error(exc.message, exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        msg = f"参数校验失败: {loc} {first.get('msg', '')}" if loc else "参数校验失败"
        return ResponseUtil.error(msg, 40002)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        return ResponseUtil.error(str(exc.detail), exc.status_code * 100 if exc.status_code >= 400 else 50000)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        import logging

        logging.getLogger("app").exception("Unhandled error on %s %s", request.method, request.url.path)
        return ResponseUtil.error("服务器内部错误", 50000)
