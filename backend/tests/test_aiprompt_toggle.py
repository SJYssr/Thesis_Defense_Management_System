"""D69: 编辑/重置 Prompt 内容不得静默重新启用已停用场景."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ai.prompts import PROMPT_SPECS, invalidate_prompt_cache, resolve_system_prompt
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIPromptTemplate
from app.models.system import SystemConfig
from app.modules.aiprompt.router import (
    PromptStatusRequest,
    PromptUpdateRequest,
    list_prompts,
    reset_prompt,
    toggle_status,
    update_prompt,
)

ADMIN = CurrentUser(id=1, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
CODE = "DEFENSE_REPORT"
DEFAULT = PROMPT_SPECS[CODE].default_system
CUSTOM = "你是自定义答辩报告老师, 五维评分口径不变。只输出 JSON: {\"total\": int, \"advice\": \"...\"}"


class _NoCacheRedis:
    """把 Redis 缓存摘掉, 断言只看数据库真实状态."""

    def get(self, *args, **kwargs):
        return None

    def setex(self, *args, **kwargs):
        return True

    def delete(self, *args, **kwargs):
        return 0


class PromptTogglePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in PROMPT_SPECS:
            invalidate_prompt_cache(code)
        patcher = patch("app.ai.prompts.redis_client", _NoCacheRedis())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _row(self) -> AIPromptTemplate:
        return self.db.execute(select(AIPromptTemplate).where(AIPromptTemplate.code == CODE)).scalar_one()

    def _edit(self, content: str):
        return update_prompt(CODE, PromptUpdateRequest(system_prompt=content), ADMIN, self.db)

    def _set_enabled(self, enabled: bool):
        return toggle_status(CODE, PromptStatusRequest(enabled=enabled), ADMIN, self.db)

    def test_first_customization_is_enabled(self):
        # 基线语义: 首次自定义默认启用(status=1), 之后启停只能显式操作
        self._edit(CUSTOM)
        self.assertEqual(self._row().status, 1)
        self.assertIn(CUSTOM, resolve_system_prompt(self.db, CODE))

    def test_edit_after_disable_keeps_disabled(self):
        self._edit(CUSTOM)
        self._set_enabled(False)
        self.assertEqual(self._row().status, 0)
        self.assertEqual(resolve_system_prompt(self.db, CODE), DEFAULT)

        self._edit(CUSTOM + " 另外必须指出改进项。")
        row = self._row()
        self.assertEqual(row.status, 0, "D69: 编辑内容不得把停用场景重新启用")
        self.assertIn("另外必须指出改进项", row.system_prompt, "内容本身仍应更新并留痕")
        self.assertEqual(resolve_system_prompt(self.db, CODE), DEFAULT, "停用场景对外仍是内置默认")
        self.assertEqual(row.version, 2, "停用状态下的编辑同样产生新版本")

    def test_reset_after_disable_keeps_disabled(self):
        self._edit(CUSTOM)
        self._set_enabled(False)
        reset_prompt(CODE, ADMIN, self.db)
        row = self._row()
        self.assertEqual(row.status, 0, "D69: 重置不得重新启用")
        self.assertEqual(row.system_prompt, DEFAULT)
        self.assertEqual(row.version, 2)

    def test_explicit_enable_restores_custom_content(self):
        self._edit(CUSTOM)
        self._set_enabled(False)
        self._edit(CUSTOM + " 补充一句。")
        self._set_enabled(True)
        row = self._row()
        self.assertEqual(row.status, 1)
        self.assertIn("补充一句", resolve_system_prompt(self.db, CODE))

    def test_list_reports_disabled_scene_as_builtin(self):
        self._edit(CUSTOM)
        self._set_enabled(False)
        items = {item["code"]: item for item in _json(list_prompts(ADMIN, self.db))}
        item = items[CODE]
        self.assertTrue(item["customized"])
        self.assertFalse(item["enabled"])
        self.assertEqual(item["source"], "BUILT_IN")
        self.assertEqual(item["system_prompt"], DEFAULT)


class LLMModelOverrideTest(unittest.TestCase):
    """D68: Redis 缓存未命中时, 模型覆盖必须直读 system_config(不再静默回退环境默认)."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)  # LIFO: 先关 session 再释放引擎
        self.addCleanup(self.db.close)
        self.db.add(SystemConfig(key="ai_llm_model", value="glm-4-plus"))
        self.db.commit()

    def _patch(self, target, value):
        patcher = patch(target, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cache_miss_reads_database_value(self):
        from app.ai.llm.factory import LLMFactory

        self._patch("app.db.redis.redis_client", _NoCacheRedis())
        self.assertEqual(LLMFactory._model_override(self.db), "glm-4-plus",
                         "缓存未命中不得返回空串(后台任务路径会静默用错模型)")

    def test_cache_miss_without_injected_session_opens_its_own(self):
        from app.ai.llm.factory import LLMFactory

        self._patch("app.db.redis.redis_client", _NoCacheRedis())
        self._patch("app.db.mysql.SessionLocal", lambda: Session(self.engine))
        self.assertEqual(LLMFactory._model_override(), "glm-4-plus")
        # 工厂自开的 session 已关闭, 本测试的 session 仍可用
        self.assertEqual(self.db.execute(select(SystemConfig.value)).scalar_one(), "glm-4-plus")

    def test_cache_hit_short_circuits_database(self):
        from app.ai.llm.factory import LLMFactory

        class _HitRedis:
            def get(self, *args, **kwargs):
                return b'{"ai_llm_model": "cached-model"}'

        self._patch("app.db.redis.redis_client", _HitRedis())
        self.assertEqual(LLMFactory._model_override(self.db), "cached-model")

    def test_no_config_configured_falls_back_to_empty(self):
        from app.ai.llm.factory import LLMFactory

        self._patch("app.db.redis.redis_client", _NoCacheRedis())
        self.db.query(SystemConfig).filter(SystemConfig.key == "ai_llm_model").delete()
        self.db.commit()
        self.assertEqual(LLMFactory._model_override(self.db), "")


class SysConfigCacheWritebackTest(unittest.TestCase):
    """D68 另一半: update_configs 删缓存后必须立刻写回新值, 不能留下空缓存."""

    class _MemoryRedis:
        def __init__(self):
            self.store: dict[str, str] = {}
            self.setex_calls: list[tuple[str, int, str]] = []

        def get(self, key, *args, **kwargs):
            return self.store.get(key)

        def setex(self, key, ttl, value, *args, **kwargs):
            self.setex_calls.append((key, ttl, value))
            self.store[key] = value
            return True

        def delete(self, *args, **kwargs):
            for key in args:
                self.store.pop(key, None)
            return len(args)

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.redis = self._MemoryRedis()
        patcher = patch("app.shared.sysconfig.redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_update_writes_new_values_back_to_cache(self):
        from app.shared.sysconfig import CACHE_KEY, get_all, update_configs

        update_configs(self.db, {"ai_llm_model": "glm-4-plus", "max_upload_daily_mb": "50"}, 1)

        self.assertTrue(self.redis.setex_calls, "更新后必须立刻写回缓存")
        key, ttl, payload = self.redis.setex_calls[-1]
        self.assertEqual(key, CACHE_KEY)
        self.assertGreater(ttl, 0)
        self.assertEqual(json.loads(payload)["ai_llm_model"], "glm-4-plus", "缓存里必须是新值而非旧值")
        self.assertEqual(json.loads(payload)["max_upload_daily_mb"], "50")
        # 命中缓存时读到的就是新值, 不会退货空缓存导致的一次性回退默认
        self.assertEqual(get_all(self.db)["ai_llm_model"], "glm-4-plus")

    def test_upload_quota_field_registered_in_schema(self):
        from app.shared.sysconfig import FIELDS, get_int

        self.assertIn("max_upload_daily_mb", FIELDS)
        self.assertEqual(get_int(self.db, "max_upload_daily_mb", 200), 200, "默认 200MB")


def _json(resp):
    import json
    return json.loads(resp.body)["data"]


if __name__ == "__main__":
    unittest.main()
