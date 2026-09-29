"""存储服务: 本地磁盘实现, 接口抽象便于以后切换 MinIO/OSS(roadmap 二十一)."""
import hashlib
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import FileException

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md", ".zip", ".rar", ".png", ".jpg", ".jpeg"}


class StorageService(ABC):
    @abstractmethod
    def save(self, file_bytes: bytes, original_name: str) -> tuple[str, str]:
        """返回 (stored_name, storage_path)."""


class LocalStorage(StorageService):
    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.upload_dir)
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
        p = (self.base_dir / storage_path).resolve()
        if not str(p).startswith(str(self.base_dir.resolve())):
            raise FileException("非法文件路径")
        return p


storage = LocalStorage()


def read_upload(file: UploadFile) -> tuple[bytes, str, str, int, str]:
    """读取并校验上传文件, 返回 (bytes, 原始名, mime, size, sha256)."""
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = file.file.read()
    if len(content) > max_bytes:
        raise FileException(f"文件超过大小限制 {settings.max_upload_size_mb}MB")
    original_name = file.filename or "unnamed"
    if Path(original_name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise FileException(f"不支持的文件类型: {Path(original_name).suffix}")
    sha256 = hashlib.sha256(content).hexdigest()
    return content, original_name, file.content_type or "application/octet-stream", len(content), sha256
