"""AI 运行时配置单测(P10-A2): 系统设置(DB) > 环境变量的生效链路.

覆盖: DB 覆盖 env / 空 DB 回退 env / provider 非法回退 auto / enabled=false /
显式 openai 缺 key 的提示语 / 管理端掩码与 has_keys / 提交掩码不覆盖真 Key /
保存后新 Key 立即生效 / 显式维度与实际维度不符 / not_configured 与 degraded 的区分。

全程离线: 数据库用 sqlite 内存库, Redis 用桩, ollama 的 HTTP 层被 monkeypatch,
云端 SDK 客户端按需替换 —— 不连 127.0.0.1:11434, 不依赖本机是否拉过模型。
"""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai import runtime_config
from app.ai.embedding.factory import EmbeddingFactory
from app.ai.health import probe_all
from app.ai.llm.factory import (
    AIServiceUnavailable,
    LLMFactory,
    OllamaProbe,
    probe_ollama,
    reset_ollama_probe_cache,
)
from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser, require_admin
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.modules.sysconfig.router import (
    CLEAR_SECRET_SENTINEL,
    ConfigUpdateRequest,
    ai_model_info,
    list_configs,
    update_configs_api,
)
from app.shared.sysconfig import get_all, update_configs

ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
DOWN = {"reachable": False, "models": None, "latency_ms": None,
        "detail": "stub: 连不上 http://127.0.0.1:11434/api/tags"}


class _NoCacheRedis:
    """sysconfig 的 Redis 缓存桩: 永远未命中, 写操作空转."""

    def get(self, key):
        return None

    def set(self, *args, **kwargs):
        return True

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0

    def exists(self, *args, **kwargs):
        return 0


class RuntimeConfigTestBase(unittest.TestCase):
    """sqlite 内存库 + Redis 桩 + 工厂/缓存复位; 所有工厂调用都显式传 db, 不碰真库."""

    FIELDS = (
        "llm_provider", "llm_base_url", "llm_api_key", "llm_model",
        "embedding_provider", "embedding_base_url", "embedding_api_key", "embedding_model",
        "embedding_dim", "ollama_base_url", "ollama_llm_model", "ollama_embedding_model",
        "ollama_probe_timeout",
    )

    def setUp(self):
        self._saved = {name: getattr(settings, name) for name in self.FIELDS}
        # StaticPool + check_same_thread=False: TestClient 会在工作线程里跑同步端点,
        # 内存库必须能跨线程共享同一条连接, 否则端到端用例报 SQLite thread 错误
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._redis_patch = mock.patch("app.shared.sysconfig.redis_client", _NoCacheRedis())
        self._redis_patch.start()
        # 基线钉成 auto/无 key: 外部 shell 若 export 了 LLM_PROVIDER=mock(见 AGENTS.md 的 smoke
        # 命令), 用例会从 "not_configured" 变成 "disabled" —— 断言随环境漂移, 单测必须对环境免疫.
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        settings.embedding_provider = "auto"
        settings.embedding_api_key = ""
        self._reset()

    def tearDown(self):
        self._redis_patch.stop()
        self.db.close()
        self.engine.dispose()
        for name, value in self._saved.items():
            setattr(settings, name, value)
        self._reset()

    @staticmethod
    def _reset():
        runtime_config.invalidate_ai_config()
        LLMFactory.reset()
        EmbeddingFactory.reset()
        reset_ollama_probe_cache()

    def _write(self, **items):
        """写系统设置(相当于管理员在页面上保存), 并让生效配置立刻失效."""
        update_configs(self.db, items, ADMIN.id)
        runtime_config.invalidate_ai_config()

    def _env(self, **items):
        for name, value in items.items():
            setattr(settings, name, value)
        runtime_config.invalidate_ai_config()

    def resolve(self):
        return runtime_config.resolve_ai_config(self.db)


