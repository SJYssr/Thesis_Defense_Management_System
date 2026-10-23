"""D16/D17/D18: 存储路径校验(Path.relative_to)、分块读取超限中断、base_dir 绝对化."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from starlette.datastructures import Headers, UploadFile

from app.core.exceptions import FileException
from app.shared.storage import READ_CHUNK_SIZE, LocalStorage, read_upload


def _upload(name: str, size: int) -> UploadFile:
    spool = tempfile.SpooledTemporaryFile(max_size=READ_CHUNK_SIZE * 2, mode="w+b")
    spool.write(b"x" * size)
    spool.seek(0)
    return UploadFile(file=spool, filename=name, headers=Headers({"content-type": "application/pdf"}))


class PathGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "uploads"
        self.storage = LocalStorage(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_base_dir_is_resolved_absolute(self):
        # D18: 传入相对路径也要 resolve 成绝对路径
        relative = LocalStorage("relative_uploads_dir")
        try:
            self.assertTrue(relative.base_dir.is_absolute())
            self.assertEqual(relative.base_dir, (Path.cwd() / "relative_uploads_dir").resolve())
        finally:
            relative.base_dir.rmdir()

    def test_normal_relative_path_allowed(self):
        target = self.storage.abs_path("2026/01/abc.pdf")
        self.assertEqual(target, (self.base / "2026/01/abc.pdf").resolve())

    def test_traversal_rejected(self):
        # D16: ../ 逃逸
        with self.assertRaises(FileException) as ctx:
            self.storage.abs_path("../../etc/passwd")
        self.assertIn("非法文件路径", ctx.exception.message)

    def test_sibling_prefix_escape_rejected(self):
        # D16: 旧实现用 startswith 前缀比较, ../uploads_evil 会被误判为合法
        with self.assertRaises(FileException):
            self.storage.abs_path("../uploads_evil/stolen.pdf")

    def test_absolute_path_outside_base_rejected(self):
        with self.assertRaises(FileException):
            self.storage.abs_path(str(self.root / "other" / "x.pdf"))

    def test_empty_path_is_base_dir(self):
        # macOS 的 tmp 目录是 /var -> /private/var 符号链接, 统一用 resolve 后的 base_dir 比较
        self.assertEqual(self.storage.abs_path(""), self.storage.base_dir)


class ChunkedReadTest(unittest.TestCase):
    def test_small_file_returns_hash(self):
        payload = b"pdf-bytes" * 10
        upload = _upload("paper.pdf", 0)
        self.addCleanup(upload.file.close)
        upload.file.seek(0)
        upload.file.write(payload)
        upload.file.seek(0)
        content, name, mime, size, sha256 = read_upload(upload, max_upload_mb=1)
        self.assertEqual(content, payload)
        self.assertEqual(name, "paper.pdf")
        self.assertEqual(mime, "application/pdf")
        self.assertEqual(size, len(payload))
        self.assertEqual(sha256, hashlib.sha256(payload).hexdigest())

    def test_oversize_aborts_before_full_read(self):
        # D17: 3MiB 文件 + 1MiB 上限 => 读到第 2 块即中断, 不把整个文件读进内存
        total_size = 3 * 1024 * 1024
        upload = _upload("big.pdf", total_size)
        self.addCleanup(upload.file.close)
        with self.assertRaises(FileException) as ctx:
            read_upload(upload, max_upload_mb=1)
        self.assertIn("文件超过大小限制 1MB", ctx.exception.message)
        consumed = upload.file.tell()
        self.assertLessEqual(consumed, 2 * READ_CHUNK_SIZE)
        self.assertLess(consumed, total_size)

    def test_illegal_extension_rejected(self):
        upload = _upload("shell.php", 10)
        self.addCleanup(upload.file.close)
        with self.assertRaises(FileException) as ctx:
            read_upload(upload, max_upload_mb=1)
        self.assertIn("不支持的文件类型", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
