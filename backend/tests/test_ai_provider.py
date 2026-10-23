"""AI provider 层单测(P10-A): 真实来源选择 / 不静默降级 / 探测缓存 / 健康探测 / 指纹.

全程离线: 用 monkeypatch 替换 ollama 探测的 HTTP 层(OllamaProbe._fetch)与 SDK 客户端,
绝不真的连 127.0.0.1:11434, 也不依赖本机是否 pull 过模型。
"""
import time
import unittest
from unittest import mock

from app.ai.embedding.factory import EmbeddingFactory, LocalHashEmbedding, OllamaEmbedding
from app.ai.health import embedding_needs_reindex, probe_all
from app.ai.llm.factory import (
    AIServiceUnavailable,
    LLMFactory,
    MockLLM,
    OllamaLLM,
    OllamaProbe,
    probe_ollama,
    reset_ollama_probe_cache,
)
from app.ai import runtime_config
from app.core.config import settings

DOWN = {"reachable": False, "models": None, "latency_ms": None,
        "detail": "无法连接 http://127.0.0.1:11434/api/tags: URLError: Connection refused"}


def up(models: list[str]) -> dict:
    return {"reachable": True, "models": models, "latency_ms": 3, "detail": "stub 可达"}


class ProviderTestBase(unittest.TestCase):
    """保存/恢复被改写的 settings, 并清空工厂与探测缓存, 保证用例互不影响."""

    FIELDS = ("llm_provider", "llm_api_key", "llm_model", "ollama_llm_model",
              "embedding_provider", "embedding_api_key", "embedding_model",
              "ollama_embedding_model", "embedding_dim", "ollama_probe_ttl",
              "ollama_probe_timeout")

    def setUp(self):
        self._saved = {name: getattr(settings, name) for name in self.FIELDS}
        # 生效配置这一层单独有单测(tests/test_ai_runtime_config.py): 这里桩成"系统设置全空",
        # 既保证纯环境变量语义, 也保证单测不去连真实数据库。
        self._db_patch = mock.patch.object(runtime_config, "_db_values",
                                          side_effect=lambda db=None: {})
        self._db_patch.start()
        # 基线钉成 auto/无 key: 外部 shell 若 export 了 LLM_PROVIDER=mock(见 AGENTS.md 的 smoke
        # 命令), 用例会从 "not_configured" 变成 "disabled" —— 断言随环境漂移, 单测必须对环境免疫.
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        settings.embedding_provider = "auto"
        settings.embedding_api_key = ""
        self._reset()

    def tearDown(self):
        self._db_patch.stop()
        for name, value in self._saved.items():
            setattr(settings, name, value)
        self._reset()

    @staticmethod
    def _reset():
        runtime_config.invalidate_ai_config()
        LLMFactory.reset()
        EmbeddingFactory.reset()
        reset_ollama_probe_cache()


