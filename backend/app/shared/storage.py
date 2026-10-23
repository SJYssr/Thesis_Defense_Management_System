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

# 上传白名单按用途分组: 后端校验与前端 accept 展示共用同一份(GET /files/upload-policy),
# 避免出现"前端 accept 里有 .mp4, 后端白名单没有"这类上传必失败的错配。
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".rar", ".7z", ".tar", ".gz"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
MEDIA_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".wmv", ".flv", ".m4v", ".mp3", ".wav", ".aac"})

# 故意不含: svg(可内联执行脚本) / html / js / exe / sh / bat 等可执行或可渲染类型
ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS | ARCHIVE_EXTENSIONS | IMAGE_EXTENSIONS | MEDIA_EXTENSIONS


def is_allowed_extension(name: str) -> bool:
    """按扩展名判定是否可上传(大小写不敏感, 无扩展名一律拒绝)."""
    return pathlib_suffix(name) in ALLOWED_EXTENSIONS


def pathlib_suffix(name: str) -> str:
    return Path(str(name or "")).suffix.lower()


def allowed_extensions_text() -> str:
    return " ".join(sorted(ALLOWED_EXTENSIONS))


def reject_unsupported(name: str) -> None:
    """扩展名不在白名单时抛 FileException, 提示里带上允许的类型."""
    ext = pathlib_suffix(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise FileException(f"不支持的文件类型: {ext or '(无扩展名)'}, 允许: {allowed_extensions_text()}")


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
        reject_unsupported(original_name)
        ext = pathlib_suffix(original_name)
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
    reject_unsupported(original_name)

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
