"""答辩语音的供应商客户端。朗读与转写分别从数据库绑定解析。"""
import io
import json
import wave
from typing import Literal

import httpx
from sqlalchemy.orm import Session

from app.ai.net import is_loopback_url
from app.ai.runtime_config import AISlot, resolve_ai_config
from app.core.exceptions import BusinessException
from app.shared.upload_policy import MAX_RECORDING_SECONDS, RECORDING_SAMPLE_RATE

MAX_QUESTION_CHARS = 1600
MAX_WAV_BYTES = 32 * 1024 * 1024


def _validate_wav(data: bytes, *, recording: bool = False) -> None:
    try:
        with wave.open(io.BytesIO(data)) as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
            channels, width = wav.getnchannels(), wav.getsampwidth()
            if recording and (rate != RECORDING_SAMPLE_RATE or channels != 1 or width != 2
                              or frames > rate * MAX_RECORDING_SECONDS):
                raise ValueError
            if not (8000 <= rate <= 96000 and 0 < frames <= rate * 600 and channels in (1, 2)):
                raise ValueError
            wav.setpos(frames - 1)
            if len(wav.readframes(1)) != width * channels:
                raise ValueError
    except (wave.Error, EOFError, ValueError, OSError, OverflowError):
        if recording:
            raise BusinessException("请使用有效的 16 kHz 单声道 PCM16 WAV 录音，最长 90 秒", 42200) from None
        raise BusinessException("语音服务返回的 WAV 音频无效，请稍后重试", 50303) from None


class SpeechService:
    def __init__(self, slot: AISlot):
        self.slot = slot

    async def _request(self, path: str, max_bytes: int, **payload) -> bytes:
        headers = {"Authorization": "Bearer " + self.slot.api_key} if self.slot.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(65, connect=5), trust_env=not is_loopback_url(self.slot.openai_base)) as client:
                async with client.stream("POST", self.slot.openai_base + path, headers=headers, **payload) as resp:
                    resp.raise_for_status()
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(65536):
                        if len(body) + len(chunk) > max_bytes:
                            raise BusinessException("语音服务返回的数据过大，请稍后重试", 50303)
                        body.extend(chunk)
                    return bytes(body)
        except httpx.TimeoutException:
            raise BusinessException("语音服务响应超时，请稍后重试", 50303) from None
        except httpx.HTTPError:
            raise BusinessException("语音服务暂不可用，请稍后重试或联系管理员检查绑定", 50303) from None

    async def synthesize(self, question: str) -> bytes:
        if not question.strip() or len(question) > MAX_QUESTION_CHARS:
            raise BusinessException("这道题的文本长度超出语音服务支持范围，可直接阅读题目", 42200)
        payload = {"model": self.slot.model, "input": question, "voice": "default", "response_format": "wav"}
        data = await self._request("/audio/speech", MAX_WAV_BYTES, json=payload)
        _validate_wav(data)
        return data

    async def transcribe(self, recording: bytes) -> str:
        _validate_wav(recording, recording=True)
        raw = await self._request("/audio/transcriptions", 65536, data={"model": self.slot.model, "language": "zh"},
                            files={"file": ("answer.wav", recording, "audio/wav")})
        try:
            text = json.loads(raw)["text"]
            if not isinstance(text, str) or len(text) > 8000:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise BusinessException("转写服务返回的数据无效，请重试", 50303) from None
        if not text.strip():
            raise BusinessException("录音中未识别到可用文字，可重录或手动输入", 42200)
        return text.strip()


class SpeechFactory:
    @staticmethod
    def get(db: Session, capability: Literal["speech", "transcription"] = "speech") -> SpeechService:
        config = resolve_ai_config(db)
        slot = config.speech_slot if capability == "speech" else config.transcription_slot
        if not slot.enabled or not slot.configured:
            raise BusinessException("语音能力尚未启用，请联系管理员检查供应商和模型绑定", 50301)
        return SpeechService(slot)
