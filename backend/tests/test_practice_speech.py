"""答辩语音的材料归属、临时转写、数据库绑定及供应商音频契约。"""
import io
import json
import unittest
import wave
from unittest.mock import patch

import httpx
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ai import providers
from app.ai.runtime_config import AISlot, invalidate_ai_config
from app.ai.speech import SpeechFactory, SpeechService
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.models import all as _models  # noqa: F401
from app.models.ai import AIDefenseSession, AIDefenseTurn, AIProvider
from app.modules.ai.router import read_question_aloud, speech_capabilities, transcribe_answer


def wav(rate=16000, seconds=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, rate, rate * seconds, "NONE", "not compressed"))
        writer.writeframes(bytes(2 * rate * seconds))
    return buffer.getvalue()


class PracticeSpeechTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.addCleanup(invalidate_ai_config)
        redis = self.enterContext(patch("app.shared.sysconfig.redis_client"))
        redis.get.return_value = None
        self.enterContext(patch("app.modules.ai.router._check_ai_rate"))
        self.owner = CurrentUser(8, "u", "学生", "STUDENT", 7)
        self.other = CurrentUser(9, "v", "学生", "STUDENT", 8)
        self.db.add_all([AIDefenseSession(id=10, student_id=7, status="RUNNING"),
                         AIDefenseTurn(id=101, session_id=10, turn_no=1, question="旧题", student_answer="已回答"),
                         AIDefenseTurn(id=102, session_id=10, turn_no=2, question="为什么选择这个方案？"),
                         AIProvider(id=20, name="本地语音", kind="custom", base_url="http://127.0.0.1:18285/v1",
                                    enabled=1)])
        self.db.commit()

    def test_db_bindings_and_optional_local_credentials(self):
        old = json.loads(speech_capabilities(self.owner, self.db).body)["data"]
        self.assertEqual(old, {"speech": False, "transcription": False, "max_recording_seconds": 90})
        with self.assertRaises(BusinessException) as exc:
            SpeechFactory.get(self.db)
        self.assertEqual(exc.exception.code, 50301)
        providers.save_bindings(self.db, {"speech": {"provider_id": 20, "model": "melo-tts-zh"},
                                          "transcription": {"provider_id": 20, "model": "vosk-small-cn"}})
        configured = json.loads(speech_capabilities(self.owner, self.db).body)["data"]
        self.assertTrue(configured["speech"] and configured["transcription"])
        self.assertEqual(SpeechFactory.get(self.db).slot.model, "melo-tts-zh")
        self.assertFalse(SpeechFactory.get(self.db, "transcription").slot.api_key)
        with self.assertRaises(BusinessException):
            providers.delete_provider(self.db, 20)
        providers.save_bindings(self.db, {"speech": {"provider_id": None}})
        self.assertFalse(json.loads(speech_capabilities(self.owner, self.db).body)["data"]["speech"])
        self.assertEqual(SpeechFactory.get(self.db, "transcription").slot.model, "vosk-small-cn")

    def test_only_owned_saved_question_is_synthesized(self):
        factory = self.enterContext(patch("app.modules.ai.router.SpeechFactory.get"))
        service = factory.return_value
        audio = wav()
        service.synthesize.return_value = audio
        for user, turn in [(self.other, 2), (self.owner, 99)]:
            with self.assertRaises(ResourceNotFoundException):
                read_question_aloud(10, turn, user, self.db)
        factory.assert_not_called()
        res = read_question_aloud(10, 2, self.owner, self.db)
        self.assertEqual((res.media_type, res.body), ("audio/wav", audio))
        service.synthesize.assert_called_once_with("为什么选择这个方案？")

    def test_written_questions_never_reach_tts(self):
        from pydantic import ValidationError

        from app.ai.defense.oral import oral_question
        from app.modules.ai.router import AnswerRequest

        for dirty in ("## 请根据 backend/app/ai/llm/factory.py", "请解释《设计报告.pdf》中的取舍？",
                      "在 HoldQueue.notify_next 中，谁先收到通知？", "调用 queue_pop() 得到什么？",
                      "请先概述《设计》第三章所引片段的内容，再说明它有何作用？"):
            self.assertFalse(oral_question(dirty), dirty)
        self.assertTrue(oral_question("如果读数超过 18.5 度，系统如何判断是否要提示异常？"))
        factory = self.enterContext(patch("app.modules.ai.router.SpeechFactory.get"))
        self.db.get(AIDefenseTurn, 102).question = "## 请根据 backend/app/ai/llm/factory.py"
        self.db.commit()
        with self.assertRaises(BusinessException) as exc:
            read_question_aloud(10, 2, self.owner, self.db)
        self.assertEqual(exc.exception.code, 40900)
        factory.assert_not_called()
        self.assertEqual(AnswerRequest(question="是否需要借阅证？", answer="  是  ").answer, "是")
        with self.assertRaises(ValidationError):
            AnswerRequest(question="是否需要借阅证？", answer="  ")

    def test_transcription_only_for_latest_unanswered_turn_does_not_submit(self):
        factory = self.enterContext(patch("app.modules.ai.router.SpeechFactory.get"))
        service = factory.return_value
        service.transcribe.return_value = "我选择这个方案。"
        audio = wav()
        def record():
            return UploadFile(filename="answer.wav", file=io.BytesIO(audio))
        for user, turn in [(self.other, 2), (self.owner, 1), (self.owner, 3)]:
            with self.assertRaises(BusinessException):
                transcribe_answer(10, turn, record(), user, self.db)
        factory.assert_not_called()
        response = transcribe_answer(10, 2, record(), self.owner, self.db)
        self.assertEqual(json.loads(response.body)["data"]["text"], "我选择这个方案。")
        service.transcribe.assert_called_once_with(audio)
        self.assertIsNone(self.db.get(AIDefenseTurn, 102).student_answer)


