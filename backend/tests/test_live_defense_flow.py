"""Exercise the live API and out-of-band grading with a synthetic student/project."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_ai_defense import _enable_sqlite_autoincrement, _NoopRedis

from app.ai.defense.dialogue import OPENER, choose_question
from app.ai.defense.service import DefenseService
from app.ai.llm.factory import MockLLM
from app.ai.rag.rag_service import RAGService
from app.ai.rag.retriever import RetrievedChunk
from app.core.exceptions import BusinessException, register_exception_handlers
from app.core.permissions import CurrentUser, require_student
from app.db.base import Base
from app.db.mysql import get_db
from app.models.ai import AIDefenseSession, AIDefenseTurn, AIUsageLog
from app.models.business import GraduationCycle, GraduationTopic, TopicSelection
from app.modules.ai.router import defense
from app.tasks.practice_reports import process_one


def choose(answer, *, may_follow=True, may_advance=True):
    return choose_question(title="进度管理系统", excerpts=["系统通过Redis管理权限缓存"], question=OPENER,
                           answer=answer, asked=[OPENER], main_index=1, may_follow=may_follow,
                           may_advance=may_advance)


class LiveDialogueTest(unittest.TestCase):
    def test_failure_fallback_remains_answer_specific(self):
        with patch("app.ai.defense.dialogue.LLMFactory.get", side_effect=BusinessException("慢", 50303)):
            cache = choose("我用Redis做权限缓存")
            queue = choose("我用Redis来做任务队列")
            metric = choose("混合检索的召回提高12%")
            incomplete = choose("我没有做测试", may_advance=False)
        self.assertTrue(cache[1])
        self.assertTrue(queue[1])
        self.assertIn("旧数据怎么失效", cache[0])
        self.assertIn("重复执行", queue[0])
        self.assertIn("12%", metric[0])
        self.assertIsNone(incomplete)

    def test_latest_answer_validation_rejects_stacking_invented_and_repeated_anchor(self):
        class Examiner:
            def __init__(self, question, anchor):
                self.question, self.anchor = question, anchor

            def live_complete(self, _, payload):
                self.latest = json.loads(payload)["最新回答"]
                return json.dumps({"action": "follow_up", "question": self.question, "anchor": self.anchor})

        answer = "我加了审批令牌"
        candidate = Examiner("你说加了审批令牌，你怎么验证它过期后不能用？", "审批令牌")
        with patch("app.ai.defense.dialogue.LLMFactory.get", return_value=candidate):
            self.assertEqual(choose(answer), (candidate.question, True))
            self.assertEqual(candidate.latest, answer)
            self.assertFalse(choose(answer, may_follow=False)[1])
            candidate.question = "审批令牌为什么会过期，并说说你怎么测试？"
            self.assertFalse(choose(answer)[1])
            candidate.anchor, candidate.question = "前端令牌", "前端令牌怎么失效？"
            self.assertFalse(choose(answer)[1])

    def test_complete_answer_can_advance_without_forced_followup(self):
        answer = "我用Redis做权限缓存，用修改权限后的对照组证明读取了新权限。"
        with patch("app.ai.defense.dialogue.LLMFactory.get", return_value=MockLLM()):
            self.assertFalse(choose(answer)[1])


class LiveDefenseApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        for model in (AIDefenseSession, AIDefenseTurn, AIUsageLog):
            _enable_sqlite_autoincrement(model)
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        for target in ("app.ai.prompts.redis_client", "app.shared.sysconfig.redis_client"):
            self.enterContext(patch(target, _NoopRedis()))
        self.enterContext(patch("app.modules.ai.router._check_ai_rate"))
        self.enterContext(patch.object(RAGService, "practice_documents", return_value=[SimpleNamespace(id=21)]))
        self.hit = RetrievedChunk(1, 21, "THESIS", "测试", "服务采用权限校验防止旧授权被复用。", 1,
                                  extra={"document_name": "练习报告"})
        self.owner = 31
        with self.factory.begin() as db:
            db.add_all([GraduationTopic(id=4, teacher_id=1, title="权限审批系统"),
                        GraduationCycle(id=8, student_id=self.owner, topic_id=4, academic_year="2026-2027", status="ACTIVE"),
                        TopicSelection(id=11, student_id=self.owner, cycle_id=8, topic_id=4, teacher_id=1,
                                       status="APPROVED", active_flag=1)])
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(defense)
        app.dependency_overrides[require_student] = lambda: CurrentUser(
            id=3, username="test-student", real_name="测试", role="STUDENT", profile_id=self.owner)

        def db_override():
            with self.factory() as db:
                yield db

        app.dependency_overrides[get_db] = db_override
        self.client = self.enterContext(TestClient(app))

    def _start(self, key=None):
        response = self.client.post("/ai/defense/live/start", json={"request_id": str(key or uuid4()), "question_count": 3})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_end_to_end_scoring_is_deferred_with_retry_isolation(self):
        with patch("app.ai.defense.service.LLMFactory.get", side_effect=AssertionError("opening must not call LLM")):
            key = uuid4()
            start = self._start(key)
            self.assertEqual(start["question"], OPENER)
            self.assertEqual(self._start(key)["session_id"], start["session_id"])
        session = start["session_id"]
        path = f"/ai/defense/sessions/{session}"
        with patch.object(DefenseService, "_grade", side_effect=AssertionError("must not grade during call")), \
                patch("app.ai.defense.dialogue.LLMFactory.get", return_value=MockLLM()), \
                patch.object(RAGService, "retrieve_practice", side_effect=AssertionError("live has no embedding")):
            def advance(number, answer):
                response = self.client.post(path + "/live/advance", json={"turn_no": number, "answer": answer})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()["data"]
            answer = "我用Redis做权限缓存"
            second = advance(1, answer)
            self.assertEqual(second["turn_no"], 2)
            self.assertTrue(second["is_follow_up"])
            self.assertIn("旧数据", second["question"])
            self.assertEqual(advance(1, answer), second)
            self.assertEqual(self.client.post(path + "/next").json()["data"], second)
            self.assertEqual(self.client.post(path + "/live/advance",
                                             json={"turn_no": 1, "answer": "后来改的"}).status_code, 409)
            third = advance(2, "权限变更时删缓存")
            self.assertFalse(third["is_follow_up"])
            fourth = advance(3, "我负责做审批流")
            self.assertFalse(fourth["is_follow_up"])
            self.assertTrue(advance(4, "按进度分配不同角色")["finished"])
            self.assertTrue(advance(4, "按进度分配不同角色")["finished"])
        report = self.client.get(path + "/report")
        self.assertEqual(report.json()["data"]["report_status"], "pending")
        with self.factory() as db:
            row = db.get(AIDefenseSession, session)
            self.assertEqual(row.answered_count, 3)
            turns = list(db.scalars(select(AIDefenseTurn).order_by(AIDefenseTurn.turn_no)))
            self.assertEqual(len(turns), 4)
            self.assertTrue(all(t.total_score is None for t in turns))
        self.owner += 1
        self.assertEqual(self.client.get(path + "/report").status_code, 404)
        self.assertEqual(self.client.post(path + "/report/retry").status_code, 404)
        self.owner -= 1

        with patch.object(RAGService, "retrieve_practice", return_value=[self.hit]) as retrieval, \
                patch("app.ai.defense.service.LLMFactory.get", return_value=MockLLM()):
            for _ in range(5):
                self.assertTrue(process_one(self.factory))
            self.assertFalse(process_one(self.factory))
        self.assertTrue(all(call.kwargs["document_ids"] == [21] for call in retrieval.call_args_list))
        final = self.client.get(path + "/report").json()["data"]
        self.assertEqual(final["report_status"], "ready")
        self.assertEqual(final["score_basis"], "main_questions")
        self.assertEqual(len(final["turns"]), 4)
        self.assertTrue(all(t["assessment"] and t["total_score"] is not None for t in final["turns"]))
        main = [t["total_score"] for t in final["turns"] if not t["is_follow_up"]]
        self.assertEqual(final["overall_score"], round(sum(main) / len(main)))

    def test_stale_report_token_cannot_publish(self):
        session_id = self._start()["session_id"]
        with self.factory() as db:
            with self.assertRaises(BusinessException) as rejected:
                DefenseService(db).build_live_report(session_id, "no-current-lease")
            self.assertEqual(rejected.exception.code, 40900)