class LLMFactoryTest(ProviderTestBase):
    def test_auto_without_key_and_ollama_down_raises_instead_of_mock(self):
        """auto + 无 key + ollama 不可达: 必须抛 AIServiceUnavailable, 不能返回 MockLLM."""
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN):
            with self.assertRaises(AIServiceUnavailable) as ctx:
                LLMFactory.get()
            # 二次调用仍失败: 不存在"第一次失败就悄悄降级"的路径
            with self.assertRaises(AIServiceUnavailable):
                LLMFactory.get()
            self.assertEqual(LLMFactory.provider_name(), "unavailable")

        self.assertEqual(ctx.exception.code, 50301)
        message = str(ctx.exception)
        self.assertIn("未检测到可用的模型服务", message)
        self.assertIn("ollama serve", message)
        self.assertIn("LLM_API_KEY", message)
        self.assertIn("Connection refused", message, "detail 应带出探测失败原因")
        self.assertNotIsInstance(LLMFactory._instance, MockLLM)

    def test_explicit_mock_returns_mock(self):
        settings.llm_provider = "mock"
        service = LLMFactory.get()
        self.assertIsInstance(service, MockLLM)
        self.assertEqual(service.name, "mock")
        self.assertEqual(LLMFactory.provider_name(), "mock")

    def test_explicit_ollama_does_not_need_probe(self):
        """显式 ollama: 不探测直接给实例, name 前缀与 base_url 指向本地 /v1."""
        settings.llm_provider = "ollama"
        settings.ollama_llm_model = "qwen2.5:7b-instruct"
        with mock.patch.object(OllamaProbe, "_fetch", side_effect=AssertionError("不该探测")):
            service = LLMFactory.get()
        self.assertIsInstance(service, OllamaLLM)
        self.assertTrue(service.name.startswith("ollama:"), service.name)
        self.assertEqual(service.model, "qwen2.5:7b-instruct")
        self.assertIn(settings.ollama_v1_url, str(service.client.base_url))

    def test_openai_provider_without_key_raises(self):
        settings.llm_provider = "openai"
        settings.llm_api_key = ""
        with self.assertRaises(AIServiceUnavailable) as ctx:
            LLMFactory.get()
        self.assertEqual(ctx.exception.code, 50301)
        self.assertIn("LLM_API_KEY", str(ctx.exception))

    def test_auto_prefers_cloud_key_over_ollama(self):
        settings.llm_provider = "auto"
        settings.llm_api_key = "sk-test"
        with mock.patch.object(LLMFactory, "_model_override", return_value=""), \
                mock.patch.object(OllamaProbe, "_fetch", return_value=up(["bge-m3"])):
            service = LLMFactory.get()
        self.assertTrue(service.name.startswith("openai:"), service.name)

    def test_probe_cached_within_ttl_and_refreshed_after(self):
        """探测带 TTL 缓存: 连续 2 次 get() 只探测 1 次, 超过 TTL 再探测."""
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        settings.ollama_probe_ttl = 30.0
        calls = {"n": 0}

        def _fetch(base_url, timeout):
            calls["n"] += 1
            return up(["qwen2.5:7b-instruct"])

        with mock.patch.object(OllamaProbe, "_fetch", side_effect=_fetch):
            LLMFactory.get()
            LLMFactory.get()
            self.assertEqual(calls["n"], 1, "TTL 内第二次 get() 不得重复探测")

            settings.ollama_probe_ttl = 0.05
            time.sleep(0.06)
            LLMFactory.get()
        self.assertEqual(calls["n"], 2, "超过 TTL 必须重新探测")

    def test_probe_failure_never_escapes_as_raw_exception(self):
        """探测层自身异常只体现为不可用(转 AIServiceUnavailable), 不把原始异常抛给调用方."""
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        with mock.patch.object(OllamaProbe, "_fetch", side_effect=RuntimeError("boom")):
            self.assertFalse(probe_ollama()["reachable"])
            with self.assertRaises(AIServiceUnavailable):
                LLMFactory.get()


class EmbeddingFactoryTest(ProviderTestBase):
    def test_auto_without_key_and_ollama_down_raises_instead_of_hash(self):
        settings.embedding_provider = "auto"
        settings.embedding_api_key = ""
        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN):
            with self.assertRaises(AIServiceUnavailable) as ctx:
                EmbeddingFactory.get()
        self.assertEqual(ctx.exception.code, 50301)
        self.assertIn("EMBEDDING_API_KEY", str(ctx.exception))
        self.assertNotIsInstance(EmbeddingFactory._instance, LocalHashEmbedding)

    def test_explicit_hash_is_allowed(self):
        settings.embedding_provider = "hash"
        settings.embedding_dim = 512
        service = EmbeddingFactory.get()
        self.assertIsInstance(service, LocalHashEmbedding)
        self.assertEqual(service.dim, 512)

    def test_explicit_ollama_embedding(self):
        settings.embedding_provider = "ollama"
        settings.ollama_embedding_model = "bge-m3"
        with mock.patch.object(OllamaProbe, "_fetch", side_effect=AssertionError("不该探测")):
            service = EmbeddingFactory.get()
        self.assertIsInstance(service, OllamaEmbedding)
        self.assertEqual(service.name, "ollama:bge-m3")
        self.assertIn(settings.ollama_v1_url, str(service.client.base_url))

    def test_openai_embedding_without_key_raises(self):
        settings.embedding_provider = "openai"
        settings.embedding_api_key = ""
        with self.assertRaises(AIServiceUnavailable) as ctx:
            EmbeddingFactory.get()
        self.assertIn("EMBEDDING_API_KEY", str(ctx.exception))

    def test_fingerprint_contains_name_and_dim(self):
        self.assertEqual(LocalHashEmbedding(1024).fingerprint, "local-hash:1024")
        ollama = OllamaEmbedding(model="bge-m3", dim=1024)
        self.assertEqual(ollama.fingerprint, "ollama:bge-m3:1024")
        self.assertIn("bge-m3", ollama.fingerprint)
        self.assertIn("1024", ollama.fingerprint)

    def test_actual_dim_overrides_configured_dim(self):
        """维度以模型实际输出为准(EMBEDDING_DIM 不是唯一真相), 并随之更新指纹."""
        settings.embedding_dim = 1024
        service = OllamaEmbedding(model="bge-m3", dim=1024)
        response = mock.Mock()
        response.data = [mock.Mock(embedding=[0.1] * 8)]
        service.client = mock.Mock()
        service.client.embeddings.create.return_value = response
        with self.assertLogs("app.ai.embedding", level="WARNING") as logs:
            vectors = service.embed(["你好"])
        self.assertEqual(len(vectors[0]), 8)
        self.assertEqual(service.dim, 8)
        self.assertEqual(service.fingerprint, "ollama:bge-m3:8")
        self.assertTrue(any("维度" in line for line in logs.output), logs.output)


