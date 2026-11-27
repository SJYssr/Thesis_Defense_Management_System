"""Loopback OpenAI-compatible voice provider. Run separately from the grading API."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from worker import ASR_MODEL, MAX_INPUT_WAV, TTS_MODEL


class SpeechRequest(BaseModel):
    model: Literal["melo-tts-zh"]
    input: str = Field(min_length=1, max_length=1200)
    response_format: Literal["wav"] = "wav"


def create_app(models: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    available = asyncio.Lock()

    async def infer(task: str, payload: bytes, request: Request) -> bytes:
        try:
            await asyncio.wait_for(available.acquire(), timeout=3)
        except TimeoutError:
            raise HTTPException(503, "语音服务繁忙，请稍后重试") from None
        try:
            if await request.is_disconnected():
                raise HTTPException(499)
            # A single disposable process bounds memory on small hosts. The business
            # API closes this connection when the browser stops waiting for a question.
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name("worker.py")), task, str(models),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            )
            output = asyncio.create_task(proc.communicate(payload))
            try:
                async with asyncio.timeout(55):
                    while not output.done():
                        if await request.is_disconnected():
                            raise HTTPException(499)
                        await asyncio.wait({output}, timeout=0.1)
                audio, _ = output.result()
                if proc.returncode or not audio:
                    raise HTTPException(503, "语音生成或识别未完成")
                return audio
            except TimeoutError:
                raise HTTPException(503, "语音服务超时") from None
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                # Drain the pipes and reap the job before giving its memory slot to ASR.
                await asyncio.gather(output, return_exceptions=True)
                await proc.wait()
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
    async def speech(body: SpeechRequest, request: Request):
        if not body.input.strip():
            raise HTTPException(422, "题目为空")
        audio = await infer("speak", body.input.encode("utf-8"), request)
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/v1/audio/transcriptions")
    async def transcription(request: Request, file: Annotated[UploadFile, File()], model: Annotated[Literal["vosk-small-cn"], Form()]):
        payload = await file.read(MAX_INPUT_WAV + 1)
        if len(payload) > MAX_INPUT_WAV:
            raise HTTPException(413, "录音过长")
        return json.loads(await infer("listen", payload, request))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18285)
    args = parser.parse_args()
    uvicorn.run(create_app(args.models.resolve()), host="127.0.0.1", port=args.port, access_log=False)