class EffectiveConfigTest(RuntimeConfigTestBase):
    def test_db_overrides_env(self):
        self._env(llm_provider="auto", llm_base_url="https://env.example.com/v1",
                  llm_api_key="", llm_model="env-model")
        self._write(ai_llm_provider="openai", ai_llm_base_url="https://api.db.example/v1/",
                    ai_llm_api_key="sk-db-key-123456", ai_llm_model="db-model")

        cfg = self.resolve()

        self.assertEqual(cfg.llm_provider, "openai")
        self.assertEqual(cfg.llm_base_url, "https://api.db.example/v1")  # 尾斜杠被去掉
        self.assertEqual(cfg.llm_api_key, "sk-db-key-123456")
        self.assertEqual(cfg.llm_model, "db-model")
        self.assertTrue(cfg.llm_configured)
        self.assertIn("api_key", cfg.llm_from_db)
        # 密钥不得出现在日志/打印里
        self.assertNotIn("sk-db-key-123456", repr(cfg))

    def test_empty_db_falls_back_to_env(self):
        self._env(llm_provider="openai", llm_base_url="https://env.example.com/v1",
                  llm_api_key="", llm_model="env-model",
                  embedding_provider="openai", embedding_base_url="https://env.example.com/v1",
                  embedding_api_key="", embedding_model="env-embedding", embedding_dim=768)

        cfg = self.resolve()

        self.assertEqual(cfg.llm_provider, "openai")
        self.assertEqual(cfg.llm_base_url, "https://env.example.com/v1")
        self.assertEqual(cfg.llm_model, "env-model")
        self.assertEqual(cfg.embedding_dim, 768)
        self.assertFalse(cfg.embedding_dim_explicit)
        self.assertEqual(cfg.llm_from_db, frozenset())

    def test_invalid_provider_falls_back_to_auto(self):
        self._env(llm_provider="auto")
        self._write(ai_llm_provider="banana")

        with self.assertLogs("app.ai.runtime", level="WARNING"):
            cfg = self.resolve()

        self.assertEqual(cfg.llm_provider, "auto")

    def test_db_embedding_dim_is_explicit(self):
        self._write(ai_embedding_dim="512")

        cfg = self.resolve()

        self.assertEqual(cfg.embedding_dim, 512)
        self.assertTrue(cfg.embedding_dim_explicit)


class FactoryDecisionTest(RuntimeConfigTestBase):
    def test_disabled_llm_raises(self):
        self._write(ai_llm_enabled="false")

        with self.assertRaises(AIServiceUnavailable) as ctx:
            LLMFactory.get(self.db)

        self.assertIn("关闭", str(ctx.exception))

    def test_disabled_embedding_raises(self):
        self._write(ai_embedding_enabled="false")

        with self.assertRaises(AIServiceUnavailable) as ctx:
            EmbeddingFactory.get(self.db)

        self.assertIn("关闭", str(ctx.exception))

    def test_explicit_openai_without_key_raises_with_settings_hint(self):
        self._env(llm_api_key="")
        self._write(ai_llm_provider="openai", ai_llm_base_url="https://api.db.example/v1")

        with self.assertRaises(AIServiceUnavailable) as ctx:
            LLMFactory.get(self.db)

        message = str(ctx.exception)
        self.assertIn("系统设置", message)
        self.assertIn("API Key", message)
        self.assertEqual(ctx.exception.code, 50301)

    def test_explicit_openai_embedding_without_key_raises_with_settings_hint(self):
        self._env(embedding_api_key="")
        self._write(ai_embedding_provider="openai")

        with self.assertRaises(AIServiceUnavailable) as ctx:
            EmbeddingFactory.get(self.db)

        message = str(ctx.exception)
        self.assertIn("系统设置", message)
        self.assertIn("EMBEDDING_API_KEY", message)

    def test_auto_prefers_cloud_when_db_key_present(self):
        self._write(ai_llm_base_url="https://api.db.example/v1",
                    ai_llm_api_key="sk-auto-key-000111", ai_llm_model="db-model")

        service = LLMFactory.get(self.db)

        self.assertEqual(service.name, "openai:db-model")
        self.assertEqual(service.model, "db-model")
        self.assertEqual(str(service.client.base_url).rstrip("/"), "https://api.db.example/v1")

    def test_invalidate_makes_new_key_effective(self):
        self._write(ai_llm_base_url="https://api.one.example/v1", ai_llm_api_key="sk-one-1111",
                    ai_llm_model="model-one")
        first = LLMFactory.get(self.db)

        # 管理员在系统设置里换成另一家服务: 保存接口会 invalidate, 这里模拟同样效果
        self._write(ai_llm_base_url="https://api.two.example/v9", ai_llm_api_key="sk-two-2222",
                    ai_llm_model="model-two")
        second = LLMFactory.get(self.db)

        self.assertIsNot(first, second)
        self.assertEqual(second.client.api_key, "sk-two-2222")
        self.assertEqual(str(second.client.base_url).rstrip("/"), "https://api.two.example/v9")
        self.assertEqual(second.model, "model-two")

    def test_embedding_explicit_dim_mismatch_raises(self):
        self._write(ai_embedding_provider="openai", ai_embedding_base_url="https://api.db.example/v1",
                    ai_embedding_api_key="sk-embed-3333", ai_embedding_model="emb-model",
                    ai_embedding_dim="8")
        service = EmbeddingFactory.get(self.db)
        # 模型实际只输出 4 维: 与系统设置的 8 维冲突必须显式失败, 不能静默写坏索引
        service.client = SimpleNamespace(embeddings=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 4) for _ in kwargs["input"]])
        ))

        with self.assertRaises(AIServiceUnavailable) as ctx:
            service.embed(["你好"])

        message = str(ctx.exception)
        self.assertIn("维度", message)
        self.assertIn("8", message)
        self.assertIn("4", message)

    def test_embedding_dim_from_db_drives_fingerprint(self):
        self._write(ai_embedding_provider="openai", ai_embedding_base_url="https://api.db.example/v1",
                    ai_embedding_api_key="sk-embed-4444", ai_embedding_model="emb-model",
                    ai_embedding_dim="768")

        service = EmbeddingFactory.get(self.db)

        self.assertEqual(service.dim, 768)
        self.assertEqual(service.fingerprint, "openai:emb-model:768")


