"""同课题、同文件名、同函数名下，题目、追问与评分依据仍属于各自的学生。"""
import json
import unittest
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.ai.defense.service import DefenseService
from app.ai.embedding.factory import LocalHashEmbedding
from app.ai.llm.factory import MockLLM
from app.core.exceptions import BusinessException
from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import GraduationCycle, TopicSelection
from app.models.system import SysFile
from app.models.user import StudentProfile, SysUser
from tests.test_material_version import new_engine, seed


class SameTopicPracticeTest(unittest.TestCase):
    def setUp(self):
        engine = new_engine()
        self.addCleanup(engine.dispose)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        seed(self.db)
        self.db.add(SysUser(id=3, username="s002", real_name="学生乙", password_hash="x", role="STUDENT"))
        self.db.add(StudentProfile(id=2, user_id=3, student_no="s002"))
        self.db.add(GraduationCycle(id=2, academic_year="2026-2027", student_id=2, topic_id=1, status="ACTIVE"))
        self.db.add(TopicSelection(id=2, student_id=2, cycle_id=2, topic_id=1, teacher_id=1,
                                   status="APPROVED", active_flag=1))
        self.rules = {1: ("先到先处理", "popleft()"), 2: ("后到先处理", "pop()")}
        self.docs = {}
        for student, (order, operation) in self.rules.items():
            self.docs[student] = set()
            for name, kind, passage in (("设计报告.md", "PRACTICE_REPORT", "预约队列按" + order + "办理。"),
                                        ("预约源码.zip", "SOURCE_CODE", "def notify_next(q): return q." + operation)):
                file = SysFile(original_name=name, stored_name=str(student) + name, storage_path=str(student) + name,
                               uploader_id=student + 1, business_type="AI_PRACTICE", business_id=student)
                self.db.add(file)
                self.db.flush()
                doc = AIDocument(student_id=student, cycle_id=student, topic_id=1, document_type=kind,
                                 document_name=name, file_id=file.id, index_status="SUCCESS", chunk_count=1)
                self.db.add(doc)
                self.db.flush()
                self.docs[student].add(doc.id)
                self.db.add(AIDocumentChunk(document_id=doc.id, student_id=student, topic_id=1, document_type=kind,
                                            scope="STUDENT", chunk_index=0, section_title=name, content=passage))
        self.db.commit()
        self.service = DefenseService(self.db)

    def test_inconsistent_historical_selection_cannot_supply_another_students_topic(self):
        selection = self.db.get(TopicSelection, 1)
        selection.student_id = 2
        self.db.commit()
        with self.assertRaises(BusinessException):
            self.service._get_topic(1)
        selection.student_id = 1
        selection.topic_id = 99
        self.db.commit()
        with self.assertRaises(BusinessException):
            self.service._get_topic(1)

    def test_questions_followups_and_marks_are_private_even_for_identical_project_names(self):
        model = MockLLM()
        with patch("app.ai.defense.service.LLMFactory.get", return_value=model), \
                patch("app.ai.rag.retriever.EmbeddingFactory.get", return_value=LocalHashEmbedding(16)), \
                patch("app.ai.rag.retriever.vector_store.search", return_value=[(i, 1) for i in range(1, 5)]), \
                patch("app.ai.rag.retriever.HybridRetriever._apply_rerank", return_value=None), \
                patch.object(model, "complete", wraps=model.complete) as completions:
            banks = {}
            for student in self.rules:
                with self.assertRaises(BusinessException):
                    self.service.generate_questions(student, student + 1, list(self.docs[3 - student]), 3)
                banks[student] = self.service.generate_questions(student, student + 1, list(self.docs[student]), 3)
                sources = [s for q in banks[student] for s in q["sources"]]
                self.assertEqual({s["document_id"] for s in sources}, self.docs[student])
                self.assert_private(student, completions.call_args.args[1])

            for student in self.rules:
                other = 3 - student
                with self.assertRaises(BusinessException):
                    self.service.create_session(student, 3, [q["id"] for q in banks[other]])
                sid = self.service.create_session(student, 3, [q["id"] for q in banks[student]])["session_id"]
                turn = self.service.start_session(student, sid)
                with self.assertRaises(BusinessException):
                    self.service.evaluate_answer(other, sid, turn["question"], "请说明队列的行为", other + 1)
                for expected_followup in (False, True):
                    self.assertEqual(turn["is_follow_up"], expected_followup)
                    grade = self.service.evaluate_answer(student, sid, turn["question"],
                                                         "我按照本项目的预约顺序通知读者。", student + 1)
                    self.assertTrue(grade["assessment"])
                    self.assertTrue({s["document_id"] for s in grade["sources"]} <= self.docs[student])
                    self.assert_private(student, completions.call_args.args[1])
                    self.assert_private(student, json.dumps(grade["assessment"], ensure_ascii=False))
                    turn = self.service.next_question(student, sid)
                self.service.finish_session(student, sid)
                report = self.service.get_report(student, sid)
                self.assert_private(student, json.dumps(report["turns"], ensure_ascii=False))
                with self.assertRaises(BusinessException):
                    self.service.get_report(other, sid)

    def test_live_opening_and_frozen_rubric_stay_private_on_provider_timeout(self):
        from sqlalchemy import select

        from app.ai.defense.rubric import snapshot
        from app.models.ai import AIDefenseTurn

        with patch("app.ai.defense.dialogue.LLMFactory.get", side_effect=BusinessException("慢", 50303)):
            for student in self.rules:
                first = self.service.start_live(student, "identical-retry-id", 3)
                self.assertIn(self.rules[student][0], first["question"])
                self.assertNotIn(self.rules[3 - student][0], first["question"])
                self.assertEqual(first, self.service.start_live(student, "identical-retry-id", 3))
                with self.assertRaises(BusinessException):
                    self.service.advance_live(3 - student, first["session_id"], 1, "我按预约顺序处理")
                turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.session_id == first["session_id"]))
                data = snapshot(turn.reference_chunks)
                self.assertIn(self.rules[student][0], data["rubric"][0]["expectation"])
                self.assertTrue({s["document_id"] for s in data["sources"]} <= self.docs[student])

    def assert_private(self, student, text):
        self.assertTrue(any(rule in text for rule in self.rules[student]), text)
        for foreign_rule in self.rules[3 - student]:
            self.assertNotIn(foreign_rule, text)
