"""AI 边界可信单测: 限流降级(D41)/结束判定(D46)/计费完整(D47)/解析兜底(D48)/入口校验(D50).

全部用例跑在内存 SQLite 上, 通过依赖覆盖直接驱动真实路由函数与真实服务代码,
不依赖 MySQL/Redis/Celery; Redis 一律用桩替换, 避免污染共享实例。
"""
import json
import unittest
from unittest import mock

import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.defense.service import DefenseService
from app.ai.llm.factory import LLMFactory
from app.ai.rag.rag_service import RAGService
from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser, require_student
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.models.ai import (
    AIDefenseQuestion,
    AIDefenseSession,
    AIDefenseTurn,
    AIDocument,
    AIDocumentChunk,
    AIUsageLog,
)
from app.models.business import GraduationCycle, GraduationTopic, TopicSelection
from app.models.system import SystemConfig
from app.modules.ai import router as ai_router
from app.modules.ai.router import RAGQueryRequest, _check_ai_rate

STUDENT = 21
USER_ID = 7

_AUTOINCREMENT_READY: set[type] = set()
_ID_SEQ: dict[type, int] = {}


def _enable_sqlite_autoincrement(model) -> None:
    """SQLite 下 BigInteger 主键不自增(只在 MySQL 侧 AUTO_INCREMENT), 补上 MySQL 语义."""
    if model in _AUTOINCREMENT_READY:
        return
    _AUTOINCREMENT_READY.add(model)

    @event.listens_for(model, "before_insert")
    def _assign_pk(mapper, connection, target):  # noqa: ANN001
        if getattr(target, "id", None) is None:
            current = connection.execute(select(func.coalesce(func.max(model.id), 0))).scalar()
            # 同一次 flush 里的多行在 DB 侧还看不到彼此, 用内存计数器兜住
            nxt = max(int(current or 0), _ID_SEQ.get(model, 0)) + 1
            _ID_SEQ[model] = nxt
            target.id = nxt


class _NoopRedis:
    """静默 Redis 桩: 让被测代码走"缓存未命中 + 写缓存失败被吞"的真实分支."""

    def get(self, *a, **k):
        return None

    def set(self, *a, **k):
        return True

    def setex(self, *a, **k):
        return True

    def incr(self, *a, **k):
        return 1

    def delete(self, *a, **k):
        return 1

    def exists(self, *a, **k):
        return False


