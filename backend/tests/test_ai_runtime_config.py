"""AI 运行时配置单测(P11): 配置只读数据库, 不跟随任何 AI 环境变量.

改造前的行为: resolve_ai_config 的优先级是"数据库 > 环境变量", 环境变量里还留着
LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY/EMBEDDING_*/OLLAMA_* 一整套, 页面上填的值
清空就会"漂回"到 env —— 用户看到的配置与真正生效的配置会不一致。
现在唯一的环境开关是 AI_TEST_MODE(测试替身, 生产禁用)。
"""
import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.ai import providers as svc
from app.ai.embedding.factory import EmbeddingFactory, LocalHashEmbedding
from app.ai.llm.factory import (AIServiceUnavailable, LLMFactory, MockLLM, OllamaLLM,
                                reset_ollama_probe_cache)
from app.ai.runtime_config import invalidate_ai_config, resolve_ai_config
from app.core.config import settings
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIProvider
from app.models.system import SystemConfig


class _NoCacheRedis:
    def get(self, *args, **kwargs):
        return None

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0


class RuntimeTestBase(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.shared.sysconfig.redis_client", _NoCacheRedis())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        @event.listens_for(self.db, "before_flush")
        def assign_ids(sess, *_):
            next_id = getattr(assign_ids, "next_id", 800)
            for item in sess.new:
                if isinstance(item, AIProvider) and item.id is None:
                    next_id += 1
                    item.id = next_id
            assign_ids.next_id = next_id

        self._reset()

    def tearDown(self):
        self._reset()

    def _reset(self):
        LLMFactory.reset()
        EmbeddingFactory.reset()
        reset_ollama_probe_cache()
        invalidate_ai_config()

    def make(self, name="云端", kind="openai", base_url="https://api.example.com/v1",
             api_key="sk-abcdefgh1234", enabled=True):
        return svc.create_provider(self.db, name=name, kind=kind, base_url=base_url,
                                   api_key=api_key, enabled=enabled)

    def bind(self, **slots):
        svc.save_bindings(self.db, slots)
        self._reset()


class NoEnvFollowTest(RuntimeTestBase):
    """Settings 里不该再有 AI 供应商字段 —— 有字段就必然有人读它."""

    def test_settings_has_no_ai_vendor_fields(self):
        for name in ("llm_provider", "llm_base_url", "llm_api_key", "llm_model",
                     "ollama_base_url", "ollama_llm_model", "ollama_embedding_model",
                     "embedding_provider", "embedding_base_url", "embedding_api_key",
                     "embedding_model", "embedding_dim", "rerank_provider"):
            self.assertFalse(hasattr(settings, name), f"settings.{name} 必须删除, 否则配置会漂回环境变量")

    def test_env_vars_do_not_change_resolution(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "LLM_API_KEY": "sk-env",
                                     "LLM_BASE_URL": "http://127.0.0.1:9999/v1"}, clear=False):
            self._reset()
            cfg = resolve_ai_config(self.db)
        self.assertEqual(cfg.llm_provider, "auto", "未绑定时必须是 auto(未配置), 不能被 env 顶成 ollama")
        self.assertEqual(cfg.llm_base_url, "")
        self.assertFalse(cfg.llm_configured)

    def test_no_binding_means_not_configured(self):
        cfg = resolve_ai_config(self.db)
        self.assertEqual(cfg.llm_provider, "auto")
        self.assertEqual(cfg.embedding_provider, "auto")
        self.assertEqual(cfg.rerank_provider, "none")
        self.assertFalse(cfg.llm_configured)
        self.assertFalse(cfg.embedding_configured)
        self.assertFalse(cfg.rerank_configured)

    def test_binding_is_the_only_source(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "gpt-4o-mini"},
                  embedding={"provider_id": provider["id"], "model": "text-embedding-3-small"})
        cfg = resolve_ai_config(self.db, use_cache=False)
        self.assertEqual(cfg.llm_provider, "openai")
        self.assertEqual(cfg.llm_base_url, "https://api.example.com/v1")
        self.assertEqual(cfg.llm_api_key, "sk-abcdefgh1234")
        self.assertEqual(cfg.llm_model, "gpt-4o-mini")
        self.assertEqual(cfg.embedding_model, "text-embedding-3-small")
        self.assertTrue(cfg.llm_configured and cfg.embedding_configured)

    def test_global_switch_disables_every_slot(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "m"})
        svc.save_bindings(self.db, {"enabled": False})
        self._reset()
        cfg = resolve_ai_config(self.db, use_cache=False)
        self.assertFalse(cfg.llm_enabled)
        self.assertFalse(cfg.embedding_enabled)

    def test_deleted_provider_leaves_broken_slot(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "m"})
        self.db.query(AIProvider).delete()
        self.db.commit()
        self._reset()
        cfg = resolve_ai_config(self.db, use_cache=False)
        self.assertEqual(cfg.llm_provider, "auto")
        self.assertFalse(cfg.llm_configured, "供应商被删后能力位必须回到未配置, 而不是拿空串去请求")