class HealthProbeTest(ProviderTestBase):
    def test_all_down_does_not_raise(self):
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        settings.embedding_provider = "auto"
        settings.embedding_api_key = ""
        with mock.patch.object(OllamaProbe, "_fetch", return_value=DOWN):
            data = probe_all()
        # 什么都没配(也没有本地 ollama)是 not_configured, 不是"配了但连不上"(degraded/down)
        self.assertEqual(data["llm"]["status"], "not_configured")
        self.assertEqual(data["embedding"]["status"], "not_configured")
        self.assertFalse(data["llm"]["configured"])
        self.assertIn("系统设置", data["llm"]["detail"])
        self.assertEqual(data["llm"]["provider"], "auto")
        self.assertFalse(data["ollama"]["reachable"])
        self.assertIn("detail", data["llm"])
        for key in ("llm", "embedding", "rerank", "vector_store", "ollama"):
            self.assertIn(key, data)

    def test_all_ok(self):
        settings.llm_provider = "auto"
        settings.llm_api_key = ""
        settings.ollama_llm_model = "qwen2.5:7b-instruct"
        settings.embedding_provider = "auto"
        settings.embedding_api_key = ""
        settings.ollama_embedding_model = "bge-m3"
        settings.embedding_dim = 1024
        with mock.patch.object(OllamaProbe, "_fetch",
                               return_value=up(["qwen2.5:7b-instruct", "bge-m3:latest"])):
            data = probe_all()
        self.assertEqual(data["llm"]["status"], "ok", data["llm"])
        self.assertEqual(data["llm"]["model"], "qwen2.5:7b-instruct")
        self.assertEqual(data["embedding"]["status"], "ok", data["embedding"])
        self.assertEqual(data["embedding"]["dim"], 1024)
        self.assertEqual(data["embedding"]["fingerprint"], "ollama:bge-m3:1024")
        self.assertTrue(data["ollama"]["reachable"])
        self.assertEqual(sorted(data["ollama"]["models"]), ["bge-m3:latest", "qwen2.5:7b-instruct"])

    def test_model_missing_is_degraded_not_ok(self):
        """来源可达但模型没 pull: 不能报 ok(那正是"看起来正常其实不可用")."""
        settings.llm_provider = "ollama"
        settings.ollama_llm_model = "qwen2.5:7b-instruct"
        settings.embedding_provider = "ollama"
        settings.ollama_embedding_model = "bge-m3"
        with mock.patch.object(OllamaProbe, "_fetch", return_value=up([])):
            data = probe_all()
        self.assertEqual(data["llm"]["status"], "degraded")
        self.assertIn("ollama pull", data["llm"]["detail"])
        self.assertEqual(data["embedding"]["status"], "degraded")

    def test_probe_all_swallows_unexpected_exception(self):
        with mock.patch("app.ai.llm.factory.probe_ollama", side_effect=RuntimeError("boom")):
            data = probe_all()
        self.assertEqual(data["llm"]["status"], "down")
        self.assertIn("RuntimeError", data["llm"]["detail"])
        self.assertEqual(data["ollama"]["reachable"], False)

    def test_mock_and_hash_are_disabled_not_fake_ok(self):
        settings.llm_provider = "mock"
        settings.embedding_provider = "hash"
        data = probe_all()
        self.assertEqual(data["llm"]["status"], "disabled")
        self.assertEqual(data["embedding"]["status"], "disabled")
        self.assertEqual(data["embedding"]["fingerprint"], f"local-hash:{settings.embedding_dim}")

    def test_rerank_reports_file_presence_only(self):
        settings.rerank_provider = "none"
        data = probe_all()
        self.assertEqual(data["rerank"]["status"], "disabled")
        self.assertFalse(data["rerank"]["available"])
        settings.rerank_provider = "auto"
        data = probe_all()
        self.assertIn("available", data["rerank"])
        self.assertEqual(data["rerank"]["model_dir"], settings.rerank_model_dir)


