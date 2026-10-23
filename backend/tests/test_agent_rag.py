"""Agent 多步 RAG 问答单测(P10-C).

覆盖 7 个验收面:
1. 正常路径事件序: meta -> step(plan/tool/observe/answer) -> delta -> citations -> done;
2. 步数上限强制收束(kind="forced"), kind=="tool" 步数 == settings.agent_max_steps;
3. 检索轮次上限: 超限的检索不真正执行, 只回 error observation;
4. 工具异常: observation 带 error, 循环继续, 最终仍有 done;
5. 归属隔离: 身份参数被拒绝, get_my_* 只返回本人数据;
6. 引用核验: 越界 -> 重生成一次; 二次仍越界 -> citations.invalid 非空且标记被丢弃;
7. 开关降级: agent_enabled=False 走 answer_stream / agent=True 覆盖配置;
8. 超时: 极小时间预算 -> 仍产 done 且 status=DEGRADED(forced)。

全部用例不依赖真实模型: 用 ScriptedLLM 按调用次序返回预设 JSON/文本,
通过 mock.patch.object(LLMFactory, "get") 注入; 检索层 patch HybridRetriever.retrieve。
"""
import json
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.agent import tools as agent_tools
from app.ai.agent.loop import AgentRunner
from app.ai.agent.tools import (
    TOOL_REGISTRY,
    AgentContext,
    AgentTool,
    AgentToolbox,
    AgentToolError,
    tools_prompt,
)
from app.ai.agent.trace import AgentTrace, agent_trace
from app.ai.llm.factory import LLMFactory
from app.ai.prompts import (
    AGENT_TOOLS_PLACEHOLDER,
    PROMPT_SPECS,
    UNTRUSTED_NOTE,
    resolve_system_prompt,
)
from app.ai.rag.rag_service import RAGService
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
from app.core.config import settings
from app.core.permissions import CurrentUser, get_current_user, require_student
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.models.business import (
    GraduationCycle,
    GraduationTopic,
    OpeningReport,
    TopicSelection,
)
from app.models.ai import AIUsageLog
from app.models.defense import ReviewRubric
from app.modules.ai import router as ai_router_module

STUDENT_A, STUDENT_B = 11, 22
CYCLE_A, CYCLE_B = 901, 902
TOPIC_A, TOPIC_B = 501, 502

_ID_SEQ: dict[type, int] = {}


def _enable_sqlite_autoincrement(model) -> None:
    """SQLite 下 BigInteger 主键不自增(MySQL 才有 AUTO_INCREMENT), 这里补上 MySQL 语义.

    AIUsageLog 由 _write_usage_log 内部创建, 单元测试不关心用量明细, 但不能让它插入失败
    把 session 拖进 PendingRollback 状态。
    """

    @event.listens_for(model, "before_insert")
    def _assign_pk(mapper, connection, target):  # noqa: ANN001
        if getattr(target, "id", None) is None:
            current = connection.execute(select(func.coalesce(func.max(model.id), 0))).scalar()
            nxt = max(int(current or 0), _ID_SEQ.get(model, 0)) + 1
            _ID_SEQ[model] = nxt
            target.id = nxt


_enable_sqlite_autoincrement(AIUsageLog)


# ---------- 桩 ----------

def _tool_decision(name: str, **args) -> str:
    return json.dumps(
        {"thought": f"需要调用 {name} 收集材料", "action": "tool", "tool": name, "args": args},
        ensure_ascii=False,
    )


def _answer_decision(hint: str = "依据材料作答") -> str:
    return json.dumps(
        {"thought": "材料已足够, 直接作答", "action": "answer", "answer_hint": hint},
        ensure_ascii=False,
    )


def _stop_decision() -> str:
    return json.dumps({"thought": "无法进一步获取材料", "action": "stop"}, ensure_ascii=False)