class TestModeTest(RuntimeTestBase):
    """AI_TEST_MODE 是唯一剩下的人工开关, 只用于测试替身, 生产禁用."""

    def test_mock_covers_chat_and_embedding(self):
        with patch.object(settings, "ai_test_mode", "mock"):
            self._reset()
            cfg = resolve_ai_config(self.db)
        self.assertEqual(cfg.llm_provider, "mock")
        self.assertEqual(cfg.embedding_provider, "hash")

    def test_hash_only_replaces_embedding(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "gpt-4o-mini"})
        with patch.object(settings, "ai_test_mode", "hash"):
            self._reset()
            cfg = resolve_ai_config(self.db)
        self.assertEqual(cfg.embedding_provider, "hash")
        self.assertEqual(cfg.llm_provider, "openai", "hash 只替向量, 会话仍按真实绑定")

    def test_prod_rejects_test_mode(self):
        from app.core.config import Settings

        with self.assertRaises(ValueError):
            Settings(app_env="prod", ai_test_mode="mock", secret_key="k" * 40)

    def test_unknown_test_mode_rejected(self):
        from app.core.config import Settings

        with self.assertRaises(ValueError):
            Settings(ai_test_mode="chatgpt", secret_key="k" * 40)


class FactoryTest(RuntimeTestBase):
    def test_unbound_llm_raises_with_actionable_message(self):
        with self.assertRaises(AIServiceUnavailable) as ctx:
            LLMFactory.get(self.db)
        self.assertIn("系统设置", ctx.exception.message, "报错必须告诉用户去哪里配")

    def test_unbound_embedding_raises(self):
        with self.assertRaises(AIServiceUnavailable) as ctx:
            EmbeddingFactory.get(self.db)
        self.assertIn("系统设置", ctx.exception.message)

    def test_mock_mode_returns_stand_ins(self):
        with patch.object(settings, "ai_test_mode", "mock"):
            self._reset()
            self.assertIsInstance(LLMFactory.get(self.db), MockLLM)
            self.assertIsInstance(EmbeddingFactory.get(self.db), LocalHashEmbedding)

    def test_cloud_provider_with_key_builds_client(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "gpt-4o-mini"})
        service = LLMFactory.get(self.db)
        self.assertEqual(service.model, "gpt-4o-mini")
        self.assertEqual(service.name, "openai:gpt-4o-mini")

    def test_cloud_provider_without_key_raises(self):
        provider = self.make(api_key=None)
        self.bind(chat={"provider_id": provider["id"], "model": "gpt-4o-mini"})
        with self.assertRaises(AIServiceUnavailable) as ctx:
            LLMFactory.get(self.db)
        self.assertIn("API Key", ctx.exception.message)

    def test_ollama_provider_needs_no_probe(self):
        provider = self.make(name="本地", kind="ollama", base_url="http://127.0.0.1:11434",
                             api_key=None)
        self.bind(chat={"provider_id": provider["id"], "model": "qwen2.5:7b-instruct"})
        service = LLMFactory.get(self.db)
        self.assertIsInstance(service, OllamaLLM, "显式绑定 ollama 时不该再去探测/猜测")
        self.assertEqual(service.base_url, "http://127.0.0.1:11434/v1")

    def test_ollama_without_base_url_raises(self):
        with self.assertRaises(AIServiceUnavailable):
            OllamaLLM("m", "")

    def test_disabled_provider_stops_ai(self):
        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "m"})
        svc.update_provider(self.db, provider["id"], enabled=False)
        self._reset()
        with self.assertRaises(AIServiceUnavailable):
            LLMFactory.get(self.db)


class HealthTest(RuntimeTestBase):
    def test_probe_all_structure_and_no_raise(self):
        from app.ai.health import probe_all

        result = probe_all(self.db)
        for key in ("llm", "embedding", "rerank", "ollama", "vector_store"):
            self.assertIn(key, result)
        self.assertEqual(result["llm"]["status"], "not_configured")
        self.assertEqual(result["embedding"]["status"], "not_configured")
        self.assertEqual(result["rerank"]["status"], "disabled")
        self.assertFalse(result["llm"]["configured"])

    def test_probe_all_never_raises_on_broken_config(self):
        from app.ai.health import probe_all

        provider = self.make(base_url="https://127.0.0.1:9/v1")
        self.bind(chat={"provider_id": provider["id"], "model": "m"})
        result = probe_all(self.db)  # 不得抛
        self.assertIn(result["llm"]["status"], ("degraded", "down"))

    def test_health_status_mapping(self):
        from app.main import _health_status

        self.assertEqual(_health_status("ok", "ok"), "ok")
        self.assertEqual(_health_status("not_configured", "not_configured"), "not_configured")
        self.assertEqual(_health_status("ok", "degraded"), "degraded")

    def test_ai_model_info_exposes_bindings_and_hides_legacy_keys(self):
        from app.modules.sysconfig.router import ai_model_info
        import json

        provider = self.make()
        self.bind(chat={"provider_id": provider["id"], "model": "gpt-4o-mini"})
        data = json.loads(ai_model_info(lambda: None, self.db).body)["data"] \
            if False else json.loads(ai_model_info(None, self.db).body)["data"]
        self.assertIn("bindings", data)
        self.assertEqual(data["bindings"]["chat"]["model"], "gpt-4o-mini")
        for legacy in ("ai_llm_model", "llm_model_effective", "llm_openai_mode", "has_keys"):
            self.assertNotIn(legacy, data, f"{legacy} 属于旧口径, 已在 P11 移除")