class MaskingTest(RuntimeConfigTestBase):
    def test_list_configs_masks_api_key(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234", ai_embedding_api_key="sk-emb-xyz98765")

        response = list_configs(ADMIN, self.db)
        body = json.loads(response.body)
        values = {item["key"]: item["value"] for item in body["data"]}

        self.assertEqual(values["ai_llm_api_key"], "***1234")
        self.assertEqual(values["ai_embedding_api_key"], "***8765")
        self.assertNotIn("sk-live-abcdef1234", response.body.decode())
        self.assertNotIn("sk-emb-xyz98765", response.body.decode())

    def test_ai_model_info_reports_has_keys_and_masks(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234", ai_embedding_api_key="sk-emb-xyz98765")

        # 探针也要桩住: 配了 Key 就会走云端探测, 单测不允许发真实请求
        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN), \
                mock.patch("app.ai.health._cloud_llm_probe",
                           side_effect=RuntimeError("offline")), \
                mock.patch("app.ai.health._cloud_embedding_probe",
                           side_effect=RuntimeError("offline")):
            response = ai_model_info(ADMIN, self.db)

        body = json.loads(response.body)
        data = body["data"]
        self.assertEqual(data["has_keys"], {"llm": True, "embedding": True, "rerank": False})
        self.assertIn("llm_status", data)
        self.assertIn("needs_reindex", data)
        self.assertNotIn("sk-live-abcdef1234", response.body.decode())

    def test_put_masked_value_does_not_overwrite_key(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234")

        body = ConfigUpdateRequest(items={"ai_llm_api_key": "***1234", "ai_llm_model": "m-new"})
        update_configs_api(body, ADMIN, self.db)

        stored = get_all(self.db)
        self.assertEqual(stored["ai_llm_api_key"], "sk-live-abcdef1234")
        self.assertEqual(stored["ai_llm_model"], "m-new")

    def test_put_empty_key_does_not_overwrite_key(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234")

        body = ConfigUpdateRequest(items={"ai_llm_api_key": "", "ai_llm_model": "m-2"})
        update_configs_api(body, ADMIN, self.db)

        self.assertEqual(get_all(self.db)["ai_llm_api_key"], "sk-live-abcdef1234")

    def test_put_only_masked_value_is_rejected_as_no_change(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234")

        body = ConfigUpdateRequest(items={"ai_llm_api_key": "***1234"})
        with self.assertRaises(BusinessException) as ctx:
            update_configs_api(body, ADMIN, self.db)

        self.assertEqual(ctx.exception.code, 40001)
        self.assertIn("不修改", str(ctx.exception))

    def test_put_clear_sentinel_clears_key(self):
        self._write(ai_llm_api_key="sk-live-abcdef1234")

        body = ConfigUpdateRequest(items={"ai_llm_api_key": CLEAR_SECRET_SENTINEL})
        update_configs_api(body, ADMIN, self.db)

        self.assertEqual(get_all(self.db)["ai_llm_api_key"], "")

    def test_put_response_never_echoes_plaintext_key(self):
        body = ConfigUpdateRequest(items={"ai_llm_api_key": "sk-brand-new-7777"})

        response = update_configs_api(body, ADMIN, self.db)

        self.assertNotIn("sk-brand-new-7777", response.body.decode())
        self.assertIn("***7777", response.body.decode())

    def test_put_takes_effect_for_factory_without_restart(self):
        self._write(ai_llm_base_url="https://api.one.example/v1", ai_llm_api_key="sk-one-1111",
                    ai_llm_model="model-one")
        first = LLMFactory.get(self.db)

        body = ConfigUpdateRequest(items={"ai_llm_base_url": "https://api.two.example/v9",
                                          "ai_llm_api_key": "sk-two-2222",
                                          "ai_llm_model": "model-two"})
        update_configs_api(body, ADMIN, self.db)
        second = LLMFactory.get(self.db)

        self.assertIsNot(first, second)
        self.assertEqual(second.client.api_key, "sk-two-2222")
        self.assertEqual(second.model, "model-two")


class HealthStatusTest(RuntimeConfigTestBase):
    def test_not_configured_when_nothing_set(self):
        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN):
            data = probe_all(self.db)

        self.assertEqual(data["llm"]["status"], "not_configured")
        self.assertFalse(data["llm"]["configured"])
        self.assertIn("系统设置", data["llm"]["detail"])
        self.assertEqual(data["embedding"]["status"], "not_configured")
        self.assertFalse(data["embedding"]["configured"])

    def test_degraded_when_configured_but_probe_fails(self):
        self._write(ai_llm_provider="openai", ai_llm_base_url="https://api.db.example/v1",
                    ai_llm_api_key="sk-degraded-5555", ai_llm_model="m1",
                    ai_embedding_provider="openai",
                    ai_embedding_base_url="https://api.db.example/v1",
                    ai_embedding_api_key="sk-degraded-6666", ai_embedding_model="e1")

        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN), \
                mock.patch("app.ai.health._cloud_llm_probe",
                           side_effect=RuntimeError("401 Unauthorized")), \
                mock.patch("app.ai.health._cloud_embedding_probe",
                           side_effect=RuntimeError("401 Unauthorized")):
            data = probe_all(self.db)

        self.assertEqual(data["llm"]["status"], "degraded")
        self.assertTrue(data["llm"]["configured"])
        self.assertIn("401", data["llm"]["detail"])
        self.assertEqual(data["embedding"]["status"], "degraded")
        self.assertTrue(data["embedding"]["configured"])

    def test_probe_all_never_raises_when_ollama_probe_blows_up(self):
        with mock.patch.object(OllamaProbe, "_fetch", side_effect=RuntimeError("boom")):
            data = probe_all(self.db)
            info = probe_ollama()

        self.assertFalse(data["ollama"]["reachable"])
        self.assertFalse(info["reachable"])

    def test_health_endpoint_status_mapping(self):
        from app.main import _health_status

        self.assertEqual(_health_status("ok", "ok"), "ok")
        self.assertEqual(_health_status("not_configured", "not_configured"), "not_configured")
        self.assertEqual(_health_status("ok", "not_configured"), "degraded")
        self.assertEqual(_health_status("degraded", "ok"), "degraded")
        self.assertEqual(_health_status("down", "disabled"), "degraded")


class HttpMaskingTest(RuntimeConfigTestBase):
    """端到端: 真实 HTTP 响应里不能出现明文 Key(验收第 4 条)."""

    def test_admin_system_configs_over_http_masks_key(self):
        from app.main import app

        self._write(ai_llm_api_key="sk-http-plain-9999")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_admin] = lambda: ADMIN
        try:
            response = TestClient(app).get("/api/v1/admin/system-configs")
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entry = next(i for i in payload["data"] if i["key"] == "ai_llm_api_key")
        self.assertEqual(entry["value"], "***9999")
        self.assertNotIn("sk-http-plain-9999", response.text)
