"""Read bounded source excerpts directly from a ZIP. Members never touch the filesystem."""
import re
import stat
import struct
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from app.ai.document.loader import Section
from app.ai.rag.index_diagnostics import IndexIssue

# These are extraction budgets, independent of the shared upload policy.
_MAX_ARCHIVE = 32 << 20
_MAX_MEMBER = 128 << 10
_READ_BUDGET = 512 << 10
_MAX_FILES = 96
_MAX_EXCERPTS = 256
_SOURCE_EXTS = frozenset((".py .java .kt .js .jsx .ts .tsx .vue .go .rs .c .cc .cpp .h .hpp "
                          ".cs .sql .swift .dart .php .rb .m .r").split())
_SKIP_DIRS = frozenset("node_modules vendor vendors dist build target out bin obj coverage __pycache__ __macosx site-packages venv env generated third_party third-party secrets credentials migrations".split())
_SENSITIVE_NAME = re.compile(r"(?:^|[._-])(?:secret[s]?|credential[s]?|password[s]?|private[_-]?key|keystore|config|settings)(?:[._-]|$)", re.I)
_SENSITIVE_LINE = re.compile(r"""(?:password|passwd|api[_-]?key|secret|access[_-]?token|authorization)\s*["']?\s*[:=]\s*["'][^"']+|(?:sk-[a-zA-Z0-9_-]{12,}|gh[pousr]_[a-zA-Z0-9]{16,})|://[^/\s:]+:[^/\s@]+@""", re.I)


class SourceArchiveError(ValueError):
    """A student-facing explanation for an unsupported source archive."""


def _source_path(info: ZipInfo) -> str | None:
    raw = info.filename
    if ("\\" in raw or ":" in raw or len(raw) > 205 or any(ord(c) < 32 for c in raw)
            or any(p.startswith(".") or not p for p in raw.split("/"))):
        return None
    path = PurePosixPath(raw)
    if (path.suffix.lower() not in _SOURCE_EXTS or _SENSITIVE_NAME.search(path.name)
            or any(part.lower() in _SKIP_DIRS for part in path.parts)
            or path.name.lower().endswith((".min.js", ".d.ts", ".generated.ts"))
            or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
            or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
            or not 0 < info.file_size <= _MAX_MEMBER):
        return None
    return raw


def _sections(name: str, source: str) -> list[Section]:
    if "\x00" in source or "PRIVATE KEY-----" in source:
        return []
    result = []
    lines = source.splitlines()
    # Bound each span by both lines and characters and preserve the original line coordinates.
    start, buf, size = 1, [], 0
    for line_no, line in enumerate(lines, 1):
        text = "[已略去凭据]" if _SENSITIVE_LINE.search(line) else line
        if len(text) > 1000:  # generated/minified or encoded data
            text = "[已略去长行]"
        if buf and (len(buf) >= 24 or size + len(text) > 650):
            if any(s.strip() for s in buf):
                result.append(Section(f"{name} L{start}–{line_no - 1}", "\n".join(buf), metadata={"source_code": True}))
            start, buf, size = line_no, [], 0
        buf.append(text)
        size += len(text) + 1
    if buf and any(s.strip() for s in buf):
        result.append(Section(f"{name} L{start}–{len(lines)}", "\n".join(buf), metadata={"source_code": True}))
    return result


def parse_source_archive(path: str) -> list[Section]:
    if Path(path).suffix.lower() != ".zip":
        raise SourceArchiveError(IndexIssue.ARCHIVE_TYPE.value)
    # ZipFile builds an in-memory member list before returning. Bound the central
    # directory before opening, including archives containing millions of tiny entries.
    size = Path(path).stat().st_size
    if size > _MAX_ARCHIVE:
        raise SourceArchiveError(IndexIssue.ARCHIVE_SIZE.value)
    with open(path, "rb") as stream:
        stream.seek(max(0, size - 65557))
        trailer = stream.read()
    end = trailer.rfind(b"PK\x05\x06")
    if end < 0 or len(trailer) - end < 22:
        raise SourceArchiveError(IndexIssue.ARCHIVE_UNREADABLE.value)
    disk, cd_disk, count_disk, count, central_bytes, _, comment_len = struct.unpack_from("<4H2LH", trailer, end + 4)
    multipart = disk or cd_disk or count_disk != count
    zip64 = trailer[max(0, end - 20):end].startswith(b"PK\x06\x07")
    if (multipart or zip64 or count > 10000 or central_bytes > (2 << 20)
            or len(trailer) - end != 22 + comment_len):
        raise SourceArchiveError(IndexIssue.ARCHIVE_DIRECTORY.value)
    remaining = _READ_BUDGET
    sections = []
    used = 0
    try:
        with ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda i: i.filename):
                name = _source_path(info)
                if name is None or info.file_size > remaining:
                    continue
                with archive.open(info) as member:
                    data = member.read(info.file_size + 1)
                remaining -= len(data)
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    continue
                spans = _sections(name, text)
                if spans:
                    # Sample across a large file so later modules still have room.
                    available = min(16, _MAX_EXCERPTS - len(sections))
                    if len(spans) <= available:
                        sections.extend(spans)
                    else:
                        for i in range(available):
                            idx = i * (len(spans) - 1) // max(1, available - 1)
                            sections.append(spans[idx])
                    used += 1
                if used >= _MAX_FILES or remaining <= 0 or len(sections) >= _MAX_EXCERPTS:
                    break
    except (BadZipFile, OSError, NotImplementedError) as exc:
        raise SourceArchiveError(IndexIssue.ARCHIVE_UNREADABLE.value) from exc
    if not sections:
        raise SourceArchiveError(IndexIssue.ARCHIVE_CONTENT.value)
    return sections
