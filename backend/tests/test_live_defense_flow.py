"""Exercise the live API and out-of-band grading with a synthetic student/project."""
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_ai_defense import _enable_sqlite_autoincrement, _NoopRedis

from app.ai.defense.dialogue import choose_question
from app.ai.defense.rubric import snapshot
from app.ai.defense.service import DefenseService
from app.ai.llm.factory import MockLLM
from app.ai.rag.rag_service import RAGService
from app.core.exceptions import BusinessException, register_exception_handlers
from app.core.permissions import CurrentUser, require_student
from app.db.base import Base
from app.db.mysql import get_db
from app.models.ai import AIDefenseSession, AIDefenseTurn, AIDocumentChunk, AIUsageLog
from app.models.business import GraduationCycle, GraduationTopic, TopicSelection
from app.modules.ai.router import defense
from app.tasks.practice_reports import process_one

FACTS = ["权限变更后，服务清除Redis缓存中的旧权限。", "任务队列在重试前核对业务编号，避免重复执行。",
         "审批令牌只能用一次，过期后会拒绝请求。", "发起审批后，系统将申请交给负责人确认。"]


def source(text, cid):
    return {"chunk_id": cid, "document_id": 21, "document_type": "THESIS", "excerpt": text}


def choose(answer, *, may_follow=True, may_advance=True, passages=None):
    return choose_question(title="权限审批", sources=[source(x, i + 1) for i, x in enumerate(passages or FACTS)],
                           question="权限变更后如何处理？", answer=answer, asked=["权限变更后如何处理？"], used=set(),
                           may_follow=may_follow, may_advance=may_advance)


