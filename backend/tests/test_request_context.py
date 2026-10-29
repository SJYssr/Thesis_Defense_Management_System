"""请求上下文中间件: x-request-id 生成/沿用 + 审计日志能拿到 request_id 与 method/path/ip.

这个测试盯的是一个**静默失败**: 审计约定业务层调用 audit.record 时可以不传 request,
缺省从 ContextVar 取。如果中间件没登记请求(或 ContextVar 没穿透中间件的任务边界),
record 不会报错, 只是把 request_id/method/path/ip 全写成 NULL —— 单看日志表只会觉得
"这些字段还没做", 看不出是链路断了。所以这里既断言响应头, 也断言库里真的落了值。
"""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.middleware import HEADER, attach_request_context
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401 — 建表前先注册全部模型
from app.models.system import OperationLog
from app.shared import audit


class RequestContextMiddlewareTest(unittest.TestCase):
    def setUp(self):
        # StaticPool + check_same_thread=False: 内存库按连接隔离, 而 TestClient 的请求跑在
        # 线程池里, 不共用连接就会在另一个空库里建不到表(audit 的异常被吞, 表现为"查无日志")。
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        app = FastAPI()
        attach_request_context(app)

        @app.get("/probe")
        def probe():
            # 刻意不传 request: 模拟业务层最省事的写法, 走 ContextVar 缺省值
            entry = audit.record(self.db, None, "PROBE", "READ")
            return {"request_id": entry.request_id if entry else None}

        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def _last_log(self) -> OperationLog:
        return self.db.execute(select(OperationLog).order_by(OperationLog.id.desc())).scalars().first()

    def test_generates_request_id_when_absent(self):
        resp = self.client.get("/probe")
        self.assertEqual(200, resp.status_code)
        rid = resp.headers.get(HEADER)
        self.assertTrue(rid, "响应必须带上 x-request-id, 否则前端报错时无从关联服务端日志")
        self.assertGreaterEqual(len(rid), 16)

    def test_reuses_inbound_request_id(self):
        resp = self.client.get("/probe", headers={HEADER: "trace-from-gateway-0001"})
        self.assertEqual("trace-from-gateway-0001", resp.headers.get(HEADER))

    def test_audit_record_gets_context_without_explicit_request(self):
        resp = self.client.get("/probe", headers={HEADER: "trace-abc-123"})
        rid = resp.headers[HEADER]
        self.assertEqual("trace-abc-123", rid)

        log = self._last_log()
        self.assertIsNotNone(log, "audit.record 应写入一条 operation_log")
        self.assertEqual("trace-abc-123", log.request_id, "request_id 必须与响应头一致, 否则日志串不起一次请求")
        # 下面三项同样靠 ContextVar 兜底, 缺了中间件就是 NULL
        self.assertEqual("GET", log.method)
        self.assertEqual("/probe", log.path)
        self.assertIsNotNone(log.ip, "客户端 IP 应被记录")

    def test_context_is_reset_between_requests(self):
        self.client.get("/probe", headers={HEADER: "first"})
        self.client.get("/probe", headers={HEADER: "second"})
        log = self._last_log()
        self.assertEqual("second", log.request_id, "上一个请求的 ContextVar 不应泄漏到下一个请求")


if __name__ == "__main__":
    unittest.main()
