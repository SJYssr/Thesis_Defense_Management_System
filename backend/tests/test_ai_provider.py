"""AI 供应商实体层单测(P11).

改造前的问题: AI 配置散在 16 个 ai_llm_*/ai_embedding_*/ai_rerank_* 配置键里,
Base URL 与 Key 每个能力各填一遍, 密钥明文进 system_config, 模型名手填, 运行期还会
跟随环境变量 —— 页面复杂、口径混乱。P11 收敛成一张 ai_provider 表 + 三个绑定键。

本文件覆盖实体层: 加密、CRUD、预设、绑定、旧配置折算(幂等)。
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.ai import providers as svc
from app.ai.crypto import decrypt_api_key, encrypt_api_key, key_hint
from app.ai.providers import KINDS, LEGACY_KEYS, PRESETS
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIProvider
from app.models.system import SystemConfig


class _NoCacheRedis:
    """断掉 system_config 的 Redis 缓存, 让读取只看到本次 sqlite 的配置行."""

    def get(self, *args, **kwargs):
        return None

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0


class ProviderTestBase(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.shared.sysconfig.redis_client", _NoCacheRedis())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        # sqlite 下 BigInteger 主键不自增(MySQL 才有 AUTO_INCREMENT), 统一显式分配
        @event.listens_for(self.db, "before_flush")
        def assign_ids(sess, *_):
            next_id = getattr(assign_ids, "next_id", 700)
            for item in sess.new:
                if isinstance(item, AIProvider) and item.id is None:
                    next_id += 1
                    item.id = next_id
            assign_ids.next_id = next_id

    def make(self, name="我的供应商", kind="openai", base_url="https://api.example.com/v1",
             api_key="sk-secret-1234", enabled=True):
        return svc.create_provider(self.db, name=name, kind=kind, base_url=base_url,
                                   api_key=api_key, enabled=enabled)


class CryptoTest(unittest.TestCase):
    """Key 落库必须加密; 解不开时返回空串而不是抛异常把接口打成 500."""

    def test_roundtrip(self):
        cipher = encrypt_api_key("sk-abcdefgh1234")
        self.assertNotIn("sk-abcdefgh1234", cipher, "密文里不得出现明文")
        self.assertEqual(decrypt_api_key(cipher), "sk-abcdefgh1234")

    def test_empty_key_is_stored_as_null(self):
        self.assertIsNone(encrypt_api_key(""))
        self.assertIsNone(encrypt_api_key(None))

    def test_broken_ciphertext_degrades_to_empty(self):
        self.assertEqual(decrypt_api_key("not-a-fernet-token"), "")

    def test_key_hint_only_exposes_tail(self):
        self.assertEqual(key_hint("sk-abcdefgh1234"), "***1234")
        self.assertEqual(key_hint("short"), "***", "不足 12 位连尾号都不露")
        self.assertEqual(key_hint(""), "")


class ProviderCrudTest(ProviderTestBase):
    def test_create_returns_masked_view_only(self):
        view = self.make()
        self.assertTrue(view["has_key"])
        self.assertEqual(view["key_hint"], "***1234")
        self.assertNotIn("api_key", view, "视图里不得有明文字段")
        self.assertNotIn("api_key_enc", view)

    def test_base_url_normalized(self):
        view = self.make(base_url="https://api.deepseek.com/")
        self.assertEqual(view["base_url"], "https://api.deepseek.com")
        self.assertEqual(view["openai_base_url"], "https://api.deepseek.com/v1")
        ollama = self.make(name="本地", kind="ollama", base_url="http://127.0.0.1:11434/v1/")
        self.assertEqual(ollama["base_url"], "http://127.0.0.1:11434", "ollama 地址不带 /v1")

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(BusinessException):
            self.make(name="  ")
        with self.assertRaises(BusinessException):
            self.make(kind="not-a-kind")
        with self.assertRaises(BusinessException):
            self.make(base_url="ftp://x")
        with self.assertRaises(BusinessException):
            self.make(base_url="")

    def test_duplicate_name_conflicts(self):
        self.make()
        with self.assertRaises(BusinessException):
            self.make()

    def test_update_key_is_three_state(self):
        view = self.make()
        pid = view["id"]
        # 省略 => 不动
        svc.update_provider(self.db, pid, name="改名")
        self.assertEqual(svc.view_of(svc.get_provider(self.db, pid))["key_hint"], "***1234")
        # 覆盖
        svc.update_provider(self.db, pid, api_key="sk-new-key-9999")
        self.assertEqual(svc.view_of(svc.get_provider(self.db, pid))["key_hint"], "***9999")
        # clear_key => 清空
        svc.update_provider(self.db, pid, clear_key=True)
        self.assertFalse(svc.view_of(svc.get_provider(self.db, pid))["has_key"])

    def test_update_unknown_provider(self):
        with self.assertRaises(ResourceNotFoundException):
            svc.update_provider(self.db, 999, name="x")

    def test_delete_unbound_and_missing(self):
        pid = self.make()["id"]
        svc.delete_provider(self.db, pid)
        self.assertEqual(svc.list_providers(self.db), [])
        with self.assertRaises(ResourceNotFoundException):
            svc.delete_provider(self.db, pid)

    def test_delete_bound_provider_is_refused(self):
        pid = self.make()["id"]
        svc.save_bindings(self.db, {"chat": {"provider_id": pid, "model": "gpt-4o-mini"}})
        with self.assertRaises(BusinessException) as ctx:
            svc.delete_provider(self.db, pid)
        self.assertIn("会话模型", ctx.exception.message, "报错要说清被哪个能力占着")


class PresetTest(unittest.TestCase):
    def test_presets_cover_common_vendors(self):
        kinds = {item["kind"] for item in PRESETS}
        self.assertTrue({"deepseek", "zhipu", "dashscope", "moonshot", "openai", "ollama", "custom"} <= kinds)

    def test_every_preset_is_renderable(self):
        for item in svc.presets():
            self.assertTrue(item["label"])
            self.assertIn("needs_key", item)
            self.assertIsInstance(item["models"], list)

    def test_ollama_preset_needs_no_key(self):
        ollama = next(item for item in PRESETS if item["kind"] == "ollama")
        self.assertFalse(ollama["needs_key"])
        self.assertTrue(ollama["base_url"].startswith("http://127.0.0.1"))


class BindingTest(ProviderTestBase):
    def test_empty_bindings_are_not_configured(self):
        bound = svc.bindings(self.db)
        self.assertTrue(bound["enabled"], "默认开启")
        for slot in ("chat", "embedding", "rerank"):
            self.assertFalse(bound["slots"][slot]["configured"])
            self.assertIsNone(bound["slots"][slot]["provider_id"])

    def test_bind_and_read_back(self):
        pid = self.make()["id"]
        bound = svc.save_bindings(self.db, {
            "chat": {"provider_id": pid, "model": "gpt-4o-mini"},
            "embedding": {"provider_id": pid, "model": "text-embedding-3-small"},
        })
        self.assertTrue(bound["slots"]["chat"]["configured"])
        self.assertEqual(bound["slots"]["chat"]["provider_name"], "我的供应商")
        self.assertEqual(bound["slots"]["chat"]["model"], "gpt-4o-mini")
        self.assertEqual(bound["slots"]["embedding"]["model"], "text-embedding-3-small")

    def test_binding_requires_provider_and_model(self):
        pid = self.make()["id"]
        with self.assertRaises(ResourceNotFoundException):
            svc.save_bindings(self.db, {"chat": {"provider_id": 999, "model": "m"}})
        with self.assertRaises(BusinessException) as ctx:
            svc.save_bindings(self.db, {"chat": {"provider_id": pid, "model": "  "}})
        self.assertIn("模型名", ctx.exception.message)

    def test_disabled_provider_cannot_be_bound(self):
        pid = self.make(enabled=False)["id"]
        with self.assertRaises(BusinessException) as ctx:
            svc.save_bindings(self.db, {"chat": {"provider_id": pid, "model": "m"}})
        self.assertIn("停用", ctx.exception.message)

    def test_global_switch_roundtrip(self):
        bound = svc.save_bindings(self.db, {"enabled": False})
        self.assertFalse(bound["enabled"])
        self.assertFalse(svc.bindings(self.db)["enabled"])

    def test_unbind_via_null_id(self):
        pid = self.make()["id"]
        svc.save_bindings(self.db, {"chat": {"provider_id": pid, "model": "m"}})
        svc.save_bindings(self.db, {"chat": {"provider_id": None}})
        self.assertIsNone(svc.bindings(self.db)["slots"]["chat"]["provider_id"])


class MigrateLegacyTest(ProviderTestBase):
    """P10 的 16 个旧键必须在启动时折算成供应商行, 折算完即删除并保持幂等."""

    def _legacy(self):
        self.db.add_all([
            SystemConfig(key="ai_llm_provider", value="openai"),
            SystemConfig(key="ai_llm_base_url", value="https://api.example.com/v1"),
            SystemConfig(key="ai_llm_api_key", value="sk-legacy-key-8888"),
            SystemConfig(key="ai_llm_model", value="glm-4-plus"),
            SystemConfig(key="ai_embedding_provider", value="openai"),
            SystemConfig(key="ai_embedding_base_url", value="https://api.example.com/v1"),
            SystemConfig(key="ai_embedding_api_key", value="sk-legacy-key-8888"),
            SystemConfig(key="ai_embedding_model", value="embedding-3"),
            SystemConfig(key="ai_embedding_dim", value="1024"),
        ])
        self.db.commit()

    def test_migrates_and_removes_legacy_keys(self):
        self._legacy()
        self.assertTrue(svc.migrate_legacy_config(self.db))

        rows = svc.list_providers(self.db)
        self.assertEqual(len(rows), 1, "同一 Base URL + 同一 Key 的向量能力应复用会话供应商")
        self.assertTrue(rows[0]["has_key"])
        bound = svc.bindings(self.db)
        self.assertEqual(bound["slots"]["chat"]["model"], "glm-4-plus")
        self.assertEqual(bound["slots"]["embedding"]["model"], "embedding-3")
        self.assertEqual(bound["embedding_dim"], 1024)
        left = self.db.query(SystemConfig).filter(SystemConfig.key.in_(LEGACY_KEYS)).count()
        self.assertEqual(left, 0, "旧键折算后必须删除, 否则下次启动会再折一遍")

    def test_second_run_is_noop(self):
        self._legacy()
        self.assertTrue(svc.migrate_legacy_config(self.db))
        self.assertFalse(svc.migrate_legacy_config(self.db), "幂等: 没有旧值就什么都不做")
        self.assertEqual(len(svc.list_providers(self.db)), 1)

    def test_kinds_are_closed_set(self):
        self.assertEqual(set(KINDS), {"openai", "deepseek", "zhipu", "dashscope",
                                      "moonshot", "ollama", "custom"})
