"""One CPU inference job. Audio and transcripts are confined to this process and its pipes."""
import io
import json
import re
import sys
import wave
from pathlib import Path

TTS_MODEL = "melo-tts-zh"
ASR_MODEL = "vosk-small-cn"
MAX_RECORDING_SECONDS = 90
MAX_INPUT_WAV = 16_000 * 2 * MAX_RECORDING_SECONDS + 4096


def synthesize(models: Path, question: str) -> bytes:
    import numpy as np
    import sherpa_onnx as so

    directory = models / "neural-model"
    vits = so.OfflineTtsVitsModelConfig(model=str(directory / "model.int8.onnx"),
                                        lexicon=str(directory / "lexicon.txt"), tokens=str(directory / "tokens.txt"))
    config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=vits, provider="cpu", num_threads=1),
                                 rule_fsts=",".join(str(directory / name) for name in ("date.fst", "number.fst")))
    engine = so.OfflineTts(config)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        # Limit peak synthesis memory on small hosts by processing one phrase at a time.
        phrases = re.findall(r"[^。！？!?；;，,\n]+[。！？!?；;，,]*", question)
        for phrase in phrases:
            for start in range(0, len(phrase), 64):
                part = phrase[start:start + 64].strip()
                if not part:
                    continue
                audio = engine.generate(part, sid=0, speed=1.0)
                pcm = np.asarray(audio.samples, dtype=np.float32)
                if not pcm.size:
                    continue
                stride = 2 if audio.sample_rate == 44100 else 1
                if not wav.getnframes():
                    wav.setframerate(audio.sample_rate // stride)
                samples = np.clip(pcm[::stride], -1, 1)
                wav.writeframesraw((samples * 32767).astype("<i2").tobytes())
        if not wav.getnframes():
            raise ValueError("no synthesized audio")
    return output.getvalue()


def transcribe(models: Path, payload: bytes) -> str:
    import vosk

    vosk.SetLogLevel(-1)
    with wave.open(io.BytesIO(payload)) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("expected mono PCM16 WAV at 16 kHz")
        if not 0 < wav.getnframes() <= 16000 * MAX_RECORDING_SECONDS:
            raise ValueError("invalid recording duration")
        recognizer = vosk.KaldiRecognizer(vosk.Model(str(models / "vosk-model-small-cn-0.22")), 16000)
        pieces = []
        while frames := wav.readframes(4000):
            if recognizer.AcceptWaveform(frames):
                pieces.append(json.loads(recognizer.Result())["text"])
        pieces.append(json.loads(recognizer.FinalResult())["text"])
    # The Chinese recognizer separates every word; preserve spaces between Latin words.
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", " ".join(p for p in pieces if p)).strip()


if __name__ == "__main__":
    models = Path(sys.argv[2])
    if sys.argv[1] == "speak":
        sys.stdout.buffer.write(synthesize(models, sys.stdin.buffer.read().decode("utf-8")))
    elif sys.argv[1] == "listen":
        result = transcribe(models, sys.stdin.buffer.read(MAX_INPUT_WAV + 1))
        sys.stdout.buffer.write(json.dumps({"text": result}, ensure_ascii=False).encode("utf-8"))