class _BoomRedis:
    """Redis 全故障桩: 任何调用都抛 RedisError."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise redis.exceptions.RedisError("redis down")
        return _boom


class _RecordingPipe:
    def __init__(self):
        self.calls = []

    def set(self, key, value, **kwargs):
        self.calls.append(("set", dict(key=key, value=value, **kwargs)))
        return self

    def incr(self, key, **kwargs):
        self.calls.append(("incr", dict(key=key, **kwargs)))
        return self

    def execute(self):
        self.calls.append(("execute", {}))
        return [True, 1]


class _RecordingRedis:
    def __init__(self):
        self.pipe = _RecordingPipe()
        self.pipeline_kwargs = {}

    def pipeline(self, **kwargs):
        self.pipeline_kwargs = kwargs
        return self.pipe


class _FakeLLM:
    """可编程 LLM 桩: 覆盖 complete / stream 两条路径."""

    def __init__(self, payload="{}", pieces=("片段一", "片段二")):
        self.name = "mock"
        self.payload = payload
        self.pieces = tuple(pieces)
        self.streamed = False

    def complete(self, system, user):
        return self.payload

    def stream(self, system, user, history=None):
        self.streamed = True
        yield from self.pieces


def _fake_user():
    return CurrentUser(id=USER_ID, username="stu21", real_name="学生二十一",
                       role="STUDENT", profile_id=STUDENT)


class AiDefenseBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        for model in (AIDocument, AIDocumentChunk, AIDefenseQuestion, AIDefenseSession,
                      AIDefenseTurn, AIUsageLog, GraduationTopic, TopicSelection, GraduationCycle):
            _enable_sqlite_autoincrement(model)
        self.SessionFactory = sessionmaker(bind=self.engine)
        self.db = Session(self.engine)
        # 真实 Redis 不参与单测: 提示词缓存与系统配置缓存都换成桩
        for target in ("app.ai.prompts.redis_client", "app.shared.sysconfig.redis_client"):
            patcher = mock.patch(target, _NoopRedis())
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ---------- 造数据 ----------

    def _student_context(self):
        """ACTIVE cycle + 通过选题 + 题目 + 一条已索引 chunk(满足 _has_knowledge)."""
        self.db.add(GraduationCycle(id=1, academic_year="2024-2025", student_id=STUDENT, status="ACTIVE"))
        self.db.add(GraduationTopic(id=1, teacher_id=1, title="毕业设计答辩管理系统"))
        self.db.add(TopicSelection(id=1, student_id=STUDENT, cycle_id=1, topic_id=1, teacher_id=1,
                                   status="APPROVED", active_flag=1))
        self.db.add(AIDocumentChunk(id=1, document_id=1, student_id=STUDENT, topic_id=1, scope="STUDENT",
                                    document_type="THESIS", chunk_index=0, section_title="第一章",
                                    content="本系统采用分层架构实现毕业设计答辩全流程管理。", token_count=20))
        self.db.commit()

    def _session(self, question_count=3, status="RUNNING"):
        session = AIDefenseSession(student_id=STUDENT, topic_id=1, status=status,
                                   question_count=question_count, answered_count=0)
        self.db.add(session)
        self.db.commit()
        return session

    def _question(self, qid, text, qtype, used=0):
        row = AIDefenseQuestion(id=qid, student_id=STUDENT, question=text, question_type=qtype,
                                difficulty="MEDIUM", used=used)
        self.db.add(row)
        self.db.commit()
        return row

    def _client(self):
        app = FastAPI()
        app.include_router(ai_router.router, prefix="/api/v1")
        app.dependency_overrides[require_student] = _fake_user
        app.dependency_overrides[get_db] = lambda: self.db
        return TestClient(app, raise_server_exceptions=False)

    # ---------- D41: 限流原子性与 Redis 故障降级 ----------

    def test_rate_limit_uses_atomic_set_nx_then_incr(self):
        """D41: 计数必须先 SET NX EX 打底再 INCR, 且两步在同一事务型 pipeline 提交."""
        recorder = _RecordingRedis()
        with mock.patch("app.modules.ai.router.redis_client", recorder):
            _check_ai_rate(_fake_user(), self.db)

        self.assertEqual(recorder.pipeline_kwargs.get("transaction"), True, "必须用事务型 pipeline")
        names = [name for name, _ in recorder.pipe.calls]
        self.assertEqual(names, ["set", "incr", "execute"], "顺序必须是 set → incr → execute")
        set_kwargs = recorder.pipe.calls[0][1]
        self.assertIs(set_kwargs.get("nx"), True, "首次写入必须是 NX, 不能覆盖已有 TTL")
        self.assertEqual(set_kwargs.get("ex"), 60, "TTL 必须与限流窗口一致(60s)")
        self.assertEqual(set_kwargs.get("value"), 0,
                         "计数初值必须是 0: 写 1 会让每个窗口少算一次, 阈值实际变成 N-1")

    def test_rate_limit_redis_error_lets_request_through(self):
        """D41: RedisError 时降级放行, 接口返回 200 而不是 500."""
        with mock.patch("app.modules.ai.router.redis_client", _BoomRedis()):
            _check_ai_rate(_fake_user(), self.db)  # 不抛异常
            resp = self._client().post("/api/v1/ai/documents/index-reports")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("code"), 0, resp.text)

    def test_rate_limit_threshold_matches_configured_value(self):
        """限流必须严格按填写值放行: limit=2 时第 1、2 次通过, 第 3 次才 429.

        历史 bug: 计数初值写 1, 每个窗口多算一次, 上限填 1 时第一个请求就被 429,
        等于「系统设置 → AI 每分钟上限」没有按填写值生效。
        """
        self.db.add(SystemConfig(key="ai_rate_limit_per_minute", value="2"))
        self.db.commit()

        def _redis_returning(current):
            class _P:
                def set(self, *a, **k):
                    return self

                def incr(self, *a, **k):
                    return self

                def execute(self):
                    return [True, current]

            return mock.Mock(pipeline=lambda **k: _P())

        for current in (1, 2):
            with mock.patch("app.modules.ai.router.redis_client", _redis_returning(current)):
                _check_ai_rate(_fake_user(), self.db)  # 未超阈值, 不抛异常
        with mock.patch("app.modules.ai.router.redis_client", _redis_returning(3)):
            with self.assertRaises(BusinessException) as ctx:
                _check_ai_rate(_fake_user(), self.db)
        self.assertEqual(ctx.exception.code, 42901)

    def test_rate_limit_over_threshold_still_429(self):
        """限流本身仍然生效: 超阈值必须是业务码 42901, 不能被降级吃掉."""
        self.db.add(SystemConfig(key="ai_rate_limit_per_minute", value="1"))
        self.db.commit()

        class _Pipe:
            def set(self, *a, **k):
                return self

            def incr(self, *a, **k):
                return self

            def execute(self):
                return [True, 5]

        with mock.patch("app.modules.ai.router.redis_client", mock.Mock(pipeline=lambda **k: _Pipe())):
            with self.assertRaises(BusinessException) as ctx:
                _check_ai_rate(_fake_user(), self.db)
        self.assertEqual(ctx.exception.code, 42901)

    # ---------- D51: 两轮题型策略 ----------

    def test_next_turn_prefers_uncovered_question_type(self):
        """D51: 第二题必须优先考未覆盖题型(题型来自 question_id 真实回查, 不再是空集合)."""
        session = self._session()
        basic = self._question(1, "请介绍系统整体架构", "BASIC", used=1)
        self._question(2, "为什么选择 FastAPI", "TECHNOLOGY")
        self._question(3, "系统的背景与目标是什么", "BASIC")
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=basic.id, turn_no=1,
                                  question=basic.question, student_answer="已答", is_follow_up=0))
        self.db.commit()

        turn = DefenseService(self.db)._next_turn(self.db, session)
        picked = self.db.get(AIDefenseQuestion, turn.question_id)
        self.assertEqual(picked.question_type, "TECHNOLOGY", "必须优先选未覆盖题型")
        self.assertEqual(picked.used, 1, "选中即置 used, 防止重复出题")

    def test_next_turn_second_round_returns_remaining_question(self):
        """D51: 所有题型都覆盖后, 第二轮仍要回到未问过的题, 不能直接判定无题可出."""
        session = self._session()
        basic = self._question(1, "请介绍系统整体架构", "BASIC", used=1)
        tech = self._question(2, "为什么选择 FastAPI", "TECHNOLOGY", used=1)
        self._question(3, "系统的背景与目标是什么", "BASIC")
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=basic.id, turn_no=1,
                                  question=basic.question, student_answer="已答", is_follow_up=0))
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=tech.id, turn_no=2,
                                  question=tech.question, student_answer="已答", is_follow_up=0))
        self.db.commit()

        turn = DefenseService(self.db)._next_turn(self.db, session)
        self.assertIsNotNone(turn, "第二轮必须继续出题")
        self.assertEqual(self.db.get(AIDefenseQuestion, turn.question_id).id, 3)

    # ---------- D46: 结束判定只统计非追问轮 ----------

    def test_follow_up_turn_never_finishes_session(self):
        """D46: 只答了追问轮时不得判定结束(追问是同一正题的追加)."""
        session = self._session(question_count=1)
        self._question(1, "请介绍系统整体架构", "BASIC", used=1)
        self._question(2, "数据库如何设计", "DATABASE")
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=1, turn_no=1,
                                  question="请介绍系统整体架构", student_answer=None,
                                  is_follow_up=0, follow_up_question="具体讲讲分层边界"))
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=1, turn_no=2,
                                  question="具体讲讲分层边界", student_answer="分三层……",
                                  is_follow_up=1))
        self.db.commit()

        result = DefenseService(self.db).next_question(STUDENT, session.id)
        self.assertFalse(result.get("finished"), "追问轮计入会让学生正题没答完就提前交卷")
        self.assertIn("question", result)

    def test_main_question_answered_reaches_question_count(self):
        """D46 正向: 正题答满 question_count 才结束并出报告."""
        session = self._session(question_count=1)
        self.db.add(AIDefenseTurn(session_id=session.id, question_id=None, turn_no=1,
                                  question="请介绍系统整体架构", student_answer="分三层……",
                                  is_follow_up=0, accuracy_score=20, logic_score=15,
                                  clarity_score=12, depth_score=10, total_score=57))
        self.db.commit()
        report_payload = json.dumps({"overall_score": 57, "summary": "基本合格", "dimension_scores": {},
                                     "strengths": [], "weaknesses": [], "risky_questions": [],
                                     "suggestions": []})
        with mock.patch.object(LLMFactory, "get", return_value=_FakeLLM(report_payload)):
            result = DefenseService(self.db).next_question(STUDENT, session.id)

        self.assertTrue(result.get("finished"))
        self.assertEqual(result["report"]["overall_score"], 57)

    # ---------- D51: 题面以 turn 为准 ----------

    def test_evaluate_answer_rejects_mismatched_question(self):
        """D51: 客户端传入题面与当前轮次不一致 → 40003, 不允许错位评分."""
        session = self._session()
        self.db.add(AIDefenseTurn(session_id=session.id, turn_no=1, question="请介绍系统整体架构",
                                  student_answer=None, is_follow_up=0))
        self.db.commit()

        with self.assertRaises(BusinessException) as ctx:
            DefenseService(self.db).evaluate_answer(STUDENT, session.id, "另一个完全不同的题目",
                                                    "我的回答", USER_ID)
        self.assertEqual(ctx.exception.code, 40003)

    def test_evaluate_answer_uses_turn_question(self):
        """D51: 题面以 turn.question 为准(仅空白差异视为同一题), 落库的是轮次题面."""
        session = self._session()
        self.db.add(AIDefenseTurn(session_id=session.id, turn_no=1, question="请介绍  系统整体架构",
                                  student_answer=None, is_follow_up=0))
        self.db.commit()
        payload = json.dumps({"accuracy": 20, "logic": 15, "clarity": 12, "depth": 10,
                              "feedback": "结构清楚", "follow_up_needed": False})
        with mock.patch.object(LLMFactory, "get", return_value=_FakeLLM(payload)), \
                mock.patch.object(RAGService, "retrieve", return_value=[]):
            result = DefenseService(self.db).evaluate_answer(
                STUDENT, session.id, "请介绍 系统整体架构", "我先说分层再讲流转", USER_ID)

        turn = self.db.execute(select(AIDefenseTurn).where(AIDefenseTurn.session_id == session.id)) \
            .scalars().first()
        self.assertEqual(turn.question, "请介绍  系统整体架构", "落库题面必须取自轮次")
        self.assertEqual(turn.student_answer, "我先说分层再讲流转")
        self.assertEqual(result["scores"]["total"], 57)

    # ---------- D47: 流式异常与计费完整 ----------

    def _usage_rows(self):
        return self.db.execute(select(func.count(AIUsageLog.id))).scalar() or 0

    def test_answer_stream_error_event_then_done_and_usage_logged(self):
        """D47: 流内异常必须转成 error 事件 + 收尾 done, 且用量记录不丢."""
        with mock.patch.object(RAGService, "retrieve", side_effect=RuntimeError("检索炸了")):
            events = list(RAGService(self.db).answer_stream("系统怎么分层", STUDENT,
                                                            user_id=USER_ID, scene="QA"))

        self.assertEqual([e["event"] for e in events], ["error", "done"], events)
        self.assertIn("message", events[0]["data"])
        self.assertEqual(events[-1]["data"]["status"], "FAILED")
        self.assertEqual(self._usage_rows(), 1, "异常路径也必须写用量记录(计费不丢)")

    def test_answer_stream_client_disconnect_still_logs_usage(self):
        """D47: 客户端提前断连(生成器被 close)也要在 finally 写用量记录."""
        fake = _FakeLLM(pieces=("第一段", "第二段"))
        with mock.patch.object(LLMFactory, "get", return_value=fake), \
                mock.patch.object(RAGService, "retrieve", return_value=[]):
            gen = RAGService(self.db).answer_stream("系统怎么分层", STUDENT, user_id=USER_ID, scene="QA")
            self.assertEqual(next(gen)["event"], "meta")
            self.assertEqual(next(gen)["event"], "delta")
            gen.close()  # 模拟客户端断连
        self.assertEqual(self._usage_rows(), 1, "断连也必须留下计费记录")

    # ---------- D48: LLM 非 JSON 兜底 ----------

    def test_generate_questions_falls_back_on_non_json(self):
        """D48: 问题生成返回非 JSON 时回落固定模板, 不 500."""
        self._student_context()
        with mock.patch.object(LLMFactory, "get", return_value=_FakeLLM("抱歉, 这里不是 JSON")), \
                mock.patch.object(RAGService, "retrieve", return_value=[]):
            items = DefenseService(self.db).generate_questions(STUDENT, USER_ID)

        self.assertEqual(len(items), 3, items)
        self.assertIn("整体架构", items[0]["question"])
        self.assertTrue(all(i["question_type"] for i in items))

    def test_ppt_outline_falls_back_on_non_json(self):
        """D48: PPT 大纲非 JSON 时回落固定模板, 页面结构可直接展示."""
        self._student_context()
        with mock.patch.object(LLMFactory, "get", return_value=_FakeLLM("这不是 JSON 文本")), \
                mock.patch.object(RAGService, "retrieve", return_value=[]):
            outline = DefenseService(self.db).generate_ppt_outline(STUDENT, USER_ID)

        self.assertIn("title", outline)
        self.assertGreaterEqual(len(outline["pages"]), 6)
        self.assertTrue(all("speaker_notes" in p for p in outline["pages"]))

    # ---------- D50: 入口校验与分页 ----------

    def test_rag_query_request_rejects_unknown_document_type(self):
        """D50: document_types 枚举强校验, 非法值进不了 RediSearch TAG 拼接."""
        req = RAGQueryRequest(query="系统架构", document_types=["THESIS", "DEFENSE_RULE"])
        self.assertEqual([t.value for t in req.document_types], ["THESIS", "DEFENSE_RULE"])
        with self.assertRaises(ValidationError):
            RAGQueryRequest(query="系统架构", document_types=["THESIS} @content:*"])

    def test_list_documents_paginated_and_capped(self):
        """D50: 文档列表分页(page/size), size 上限 100, 超出由入口拒绝."""
        for i in range(3):
            self.db.add(AIDocument(id=i + 1, student_id=STUDENT, document_type="THESIS",
                                   document_name="论文" + str(i + 1), scope="STUDENT",
                                   index_status="SUCCESS", version=1))
        self.db.commit()

        client = self._client()
        resp = client.get("/api/v1/ai/documents", params={"page": 2, "size": 2})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["page_size"], 2)
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["items"]), 1, "第 2 页只剩 1 条")
        self.assertEqual(data["items"][0]["document_name"], "论文1", "按 id 倒序分页")

        bad = client.get("/api/v1/ai/documents", params={"page": 1, "size": 101})
        self.assertIn(bad.status_code, (400, 422), "size 上限 100 必须在入口拦住")


if __name__ == "__main__":
    unittest.main()