class LiveDialogueTest(unittest.TestCase):
    def test_slow_model_can_only_ask_about_supported_answer_specific_facts(self):
        with patch("app.ai.defense.dialogue.LLMFactory.get", side_effect=BusinessException("慢", 50303)):
            cache = choose("我用Redis做权限缓存")
            queue = choose("我用Redis来做任务队列")
            self.assertTrue(cache.follow and queue.follow)
            self.assertIn("旧的权限缓存", cache.text)
            self.assertIn("重复执行", queue.text)
            self.assertNotEqual(snapshot(cache.evidence)["rubric"], snapshot(queue.evidence)["rubric"])
            unrelated = choose("我用Redis做权限缓存", passages=[FACTS[1]])
            self.assertFalse(unrelated.follow)
            self.assertNotIn("权限", unrelated.text)
            self.assertIsNone(choose("我没有做测试", may_advance=False))

    def test_queue_retry_evidence_crosses_sentences_within_the_same_project_paragraph(self):
        auth = "Redis 保存借阅证的权限缓存，缓存键包含账户及授权版本。管理员变更读者角色时递增授权版本并删除旧缓存，下一次访问重新从权限表读取，从而立即撤销旧权限。"
        queue = "Redis 也承载图书到馆通知任务队列。每个任务持有唯一通知编号，消费者发送前先查询处理记录。若网络失败则重试，同一编号处理成功后便跳过后续重复发送。"
        prior = "每个读者凭有效借阅证加入队列，缺少借阅证的申请直接拒绝。重复预约只返回原位置。"
        material = auth + "\n" + queue
        with patch("app.ai.defense.dialogue.LLMFactory.get") as provider:
            provider.return_value.live_complete.return_value = json.dumps({
                "action": "next", "id": 2, "quote": auth.split("。")[1], "topic": "旧缓存",
                "question": "管理员怎样删除旧缓存？"})
            draft = choose("我们用 Redis 做任务队列。", passages=[prior, material])
            self.assertTrue(draft.follow)
            self.assertIn("重复执行", draft.text)
            point, = snapshot(draft.evidence)["rubric"]
            self.assertEqual(point["source_ids"], [2])
            self.assertIn(point["expectation"], queue)
            self.assertIn("唯一通知编号", point["expectation"])
            self.assertIn("跳过后续重复发送", point["expectation"])
            # Another mechanism, paragraph or document cannot fill in a missing queue rule.
            for fragments in ([prior], [queue.split("。")[0], queue.split("。")[2]],
                              [queue.split("。")[0] + "\n" + queue.split("。")[2]],
                              ["此处使用任务队列。权限缓存发生变更时，使用重试机制更新权限。"]):
                result = choose("我们用 Redis 做任务队列。", passages=fragments)
                self.assertFalse(result and result.follow)
            self.assertIn("旧的权限缓存", choose("我们用 Redis 做权限缓存。", passages=[material]).text)

    def test_model_must_return_personal_evidence_and_answer_anchor(self):
        class Examiner:
            def live_complete(self, _, payload):
                self.latest = json.loads(payload)["最新回答"]
                return json.dumps(self.data)

        answer, provider = "我加了审批令牌", Examiner()
        provider.data = {"action": "follow_up", "question": "审批令牌过期后如何处理？", "anchor": "审批令牌",
                         "topic": "审批令牌", "id": 3, "quote": FACTS[2].rstrip("。")}
        with patch("app.ai.defense.dialogue.LLMFactory.get", return_value=provider):
            result = choose(answer)
            self.assertTrue(result.follow)
            self.assertEqual(result.text, provider.data["question"])
            self.assertEqual(provider.latest, answer)
            self.assertFalse(choose(answer, may_follow=False).follow)
            for override in ({"question": "审批令牌为什么会过期，并说说你怎么测试？"},
                             {"quote": "其他学生的审批设计"}, {"anchor": "前端令牌"}):
                with patch.dict(provider.data, override):
                    self.assertFalse(choose(answer).follow)
            provider.data = {"action": "follow_up", "question": "Redis与权限缓存有什么关系？",
                             "anchor": "Redis", "topic": "缓存", "id": 1, "quote": FACTS[0]}
            self.assertIn("旧的权限缓存", choose("我用Redis做权限缓存").text)

    def test_code_only_model_translates_a_cited_line_without_speaking_the_identifier(self):
        code = {"chunk_id": 11, "document_id": 22, "document_type": "SOURCE_CODE",
                "excerpt": "def notify_next(q): return q.popleft()"}
        output = {"action": "next", "id": 11, "quote": "q.popleft()", "topic": "队首",
                  "question": "从队首取出预约者会怎样影响等候顺序？"}
        with patch("app.ai.defense.dialogue.LLMFactory.get") as provider:
            provider.return_value.live_complete.return_value = json.dumps(output)
            draft = choose_question(title="预约", sources=[code], question="", answer="", asked=[], used=set(),
                                    may_follow=False, may_advance=True)
        self.assertEqual(draft.text, output["question"])
        self.assertEqual(snapshot(draft.evidence)["rubric"][0]["expectation"], "q.popleft()")

    def test_no_authorized_evidence_rejects_opening_even_when_answer_sounds_specific(self):
        with self.assertRaises(BusinessException):
            choose_question(title="预约", sources=[], question="", answer="Redis权限缓存", asked=[], used=set(),
                            may_follow=False, may_advance=True)

    def test_long_heading_cannot_supply_a_fallback_or_a_model_mark(self):
        for heading in ("基于微服务架构的图书预约管理系统设计与实现", "第四章 预约队列缓存机制的总体设计"):
            with patch("app.ai.defense.dialogue.LLMFactory.get") as provider:
                provider.return_value.live_complete.return_value = json.dumps({
                    "action": "next", "question": "预约的设计有什么依据？", "quote": heading, "id": 1, "topic": "预约"})
                self.assertIsNone(choose("我使用了预约", may_follow=False, passages=[heading]))
        with patch("app.ai.defense.dialogue.LLMFactory.get", side_effect=BusinessException("慢", 50303)):
            draft = choose("", may_follow=False, passages=["读者归还书籍后，系统通知队首的预约者。"])
            self.assertIn("归还书籍", draft.text)

    def test_complete_answer_can_advance_without_forced_followup(self):
        with patch("app.ai.defense.dialogue.LLMFactory.get", return_value=MockLLM()):
            self.assertFalse(choose("我用Redis做权限缓存，权限变更后删除缓存。").follow)


class LiveDefenseApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        for model in (AIDefenseSession, AIDefenseTurn, AIDocumentChunk, AIUsageLog):
            _enable_sqlite_autoincrement(model)
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        for target in ("app.ai.prompts.redis_client", "app.shared.sysconfig.redis_client"):
            self.enterContext(patch(target, _NoopRedis()))
        self.enterContext(patch("app.modules.ai.router._check_ai_rate"))
        self.enterContext(patch.object(RAGService, "practice_documents", return_value=[SimpleNamespace(id=21, document_type="THESIS", document_name="练习报告")]))
        self.owner = 31
        with self.factory.begin() as db:
            db.add_all([AIDocumentChunk(id=i, student_id=self.owner, document_id=21, scope="STUDENT",
                                        document_type="THESIS", chunk_index=i, content=fact)
                        for i, fact in enumerate(FACTS, 1)])
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
        with patch("app.ai.defense.service.LLMFactory.get", return_value=MockLLM()):
            key = uuid4()
            start = self._start(key)
            self.assertIn("权限", start["question"])
            self.assertEqual(start["sources"][0]["chunk_id"], 1)
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
            self.assertIn("旧的权限缓存", second["question"])
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

        with patch.object(RAGService, "retrieve_practice", side_effect=AssertionError("must grade pinned facts")) as retrieval, \
                patch("app.ai.defense.service.LLMFactory.get", return_value=MockLLM()):
            for _ in range(5):
                self.assertTrue(process_one(self.factory))
            self.assertFalse(process_one(self.factory))
        retrieval.assert_not_called()
        final = self.client.get(path + "/report").json()["data"]
        self.assertEqual(final["report_status"], "ready")
        self.assertEqual(final["score_basis"], "main_questions")
        self.assertEqual(len(final["turns"]), 4)
        self.assertTrue(all(t["assessment"] and t["total_score"] is not None for t in final["turns"]))
        main = [t["total_score"] for t in final["turns"] if not t["is_follow_up"]]
        self.assertEqual(final["overall_score"], round(sum(main) / len(main)))

    def test_stale_report_token_cannot_publish(self):
        with patch("app.ai.defense.dialogue.LLMFactory.get", return_value=MockLLM()):
            session_id = self._start()["session_id"]
        with self.factory() as db:
            with self.assertRaises(BusinessException) as rejected:
                DefenseService(db).build_live_report(session_id, "no-current-lease")
            self.assertEqual(rejected.exception.code, 40900)

    def test_old_recording_freezes_verified_anchors_before_retriable_grading(self):
        answer = "我会清除旧权限"
        with self.factory.begin() as db:
            session = AIDefenseSession(student_id=self.owner, topic_id=4, status="FINISHED", live_request_id=str(uuid4()),
                                       report_status="pending", report_claim="lease",
                                       report_lease_until=datetime.now() + timedelta(minutes=5))
            db.add(session)
            db.flush()
            turn = AIDefenseTurn(session_id=session.id, turn_no=1, question="权限变更后如何处理？", student_answer=answer,
                                 reference_chunks=json.dumps([source(FACTS[0], 1)]))
            db.add(turn)
            db.flush()
            sid, tid = session.id, turn.id

        transcripts = []
        grade = {"accuracy": 29, "logic": 20, "clarity": 18, "depth": 12, "feedback": "说明了失效操作。",
                 "source_ids": [1], "assessment": []}

        def model(_system, user):
            data = json.loads(user)
            transcripts.append(data)
            if len(transcripts) == 1:
                self.assertNotIn("student_answer", data)
                return json.dumps({"rubric": [{"expectation": FACTS[0], "source_ids": [1]}]})
            self.assertEqual(data["rubric"][0]["expectation"], FACTS[0])
            if len(transcripts) == 3:
                grade["assessment"] = [{"id": 1, "verdict": "supported", "answer_quote": "清除旧权限", "reason": "符合材料。"}]
            return json.dumps(grade)

        with patch.object(RAGService, "retrieve_practice", return_value=[]) as retrieval, \
                patch("app.ai.defense.service.LLMFactory.get", return_value=SimpleNamespace(name="probe", complete=model)):
            with self.factory() as db:
                with self.assertRaises(BusinessException):
                    DefenseService(db).score_live_turn(sid, "lease", tid)
            with self.factory() as db:
                saved = snapshot(db.get(AIDefenseTurn, tid).reference_chunks)["rubric"]
                self.assertEqual([FACTS[0]], [r["expectation"] for r in saved])
                self.assertIsNone(db.get(AIDefenseTurn, tid).total_score)
                DefenseService(db).score_live_turn(sid, "lease", tid)
                self.assertEqual(saved, snapshot(db.get(AIDefenseTurn, tid).reference_chunks)["rubric"])
                self.assertEqual(db.get(AIDefenseTurn, tid).accuracy_score, 35)
            self.assertEqual(len(transcripts), 3)
            retrieval.assert_called_once()