class ScriptedLLM:
    """按调用次序返回预设内容的假模型: 规划/自检走 complete, 作答走 stream。"""

    name = "scripted"

    def __init__(self, decisions=(), answers=("依据材料 [1] 作答。",), sufficiency=None):
        self.decisions = list(decisions)
        self.answers = list(answers)
        self.sufficiency = dict(sufficiency or {"enough": True, "missing": "", "next_query": ""})
        self.plan_calls = 0
        self.complete_calls = 0
        self.stream_calls = 0
        self.systems: list[tuple[str, str, str]] = []

    @staticmethod
    def _kind(system: str) -> str:
        if "规划器" in system:
            return "plan"
        if "充分性自检器" in system:
            return "sufficiency"
        return "answer"

    def complete(self, system: str, user: str, temperature: float = 0.3) -> str:
        self.complete_calls += 1
        kind = self._kind(system)
        self.systems.append((kind, system, user))
        if kind == "plan":
            self.plan_calls += 1
            if self.decisions:
                return self.decisions.pop(0)
            return _stop_decision()
        if kind == "sufficiency":
            return json.dumps(self.sufficiency, ensure_ascii=False)
        return self.answers[-1]

    def stream(self, system: str, user: str, history=None, temperature: float = 0.5):
        self.stream_calls += 1
        self.systems.append(("stream", system, user))
        if len(self.answers) > 1:
            text = self.answers.pop(0)
        else:
            text = self.answers[0]
        for i in range(0, len(text), 5):  # 分块, 验证逐块 delta
            yield text[i:i + 5]


class _NoRedis:
    """prompt 管理端覆盖走 redis, 单测不打真实 redis。"""

    def __getattr__(self, name):  # noqa: ANN001
        raise RuntimeError("no redis in unit test")


def _chunk(cid: int, document_type: str, section: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=cid, document_id=cid, document_type=document_type,
                          section_title=section, content=content, score=0.9 - cid / 100.0)


def _student_a() -> CurrentUser:
    return CurrentUser(id=1, username="stu_a", real_name="学生A", role="STUDENT",
                       profile_id=STUDENT_A, avatar=None, must_change_password=False)


# ---------- 基类 ----------

