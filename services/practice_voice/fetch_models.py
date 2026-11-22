"""Download and verify the Mandarin speech models for the standalone CPU service."""
import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MELO_REVISION = "a0d5c6a264c0ef92d70d8661d8cc502d79627cd6"
MELO = f"https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/resolve/{MELO_REVISION}"
FILES = {
    "model.int8.onnx": "f085f5079e05f039b800aeb542f5253c26a303211b0c6465d0d9387977855a63",
    "lexicon.txt": "7236884b02435ac5d10cf69b4be40a61b45aa676b5300f0e412f185748fee528",
    "tokens.txt": "d18664a7e12bd7ea1022ddaf951e534e136815016c5a809d6b64156bffb4369d",
    "date.fst": "eb8aa079ae3cb81d8f4404992f39d61a0cb990947512b5b8d1e54d1f6980e718",
    "number.fst": "743f402181fcfebf76cc2f0546b71fa26476e626fbe4e460fb7b4c3a7a8bd5bd",
    "LICENSE": "88a50e5a02bbc2a5c2f084dc19da751aa97b1690f5fda76cd8005c8634d1ca70",
    "README.md": "ae6e44dead53dfdbd5b4a77106226f3118eb910222b46cb5533a3860773fc6c7",
}
VOSK = "vosk-model-small-cn-0.22"
VOSK_SHA256 = "3af8b0e7e0f835ae9d414ce5df580237a3cfb08d586c9fbbb0f7ff29ad5b14ba"


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def fetch(url: str, path: Path, expected: str) -> None:
    if path.is_file() and digest(path) == expected:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as file:
            shutil.copyfileobj(response, file)
        if digest(temporary) != expected:
            raise ValueError(f"checksum mismatch: {path.name}")
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(directory: Path) -> None:
    target = directory / "neural-model"
    target.mkdir(parents=True, exist_ok=True)
    for name, checksum in FILES.items():
        fetch(f"{MELO}/{name}", target / name, checksum)
    marker = directory / VOSK / ".source-sha256"
    if marker.is_file() and marker.read_text().strip() == VOSK_SHA256:
        return
    with tempfile.TemporaryDirectory(dir=directory) as staging:
        archive = Path(staging) / (VOSK + ".zip")
        fetch(f"https://alphacephei.com/vosk/models/{VOSK}.zip", archive, VOSK_SHA256)
        # Only the exact hash-verified upstream archive is extracted.
        with zipfile.ZipFile(archive) as model:
            model.extractall(staging)
        (Path(staging) / VOSK / marker.name).write_text(VOSK_SHA256)
        dest = directory / VOSK
        if dest.exists():
            raise FileExistsError(f"{dest} already exists without the expected source marker; choose a clean model directory")
        (Path(staging) / VOSK).rename(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    install(parser.parse_args().directory)
