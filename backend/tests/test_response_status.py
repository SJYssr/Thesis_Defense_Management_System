"""D11: 错误响应必须映射真实 HTTP 状态码, 响应体结构保持 {code, message, data}."""
import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    AuthenticationException,
    BusinessException,
    FileException,
    PermissionDeniedException,
    ResourceNotFoundException,
    StageValidationException,
    register_exception_handlers,
)
from app.core.response import ResponseUtil, http_status_for_code


class HttpStatusMappingTest(unittest.TestCase):
    def test_documented_mapping(self):
        expected = {
            40100: 401,
            40300: 403,
            40400: 404,
            40000: 400,
            40001: 400,
            40002: 400,
            40010: 400,
            42200: 422,
            50000: 500,
            50001: 500,
        }
        for code, status in expected.items():
            self.assertEqual(http_status_for_code(code), status, f"code={code}")

    def test_extra_business_codes(self):
        self.assertEqual(http_status_for_code(40900), 409)
        self.assertEqual(http_status_for_code(42901), 429)

    def test_legacy_http_code_passthrough(self):
        self.assertEqual(http_status_for_code(400), 400)
        self.assertEqual(http_status_for_code(500), 500)

    def test_error_body_shape_and_status(self):
        response = ResponseUtil.error("权限不足", 40300)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.body), {"code": 40300, "message": "权限不足", "data": None})

    def test_success_still_200(self):
        response = ResponseUtil.success({"ok": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["code"], 0)

    def test_page_structure(self):
        body = json.loads(ResponseUtil.page([{"id": 1}], 10, 1, 20).body)
        self.assertEqual(set(body["data"].keys()), {"items", "page", "page_size", "total"})


class ExceptionHandlerTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom/auth")
        def _auth():
            raise AuthenticationException("用户名或密码错误")

        @app.get("/boom/permission")
        def _permission():
            raise PermissionDeniedException()

        @app.get("/boom/missing")
        def _missing():
            raise ResourceNotFoundException("用户不存在")

        @app.get("/boom/stage")
        def _stage():
            raise StageValidationException()

        @app.get("/boom/file")
        def _file():
            raise FileException("文件超过大小限制 50MB")

        @app.get("/boom/business")
        def _business():
            raise BusinessException("用户名已存在")

        self.client = TestClient(app)

    def test_status_and_code_pairs(self):
        cases = [
            ("/boom/auth", 401, 40100),
            ("/boom/permission", 403, 40300),
            ("/boom/missing", 404, 40400),
            ("/boom/stage", 400, 40001),
            ("/boom/file", 400, 40010),
            ("/boom/business", 400, 400),
        ]
        for path, status, code in cases:
            response = self.client.get(path)
            self.assertEqual(response.status_code, status, path)
            body = response.json()
            self.assertEqual(set(body.keys()), {"code", "message", "data"}, path)
            self.assertEqual(body["code"], code, path)
            self.assertIsNone(body["data"], path)

    def test_unknown_path_returns_404(self):
        response = self.client.get("/no-such-route")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 40400)


if __name__ == "__main__":
    unittest.main()