class EmbeddingReindexTest(ProviderTestBase):
    def setUp(self):
        super().setUp()
        settings.embedding_provider = "hash"
        settings.embedding_dim = 1024
        EmbeddingFactory.reset()

    def test_missing_key_means_reindex(self):

        with mock.patch("app.db.redis.redis_client.get", return_value=None):
            self.assertTrue(embedding_needs_reindex())

    def test_same_fingerprint_needs_no_reindex(self):

        with mock.patch("app.db.redis.redis_client.get", return_value=b"local-hash:1024"):
            self.assertFalse(embedding_needs_reindex())

    def test_changed_fingerprint_needs_reindex(self):

        with mock.patch("app.db.redis.redis_client.get", return_value=b"ollama:bge-m3:1024"):
            self.assertTrue(embedding_needs_reindex())

    def test_redis_failure_is_not_an_error(self):
        """缓存故障不能把管理端页面打成错误, 也不能谎报需要重建."""

        with mock.patch("app.db.redis.redis_client.get", side_effect=RuntimeError("redis down")):
            with self.assertLogs("app.ai.health", level="WARNING"):
                self.assertFalse(embedding_needs_reindex())


class HealthEndpointTest(ProviderTestBase):
    def test_health_status_mapping(self):
        from app.main import health

        ok = {"llm": {"status": "ok", "provider": "ollama", "model": "m", "latency_ms": 1, "detail": ""},
              "embedding": {"status": "ok", "provider": "ollama", "model": "e", "latency_ms": 1,
                            "detail": "", "dim": 1024, "fingerprint": "ollama:e:1024"},
              "rerank": {"status": "ok", "provider": "auto", "model_dir": "/x", "available": True},
              "vector_store": {"mode": "mysql"}, "ollama": {"reachable": True, "base_url": "u", "models": []}}
        with mock.patch("app.ai.health.probe_all", return_value=ok):
            body = health()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["llm_provider"], "ollama")
        self.assertIn("redis", body)
        self.assertEqual(body["app"], settings.app_name)

        degraded = {**ok, "embedding": {**ok["embedding"], "status": "down"}}
        with mock.patch("app.ai.health.probe_all", return_value=degraded):
            body = health()
        self.assertEqual(body["status"], "degraded", "任一 AI 链路不可用都不能报 ok")

    def test_health_ai_returns_full_structure(self):
        from app.main import health_ai

        payload = {"llm": {"status": "down", "provider": "unavailable", "model": "",
                           "latency_ms": None, "detail": "x"},
                   "embedding": {"status": "down", "provider": "unavailable", "model": "",
                                 "latency_ms": None, "detail": "x", "dim": 0, "fingerprint": ""},
                   "rerank": {"status": "down", "provider": "auto", "model_dir": "/x", "available": False},
                   "vector_store": {"mode": "unknown"},
                   "ollama": {"reachable": False, "base_url": "u", "models": []}}
        with mock.patch("app.ai.health.probe_all", return_value=payload):
            self.assertEqual(health_ai(), payload)


if __name__ == "__main__":
    unittest.main()