class _AgentTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.SessionFactory = sessionmaker(bind=self.engine)
        self.db: Session = Session(self.engine)
        self.chunks: list[RetrievedChunk] = []
        self.retrieve_calls: list[tuple] = []
        self._seed()
        agent_trace.clear()
        self.addCleanup(self._cleanup)
        self.retriever_patch = mock.patch.object(HybridRetriever, "retrieve",
                                                side_effect=self._fake_retrieve)
        self.retriever_patch.start()
        self.addCleanup(self.retriever_patch.stop)
        redis_patch = mock.patch("app.ai.prompts.redis_client", _NoRedis())
        redis_patch.start()
        self.addCleanup(redis_patch.stop)

    def _cleanup(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _fake_retrieve(self, *args, **kwargs):
        self.retrieve_calls.append((args, kwargs))
        return list(self.chunks)

    def _seed(self):
        self.db.add_all([
            GraduationCycle(id=CYCLE_A, academic_year="2025-2026", student_id=STUDENT_A, status="ACTIVE"),
            GraduationCycle(id=CYCLE_B, academic_year="2025-2026", student_id=STUDENT_B, status="ACTIVE"),
            GraduationTopic(id=TOPIC_A, teacher_id=900, title="学生A的题目", max_students=1,
                            status="PUBLISHED", topic_type="工程实践", difficulty="MEDIUM"),
            GraduationTopic(id=TOPIC_B, teacher_id=901, title="学生B的题目", max_students=1,
                            status="PUBLISHED"),
        ])
        self.db.flush()
        self.db.add_all([
            TopicSelection(id=7001, student_id=STUDENT_A, cycle_id=CYCLE_A, topic_id=TOPIC_A,
                           teacher_id=900, status="APPROVED", student_reason="本人兴趣",
                           active_flag=1),
            TopicSelection(id=7002, student_id=STUDENT_B, cycle_id=CYCLE_B, topic_id=TOPIC_B,
                           teacher_id=901, status="PENDING", active_flag=1),
            OpeningReport(id=6001, student_id=STUDENT_A, cycle_id=CYCLE_A, topic_id=TOPIC_A,
                          teacher_id=900, title="学生A的开题报告", status="SUBMITTED"),
            ReviewRubric(id=8001, academic_year="2025-2026", stage="OPENING", version=1,
                         weights_json='[{"name":"报告质量","weight":40},{"name":"答辩表现","weight":60}]',
                         pass_line=60, enabled=1),
        ])
        self.db.commit()

    # ---------- 运行助手 ----------

    def _run_agent(self, llm, query="系统怎么分层", student_id=STUDENT_A, **kwargs):
        with mock.patch.object(LLMFactory, "get", return_value=llm):
            events = list(RAGService(self.db).answer_agent_stream(
                query, student_id, user_id=1, **kwargs))
        return events

    @staticmethod
    def _steps(events):
        return [e["data"] for e in events if e["event"] == "step"]

    @staticmethod
    def _by_event(events, name):
        return [e["data"] for e in events if e["event"] == name]


# ---------- 1 & 2 & 3 & 4 & 5 & 6 & 8: 事件序与硬约束 ----------

class AgentFlowTest(_AgentTestBase):
    def test_normal_path_event_sequence(self):
        self.chunks = [
            _chunk(1, "THESIS", "第三章", "系统分为接入层/服务层/数据层三层。"),
            _chunk(2, "OPENING_REPORT", "研究内容", "本课题研究答辩管理系统的可信性。"),
        ]
        llm = ScriptedLLM(decisions=[_tool_decision("search_knowledge", query="系统怎么分层"),
                                    _answer_decision()],
                          answers=("根据材料 [1], 系统采用三层架构; 材料 [2] 说明了研究范围。",))
        events = self._run_agent(llm)

        names = [e["event"] for e in events]
        self.assertEqual(names[0], "meta")
        self.assertEqual(names[-2:], ["citations", "done"])
        self.assertEqual(names.count("meta"), 1)
        self.assertIn("step", names)

        # 事件序: plan -> tool -> observe -> answer, 且 delta 全部落在 step 之后
        kinds = [s["kind"] for s in self._steps(events)]
        self.assertEqual(kinds, ["plan", "tool", "observe", "answer"])
        first_delta = names.index("delta")
        last_step = max(i for i, n in enumerate(names) if n == "step")
        self.assertLess(last_step, first_delta, "delta 必须在全部 step 之后")

        tool_step = self._steps(events)[1]
        self.assertEqual(tool_step["tool"], "search_knowledge")
        self.assertEqual(tool_step["args"], {"query": "系统怎么分层"})
        observe = self._steps(events)[2]
        self.assertTrue(observe["observation"].lstrip().startswith("["), "检索观察应是 chunk 列表")
        self.assertIn("三层", observe["observation"])

        # meta: agent 标记 + 工具清单 + 初始 sources 为空
        meta = events[0]["data"]
        self.assertTrue(meta["agent"])
        self.assertEqual(meta["sources"], [])
        self.assertEqual(sorted(meta["tools"]), sorted(TOOL_REGISTRY.keys()))

        text = "".join(self._by_event(events, "delta"))
        self.assertEqual(text, "根据材料 [1], 系统采用三层架构; 材料 [2] 说明了研究范围。")

        cit = self._by_event(events, "citations")[0]
        self.assertTrue(cit["checked"])
        self.assertFalse(cit["regenerated"])
        self.assertEqual(cit["invalid"], [])
        self.assertEqual([s["index"] for s in cit["sources"]], [1, 2])

        done = events[-1]["data"]
        self.assertEqual(done["status"], "SUCCESS")
        self.assertFalse(done["degraded"])
        self.assertEqual(done["steps"], 2)
        self.assertEqual(done["sources"], 2)
        self.assertEqual(done["length"], len(text))

        # 规划 system 必须已把 {{TOOLS}} 渲染成真实工具清单
        plan_system = next(s for kind, s, _ in llm.systems if kind == "plan")
        self.assertNotIn(AGENT_TOOLS_PLACEHOLDER, plan_system)
        self.assertIn("search_knowledge", plan_system)
        self.assertIn("不得发明工具", plan_system)

        # 轨迹: 全局环形缓冲应记到本次执行
        recent = agent_trace.recent(5)
        self.assertTrue(recent)
        self.assertEqual(recent[-1]["query"], "系统怎么分层")
        self.assertEqual(recent[-1]["student_id"], STUDENT_A)

    def test_step_cap_forces_conclusion(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        llm = ScriptedLLM(
            decisions=[_tool_decision("search_knowledge", query=f"第{i}次检索") for i in range(10)],
            answers=("仍按已有材料作答。",))
        with mock.patch.object(settings, "agent_max_steps", 3), \
                mock.patch.object(settings, "agent_max_retrieval_rounds", 10):
            events = self._run_agent(llm)

        steps = self._steps(events)
        tool_steps = [s for s in steps if s["kind"] == "tool"]
        forced = [s for s in steps if s["kind"] == "forced"]
        self.assertEqual(len(tool_steps), 3, "tool 步数必须等于步数上限 agent_max_steps=3")
        self.assertEqual(llm.plan_calls, 3, "到达上限后不允许再决策")
        self.assertEqual(len(forced), 1, "强制收束只允许发生一次")
        self.assertIn("最大步数", forced[0]["forced"])
        self.assertTrue(forced[0]["thought"])

        done = events[-1]["data"]
        self.assertEqual(done["status"], "DEGRADED")
        self.assertTrue(done["degraded"])
        self.assertEqual(done["steps"], 3)
        self.assertIn("最大步数", done["forced"])
        # 强制收束后仍必须有答案与引用事件, 前端不白屏
        self.assertTrue(self._by_event(events, "delta"))
        self.assertTrue(self._by_event(events, "citations"))

    def test_retrieval_round_cap_blocks_extra_retrieval(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        llm = ScriptedLLM(
            decisions=[_tool_decision("search_knowledge", query=f"q{i}") for i in range(4)],
            answers=("按已有材料作答。",))
        with mock.patch.object(settings, "agent_max_steps", 4), \
                mock.patch.object(settings, "agent_max_retrieval_rounds", 2):
            events = self._run_agent(llm)

        observes = [s for s in self._steps(events) if s["kind"] == "observe"]
        self.assertEqual(len(observes), 4)
        self.assertEqual(len(self.retrieve_calls), 2, "超过检索轮次上限不得真正检索")
        self.assertNotIn("上限", observes[1]["observation"])
        self.assertIn("检索轮次已达上限", observes[2]["observation"])
        self.assertIn("检索轮次已达上限", observes[3]["observation"])
        self.assertEqual(events[-1]["event"], "done")

    def test_tool_exception_becomes_observation_and_loop_continues(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]

        def _boom(db, ctx, **kwargs):
            raise RuntimeError("review_rubric 表不可用")

        broken = AgentTool(name="get_rubric", description="读取评分细则(测试桩)",
                           parameters={"type": "object", "properties": {}}, func=_boom)
        llm = ScriptedLLM(
            decisions=[_tool_decision("get_rubric"),
                       _tool_decision("search_knowledge", query="兜底检索"),
                       _answer_decision()],
            answers=("工具异常也不影响作答。",))
        with mock.patch.dict(TOOL_REGISTRY, {"get_rubric": broken}):
            events = self._run_agent(llm)

        observes = [s for s in self._steps(events) if s["kind"] == "observe"]
        self.assertEqual(len(observes), 2)
        self.assertIn("error", observes[0]["observation"])
        self.assertIn("review_rubric 表不可用", observes[0]["observation"])
        self.assertNotIn("error", observes[1]["observation"], "异常后循环必须继续执行后续工具")
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["data"]["status"], "SUCCESS")

    def test_identity_args_rejected_and_tools_scoped_to_self(self):
        ctx_a = AgentContext(student_id=STUDENT_A)
        ctx_b = AgentContext(student_id=STUDENT_B)
        box = AgentToolbox()

        # 1) 越权参数: 覆盖 student_id 必须被拒绝(不是静默忽略)
        out = box.execute("search_knowledge", self.db, ctx_a,
                          {"query": "q", "student_id": STUDENT_B})
        self.assertIn("error", out)
        self.assertIn("student_id", out["error"])
        self.assertEqual(self.retrieve_calls, [], "被拒绝的调用不得触达检索层")

        with self.assertRaises(AgentToolError):
            TOOL_REGISTRY["search_knowledge"].run(self.db, ctx_a, query="q", student_id=STUDENT_B)

        # 2) 未知文档类型直接拒绝, 不是返回空列表
        bad = box.execute("search_knowledge", self.db, ctx_a,
                          {"query": "q", "document_types": ["NOT_A_DOC_TYPE"]})
        self.assertIn("error", bad)

        # 3) get_my_* 只返回本人数据
        topic_a = box.execute("get_my_topic", self.db, ctx_a, {})
        topic_b = box.execute("get_my_topic", self.db, ctx_b, {})
        self.assertEqual(topic_a["title"], "学生A的题目")
        self.assertEqual(topic_b["title"], "学生B的题目")
        self.assertEqual(topic_a["selection_status"], "APPROVED")
        self.assertEqual(topic_b["selection_status"], "PENDING")

        stages_a = {row["stage"]: row["status"] for row in
                    box.execute("get_my_stage_status", self.db, ctx_a, {})}
        stages_b = {row["stage"]: row["status"] for row in
                    box.execute("get_my_stage_status", self.db, ctx_b, {})}
        self.assertEqual(stages_a["OPENING"], "SUBMITTED")
        self.assertEqual(stages_b["OPENING"], "NOT_SUBMITTED")
        self.assertEqual(stages_a["SELECTION"], "APPROVED")

        # 4) 检索下推的 student_id 只能是 ctx 本人
        box.execute("search_knowledge", self.db, ctx_a, {"query": "本人检索"})
        args, kwargs = self.retrieve_calls[-1]
        self.assertEqual(args[1], STUDENT_A, "检索必须用 ctx.student_id 做归属过滤")

        # 5) loop 层同样拒绝: LLM 决策里带身份参数只能拿到 error observation
        llm = ScriptedLLM(
            decisions=[_tool_decision("search_knowledge", query="q", student_id=STUDENT_B),
                       _answer_decision()],
            answers=("越权参数被拒绝后照常作答。",))
        events = self._run_agent(llm)
        observes = [s for s in self._steps(events) if s["kind"] == "observe"]
        self.assertIn("student_id", observes[0]["observation"])
        self.assertEqual(len([c for c in self.retrieve_calls if c[0][1] == STUDENT_B]), 0)
        self.assertEqual(events[-1]["event"], "done")

    def test_citation_invalid_triggers_one_regeneration(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "甲的说明。"),
                       _chunk(2, "THESIS", "第二章", "乙的说明。"),
                       _chunk(3, "THESIS", "第三章", "丙的说明。")]
        llm = ScriptedLLM(decisions=[_tool_decision("search_knowledge", query="q"),
                                    _answer_decision()],
                          answers=("见材料 [9] 的结论。", "见材料 [1] 的结论。"))
        events = self._run_agent(llm)

        self.assertEqual(llm.stream_calls, 2, "越界必须正好触发一次重生成")
        cit = self._by_event(events, "citations")[0]
        self.assertTrue(cit["checked"])
        self.assertTrue(cit["regenerated"])
        self.assertEqual(cit["invalid"], [])
        text = "".join(self._by_event(events, "delta"))
        self.assertEqual(text, "见材料 [1] 的结论。", "只有核验通过的答案才会下发")
        self.assertNotIn("[9]", text)
        stream_calls = [(s, u) for kind, s, u in llm.systems if kind == "stream"]
        self.assertEqual(len(stream_calls), 2, "首次生成 + 一次重生成")
        regen_system, regen_user = stream_calls[-1]
        self.assertIn("引用核验器", regen_system)
        self.assertIn("不存在的来源: [9]", regen_user)
        self.assertEqual(events[-1]["data"]["status"], "SUCCESS")

    def test_citation_still_invalid_after_regeneration(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "甲的说明。"),
                       _chunk(2, "THESIS", "第二章", "乙的说明。"),
                       _chunk(3, "THESIS", "第三章", "丙的说明。")]
        llm = ScriptedLLM(decisions=[_tool_decision("search_knowledge", query="q"),
                                    _answer_decision()],
                          answers=("材料 [9] 说明一切。", "材料 [9] 依然错误。"))
        events = self._run_agent(llm)

        self.assertEqual(llm.stream_calls, 2)
        cit = self._by_event(events, "citations")[0]
        self.assertTrue(cit["regenerated"])
        self.assertEqual(cit["invalid"], [9], "二次仍越界的编号必须写进 citations.invalid")
        text = "".join(self._by_event(events, "delta"))
        self.assertNotIn("[9]", text, "二次越界的标记必须被丢弃")
        self.assertIn("依然错误", text, "下发的应是二次生成的正文")
        self.assertIn("材料", text, "丢弃标记不得丢弃正文")
        done = events[-1]["data"]
        self.assertEqual(done["status"], "DEGRADED")
        self.assertTrue(done["degraded"])
        self.assertEqual(done["invalid_citations"], [9])

    def test_sufficiency_self_check_triggers_extra_retrieval(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        llm = ScriptedLLM(
            decisions=[_tool_decision("search_knowledge", query="系统背景"),
                       _answer_decision()],
            answers=("按补充材料作答。",),
            sufficiency={"enough": False, "missing": "缺少答辩规范", "next_query": "答辩迟到规定"})
        with mock.patch.object(settings, "agent_max_retrieval_rounds", 3):
            events = self._run_agent(llm)

        steps = self._steps(events)
        self_check_steps = [s for s in steps if s.get("self_check")]
        self.assertEqual(len(self_check_steps), 1)
        self.assertEqual(self_check_steps[0]["kind"], "tool")
        self.assertEqual(self_check_steps[0]["args"], {"query": "答辩迟到规定"})
        self.assertEqual(len(self.retrieve_calls), 2)
        sufficiency_systems = [s for k, s, _ in llm.systems if k == "sufficiency"]
        self.assertEqual(len(sufficiency_systems), 1, "充分性自检每轮只允许一次")
        self.assertIn("充分性自检器", sufficiency_systems[0])
        self.assertEqual(events[-1]["event"], "done")

    def test_timeout_still_produces_done_and_degrades(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        llm = ScriptedLLM(decisions=[_tool_decision("get_my_progress") for _ in range(5)],
                          answers=("超时也按已有材料作答。",))
        with mock.patch.object(settings, "agent_timeout_seconds", 1e-9):
            events = self._run_agent(llm)

        steps = self._steps(events)
        forced = [s for s in steps if s["kind"] == "forced"]
        self.assertEqual(len(forced), 1)
        self.assertIn("时间预算", forced[0]["forced"])
        self.assertEqual(len([s for s in steps if s["kind"] == "tool"]), 0,
                         "时间预算耗尽后不得再执行工具")
        done = events[-1]["data"]
        self.assertEqual(done["status"], "DEGRADED")
        self.assertTrue(done["degraded"])
        self.assertTrue(self._by_event(events, "delta"))

    def test_protocol_violation_retries_once_then_forces(self):
        llm = ScriptedLLM(decisions=["这不是 JSON", "也不是 JSON"], answers=("收束作答。",))
        events = self._run_agent(llm)

        self.assertEqual(llm.plan_calls, 2, "不合协议必须重试且只重试一次")
        forced = [s for s in self._steps(events) if s["kind"] == "forced"]
        self.assertEqual(len(forced), 1)
        self.assertIn("未按协议输出", forced[0]["forced"])
        self.assertEqual(events[-1]["event"], "done")
        self.assertTrue(self._by_event(events, "delta"))

    def test_unknown_tool_is_rejected_then_loop_recovers(self):
        llm = ScriptedLLM(
            decisions=[_tool_decision("drop_database"),
                       _answer_decision()],
            answers=("改用已有材料作答。",))
        events = self._run_agent(llm)
        self.assertEqual(llm.plan_calls, 2)
        self.assertFalse([s for s in self._steps(events) if s["kind"] == "tool"],
                         "未注册工具不得产生 tool 步")
        self.assertEqual(events[-1]["event"], "done")

    def test_internal_failure_falls_back_to_single_turn(self):
        llm = ScriptedLLM(answers=("单轮兜底答案。",))
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        with mock.patch.object(AgentRunner, "run", side_effect=RuntimeError("loop 炸了")), \
                mock.patch.object(LLMFactory, "get", return_value=llm):
            events = list(RAGService(self.db).answer_agent_stream("系统怎么分层", STUDENT_A,
                                                                 user_id=1))
        names = [e["event"] for e in events]
        self.assertEqual(names[0], "meta")
        self.assertIn("error", names)
        error = self._by_event(events, "error")[0]
        self.assertIn("loop 炸了", error["detail"])
        self.assertIn("单轮兜底答案。", "".join(self._by_event(events, "delta")))
        done = events[-1]["data"]
        self.assertEqual(done["status"], "DEGRADED")
        self.assertTrue(done["degraded"])

    def test_answer_stream_unchanged(self):
        self.chunks = [_chunk(1, "THESIS", "第一章", "系统背景与范围。")]
        llm = ScriptedLLM(answers=("单轮答案。",))
        with mock.patch.object(LLMFactory, "get", return_value=llm):
            events = list(RAGService(self.db).answer_stream("问题是什么", STUDENT_A))
        self.assertEqual([e["event"] for e in events], ["meta", "delta", "done"])
        self.assertEqual(events[-1]["data"]["status"], "SUCCESS")
        self.assertNotIn("agent", events[0]["data"])


# ---------- 7: 开关与 router ----------

class ChatStreamSwitchTest(_AgentTestBase):
    def _client(self):
        app = FastAPI()
        app.include_router(ai_router_module.router)
        app.dependency_overrides[require_student] = _student_a
        app.dependency_overrides[get_current_user] = _student_a
        app.dependency_overrides[get_db] = lambda: self.db
        return TestClient(app)

    def _patches(self, agent_stream, single_stream):
        return [
            mock.patch.object(ai_router_module, "_check_ai_rate", lambda *a, **k: None),
            mock.patch("app.db.mysql.SessionLocal", lambda: self.db),
            mock.patch.object(RAGService, "answer_stream", side_effect=agent_stream),
            mock.patch.object(RAGService, "answer_agent_stream", side_effect=single_stream),
        ]

    def test_agent_disabled_uses_single_turn(self):
        single = [{"event": "meta", "data": {"provider": "mock", "sources": []}},
                  {"event": "delta", "data": "单轮答案"},
                  {"event": "done", "data": {"length": 4, "status": "SUCCESS"}}]
        single_mock = mock.Mock(side_effect=lambda *a, **k: iter(single))
        agent_mock = mock.Mock(side_effect=AssertionError("agent 路径不应被调用"))
        patches = self._patches(single_mock, agent_mock)
        with mock.patch.object(settings, "agent_enabled", False):
            for p in patches:
                p.start()
            try:
                resp = self._client().post("/ai/chat/stream", json={"query": "系统怎么分层"})
            finally:
                for p in patches:
                    p.stop()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: delta", resp.text)
        self.assertEqual(single_mock.call_count, 1)
        self.assertEqual(agent_mock.call_count, 0)

    def test_request_flag_overrides_config(self):
        agent = [{"event": "meta", "data": {"provider": "mock", "agent": True, "sources": []}},
                 {"event": "done", "data": {"length": 0, "status": "SUCCESS"}}]
        agent_mock = mock.Mock(side_effect=lambda *a, **k: iter(agent))
        single_mock = mock.Mock(side_effect=AssertionError("agent=True 时不应走单轮"))
        patches = self._patches(single_mock, agent_mock)
        with mock.patch.object(settings, "agent_enabled", False):
            for p in patches:
                p.start()
            try:
                resp = self._client().post("/ai/chat/stream",
                                           json={"query": "系统怎么分层", "agent": True})
            finally:
                for p in patches:
                    p.stop()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(agent_mock.call_count, 1)
        self.assertEqual(single_mock.call_count, 0)

    def test_trace_endpoint_scoped_to_self(self):
        agent_trace.record(query="学生A的提问", student_id=STUDENT_A, steps=2, elapsed_ms=10)
        agent_trace.record(query="学生B的提问", student_id=STUDENT_B, steps=3, elapsed_ms=20)

        client = self._client()
        resp = client.get("/ai/agent/trace")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertEqual(payload["scope"], "process-local-ring-buffer")
        titles = [row["query"] for row in payload["items"]]
        self.assertIn("学生A的提问", titles)
        self.assertNotIn("学生B的提问", titles, "学生只能看到自己的轨迹")

        admin = CurrentUser(id=9, username="admin", real_name="管理员", role="ADMIN",
                            profile_id=None, avatar=None, must_change_password=False)
        app = FastAPI()
        app.include_router(ai_router_module.router)
        app.dependency_overrides[require_student] = _student_a
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: self.db
        admin_payload = TestClient(app).get("/ai/agent/trace").json()["data"]
        admin_titles = [row["query"] for row in admin_payload["items"]]
        self.assertIn("学生A的提问", admin_titles)
        self.assertIn("学生B的提问", admin_titles, "ADMIN 可看全部")


# ---------- prompts / tools / trace 元数据 ----------

class AgentPromptAndToolTest(_AgentTestBase):
    def test_three_scenes_registered_as_untrusted(self):
        specs = {code: PROMPT_SPECS[code] for code in
                 ("AGENT_PLAN", "AGENT_SUFFICIENCY", "AGENT_CITATION_CHECK")}
        for code, spec in specs.items():
            self.assertEqual(spec.code, code)
            self.assertTrue(spec.untrusted, f"{code} 必须声明 untrusted")
            self.assertTrue(spec.name)
            self.assertTrue(spec.description)

        plan = resolve_system_prompt(self.db, "AGENT_PLAN")
        self.assertIn(AGENT_TOOLS_PLACEHOLDER, plan, "规划场景必须保留工具占位符")
        self.assertEqual(plan.count(UNTRUSTED_NOTE.strip()), 1)

        for code in ("AGENT_SUFFICIENCY", "AGENT_CITATION_CHECK"):
            system = resolve_system_prompt(self.db, code)
            self.assertEqual(system.count(UNTRUSTED_NOTE.strip()), 1,
                             f"{code} 的隔离声明必须唯一")
            self.assertNotIn(AGENT_TOOLS_PLACEHOLDER, system)

        # 既有场景不能被改动
        self.assertTrue(PROMPT_SPECS["RAG_ANSWER"].untrusted)
        self.assertFalse(PROMPT_SPECS["DEFENSE_REPORT"].untrusted)
        self.assertFalse(PROMPT_SPECS["QUERY_REWRITE"].untrusted)

    def test_tools_prompt_lists_every_registered_tool(self):
        prompt = tools_prompt(AgentToolbox())
        self.assertIn("只能从下列工具中选择", prompt)
        self.assertIn("不得发明工具", prompt)
        for name in TOOL_REGISTRY:
            self.assertIn(name, prompt)
        self.assertNotIn("shell", prompt)
        self.assertNotIn("delete", prompt)

    def test_get_rubric_returns_weights_snapshot(self):
        out = AgentToolbox().execute("get_rubric", self.db, AgentContext(student_id=STUDENT_A), {})
        self.assertEqual([item["name"] for item in out["items"]], ["报告质量", "答辩表现"])
        self.assertEqual(out["pass_line"], 60)
        self.assertEqual(out["min_examiners"], 3)

    def test_trace_ring_is_bounded_and_ordered(self):
        trace = AgentTrace(keep=3)
        for i in range(5):
            trace.record(query=f"q{i}", student_id=i, steps=1, elapsed_ms=i,
                         tool_calls=["get_my_progress"])
        snapshot = trace.snapshot()
        self.assertEqual(len(snapshot), 3)
        self.assertEqual([row["query"] for row in snapshot], ["q2", "q3", "q4"])
        self.assertEqual([row["query"] for row in trace.recent(2)], ["q3", "q4"])
        self.assertEqual(trace.recent(99), snapshot)
        self.assertEqual(snapshot[-1]["tool_calls"], ["get_my_progress"])
        self.assertIn("created_at", snapshot[-1])
        trace.clear()
        self.assertEqual(trace.snapshot(), [])

    def test_context_budget_drops_oldest_observation(self):
        runner = AgentRunner(self.db, AgentContext(student_id=STUDENT_A),
                             toolbox=AgentToolbox(), llm=ScriptedLLM())
        # 单条观察进 prompt 会被裁到 900 字符, 因此要造足够多条才会撞上 12000 预算
        runner._observations = [
            {"tool": "get_my_progress", "args": {}, "observation": f"OBS{i:02d}" + "Z" * 880}
            for i in range(20)
        ]
        text = runner._context_text()
        self.assertLessEqual(len(text), 12000 + 200)
        self.assertNotIn("OBS00", text, "预算裁剪必须从最老的观察开始丢")
        self.assertIn("OBS19", text, "最新的观察必须保留")
        self.assertIn("较早的", text, "裁剪发生了就要在上下文里标注")

    def test_agent_context_rejects_invalid_student(self):
        with self.assertRaises(AgentToolError):
            AgentContext(student_id=0)
        with self.assertRaises(AgentToolError):
            AgentContext(student_id=None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