class SpeechClientTest(unittest.TestCase):
    def setUp(self):
        self.service = SpeechService(AISlot("speech", "openai", True, 20, "本机", "http://127.0.0.1:18285/v1",
                                            "", "local-model"))

    def with_upstream(self, handler):
        real_client = httpx.Client
        return patch("app.ai.speech.httpx.Client",
                     side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    def test_local_service_receives_only_saved_text_and_wav_request(self):
        audio = wav()
        def handle(req):
            self.assertEqual(req.url.path, "/v1/audio/speech")
            self.assertNotIn("authorization", req.headers)
            self.assertEqual(json.loads(req.content), {"model": "local-model", "input": "题目",
                                                      "voice": "default", "response_format": "wav"})
            return httpx.Response(200, content=audio)
        with self.with_upstream(handle) as client:
            self.assertEqual(self.service.synthesize("题目"), audio)
        self.assertFalse(client.call_args.kwargs["trust_env"])

    def test_temporary_wav_requires_format_and_duration(self):
        with patch.object(self.service, "_request") as request:
            for invalid in (b"bad wav", wav(8000), wav(seconds=91), wav()[:-1]):
                with self.assertRaises(BusinessException) as exc:
                    self.service.transcribe(invalid)
                self.assertEqual(exc.exception.code, 42200)
            request.assert_not_called()
        def handle(req):
            self.assertEqual(req.url.path, "/v1/audio/transcriptions")
            self.assertIn(b'name="file"; filename="answer.wav"', req.content)
            self.assertIn(b"local-model", req.content)
            return httpx.Response(200, json={"text": "  样例答案 "})
        with self.with_upstream(handle):
            self.assertEqual(self.service.transcribe(wav()), "样例答案")

    def test_invalid_or_truncated_speech_response_is_503(self):
        for value in (b"<html>error</html>", wav()[:-1]):
            with self.with_upstream(lambda req: httpx.Response(200, content=value)), self.assertRaises(BusinessException) as exc:
                self.service.synthesize("题目")
            self.assertEqual(exc.exception.code, 50303)
