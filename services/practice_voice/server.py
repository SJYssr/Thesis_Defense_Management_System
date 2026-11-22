"""Loopback OpenAI-compatible voice provider. Run separately from the grading API."""
import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from worker import ASR_MODEL, MAX_INPUT_WAV, TTS_MODEL


class SpeechRequest(BaseModel):
    model: Literal["melo-tts-zh"]
    input: str = Field(min_length=1, max_length=1200)
    response_format: Literal["wav"] = "wav"


def create_app(models: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    available = threading.BoundedSemaphore(1)

    def infer(task: str, payload: bytes) -> bytes:
        if not available.acquire(timeout=3):
            raise HTTPException(503, "语音服务繁忙，请稍后重试")
        try:
            # Workers exit after each request so the web service never retains two models in memory.
            proc = subprocess.run([sys.executable, str(Path(__file__).with_name("worker.py")), task, str(models)],
                                  input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=55, check=False,
                                  env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
            if proc.returncode or not proc.stdout:
                raise HTTPException(503, "语音生成或识别未完成")
            return proc.stdout
        except subprocess.TimeoutExpired:
            raise HTTPException(503, "语音服务超时") from None
        finally:
            available.release()

    @app.get("/v1/models")
    def list_models():
        found = []
        if (models / "neural-model/model.int8.onnx").is_file():
            found.append(TTS_MODEL)
        if (models / "vosk-model-small-cn-0.22/am/final.mdl").is_file():
            found.append(ASR_MODEL)
        return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "local"} for model in found]}

    @app.post("/v1/audio/speech")
    def speech(body: SpeechRequest):
        if not body.input.strip():
            raise HTTPException(422, "题目为空")
        audio = infer("speak", body.input.encode("utf-8"))
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/v1/audio/transcriptions")
    def transcription(file: Annotated[UploadFile, File()], model: Annotated[Literal["vosk-small-cn"], Form()]):
        payload = file.file.read(MAX_INPUT_WAV + 1)
        if len(payload) > MAX_INPUT_WAV:
            raise HTTPException(413, "录音过长")
        return json.loads(infer("listen", payload))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18285)
    args = parser.parse_args()
    uvicorn.run(create_app(args.models.resolve()), host="127.0.0.1", port=args.port, access_log=False)
