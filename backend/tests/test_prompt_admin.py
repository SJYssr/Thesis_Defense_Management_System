"""Prompt 模板管理与解析单测(roadmap 七十三章)."""
import json
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ai.llm.factory import LLMFactory, MockLLM
from app.ai.prompts import (
    PROMPT_SPECS,
    UNTRUSTED_NOTE,
    invalidate_prompt_cache,
    resolve_system_prompt,
)
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIPromptTemplate, AIPromptTemplateVersion
from app.modules.aiprompt.router import (
    list_prompts,
    prompt_versions,
    reset_prompt,
    toggle_status,
    update_prompt,
)


class _FakeUser:
    id = 1
    username = "admin"


CUSTOM = "你是自定义答辩评价教师, 评分维度不变。只输出 JSON: {\"accuracy\": int, \"feedback\": \"...\"}"


class PromptAdminTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in PROMPT_SPECS:
            invalidate_prompt_cache(code)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_builtin_default_and_safety_note(self):
        rag = resolve_system_prompt(self.db, "RAG_ANSWER")
        self.assertIn(PROMPT_SPECS["RAG_ANSWER"].default_system, rag)
        self.assertIn(UNTRUSTED_NOTE, rag, "untrusted 场景必须强制追加安全声明")
        report = resolve_system_prompt(self.db, "DEFENSE_REPORT")
        self.assertEqual(report, PROMPT_SPECS["DEFENSE_REPORT"].default_system)
        self.assertNotIn(UNTRUSTED_NOTE, report)

    def test_update_override_and_version_history(self):
        resp = update_prompt("DEFENSE_EVALUATE_ANSWER",
                             type("B", (), {"system_prompt": CUSTOM, "name": None})(),
                             _FakeUser(), self.db)
        version = json.loads(resp.body)["data"]["version"]
        self.assertEqual(version, 1)
        resolved = resolve_system_prompt(self.db, "DEFENSE_EVALUATE_ANSWER")
        self.assertIn(CUSTOM, resolved)
        self.assertIn(UNTRUSTED_NOTE, resolved, "自定义内容同样被追加安全声明")

        update_prompt("DEFENSE_EVALUATE_ANSWER",
                      type("B", (), {"system_prompt": CUSTOM + "补充规则。", "name": None})(),
                      _FakeUser(), self.db)
        row = self.db.execute(select(AIPromptTemplate)
                              .where(AIPromptTemplate.code == "DEFENSE_EVALUATE_ANSWER")).scalar_one()
        self.assertEqual(row.version, 2)
        history = self.db.execute(select(AIPromptTemplateVersion)
                                  .where(AIPromptTemplateVersion.code == "DEFENSE_EVALUATE_ANSWER")).scalars().all()
        self.assertEqual(len(history), 2, "每次修改留痕")

    def test_disable_falls_back_to_builtin(self):
        update_prompt("RAG_ANSWER", type("B", (), {"system_prompt": CUSTOM, "name": None})(),
                      _FakeUser(), self.db)
        toggle_status("RAG_ANSWER", type("B", (), {"enabled": False})(), _FakeUser(), self.db)
        resolved = resolve_system_prompt(self.db, "RAG_ANSWER")
        self.assertIn(PROMPT_SPECS["RAG_ANSWER"].default_system, resolved, "停用后回退内置默认")
        self.assertNotIn(CUSTOM, resolved)
        toggle_status("RAG_ANSWER", type("B", (), {"enabled": True})(), _FakeUser(), self.db)
        self.assertIn(CUSTOM, resolve_system_prompt(self.db, "RAG_ANSWER"), "重新启用恢复自定义")

    def test_reset_restores_default_with_new_version(self):
        update_prompt("PPT_OUTLINE", type("B", (), {"system_prompt": CUSTOM, "name": None})(),
                      _FakeUser(), self.db)
        reset_prompt("PPT_OUTLINE", _FakeUser(), self.db)
        row = self.db.execute(select(AIPromptTemplate)
                              .where(AIPromptTemplate.code == "PPT_OUTLINE")).scalar_one()
        self.assertEqual(row.version, 2, "重置也产生新版本")
        self.assertIn(PROMPT_SPECS["PPT_OUTLINE"].default_system,
                      resolve_system_prompt(self.db, "PPT_OUTLINE"))

    def test_unknown_code_and_short_content_rejected(self):
        with self.assertRaises(ResourceNotFoundException):
            update_prompt("NOT_A_SCENE", type("B", (), {"system_prompt": CUSTOM, "name": None})(),
                          _FakeUser(), self.db)
        with self.assertRaises(ValueError):
            resolve_system_prompt(self.db, "NOT_A_SCENE")
        with self.assertRaises(BusinessException):
            update_prompt("RAG_ANSWER", type("B", (), {"system_prompt": "太短", "name": None})(),
                          _FakeUser(), self.db)
        with self.assertRaises(ResourceNotFoundException):
            reset_prompt("RAG_ANSWER", _FakeUser(), self.db, )

    def test_list_merges_registry_and_overrides(self):
        update_prompt("QUERY_REWRITE", type("B", (), {"system_prompt": CUSTOM, "name": None})(),
                      _FakeUser(), self.db)
        data = json.loads(list_prompts(_FakeUser(), self.db).body)["data"]
        self.assertEqual(len(data), len(PROMPT_SPECS), "场景以注册表为准")
        by_code = {item["code"]: item for item in data}
        self.assertEqual(by_code["QUERY_REWRITE"]["source"], "DB")
        self.assertEqual(by_code["QUERY_REWRITE"]["version"], 1)
        self.assertEqual(by_code["RAG_ANSWER"]["source"], "BUILT_IN")
        self.assertEqual(by_code["RAG_ANSWER"]["version"], 0)
        versions = json.loads(prompt_versions("QUERY_REWRITE", _FakeUser(), self.db).body)["data"]
        self.assertEqual(len(versions), 1)

    def test_llm_factory_falls_back_without_redis_config(self):
        """无 Redis/无覆盖配置时, 模型名回退环境变量默认; Mock 模式不受覆盖影响."""
        LLMFactory._instance = None
        LLMFactory._instance_model = None
        self.assertEqual(LLMFactory._model_override(), "")
        service = LLMFactory.get()
        self.assertIsInstance(service, MockLLM)  # 测试环境无 API Key, 走离线 Mock
