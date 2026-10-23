"""存储服务: 本地磁盘实现, 接口抽象便于以后切换 MinIO/OSS(roadmap 二十一).

D16/D17/D18:
- base_dir 在 __init__ 中 resolve, 与进程 CWD 解耦;
- 路径校验改用 Path.relative_to + 捕获 ValueError, 不再用 startswith 前缀比较;
- 上传读取改为 1MiB 分块累积, 超限立即中断(不把超大文件整体读进内存).
"""
import hashlib
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import FileException

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md", ".zip", ".rar", ".png", ".jpg", ".jpeg"}

# D17: 分块大小 1MiB
READ_CHUNK_SIZE = 1024 * 1024


class StorageService(ABC):
    @abstractmethod
    def save(self, file_bytes: bytes, original_name: str) -> tuple[str, str]:
        """返回 (stored_name, storage_path)."""


class LocalStorage(StorageService):
    def __init__(self, base_dir: str | Path | None = None):
        # D18: 一律 resolve, 保证 base_dir 为绝对规范路径
        self.base_dir = Path(base_dir or settings.upload_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file_bytes: bytes, original_name: str) -> tuple[str, str]:
        ext = Path(original_name).suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise FileException(f"不支持的文件类型: {ext}")
        stored_name = f"{uuid.uuid4().hex}{ext}"
        now = datetime.now()
        rel_dir = f"{now.year}/{now.month:02d}"
        dir_path = self.base_dir / rel_dir
        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / stored_name).write_bytes(file_bytes)
        return stored_name, f"{rel_dir}/{stored_name}"

    def abs_path(self, storage_path: str) -> Path:
        """D16: 用 Path.relative_to 判定越界(捕获 ValueError), 阻止 ../ 与符号链接逃逸."""
        target = (self.base_dir / str(storage_path or "")).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError as exc:
            raise FileException("非法文件路径") from exc
        return target


storage = LocalStorage()


def read_upload(file: UploadFile, max_upload_mb: int | None = None) -> tuple[bytes, str, str, int, str]:
    """读取并校验上传文件, 返回 (bytes, 原始名, mime, size, sha256). 大小上限可由系统配置传入.

    D17: 1MiB 分块累积, 累计超限立刻中断并抛 FileException, 避免超大文件把内存撑爆.
    """
    limit_mb = max_upload_mb if max_upload_mb and max_upload_mb > 0 else settings.max_upload_size_mb
    max_bytes = limit_mb * 1024 * 1024

    original_name = file.filename or "unnamed"
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FileException(f"不支持的文件类型: {ext}")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FileException(f"文件超过大小限制 {limit_mb}MB")
        chunks.append(chunk)

    content = b"".join(chunks)
    sha256 = hashlib.sha256(content).hexdigest()
    return content, original_name, file.content_type or "application/octet-stream", len(content), sha256
