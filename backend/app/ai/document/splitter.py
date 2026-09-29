"""章节感知分块: 按章节→小节→递归字符切分(roadmap 三十五章).

参数: chunk_size 500~900 中文字符, overlap 80~150.
"""
import re

from app.ai.document.loader import Section

DEFAULT_CHUNK_SIZE = 700
DEFAULT_OVERLAP = 120
MIN_CHUNK_SIZE = 50


def split_section_content(content: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """单个超长小节递归二分 + 按句子边界切分."""
    content = content.strip()
    if len(content) <= chunk_size:
        return [content] if content else []

    # 优先按句号/问号/换行等句子边界找切点
    pieces: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            window = content[start:end]
            cut = max(window.rfind("。"), window.rfind("；"), window.rfind("\n"), window.rfind("，"))
            if cut > MIN_CHUNK_SIZE:
                end = start + cut + 1
        pieces.append(content[start:end])
        if end >= len(content):
            break
        start = max(end - overlap, start + 1)
    return pieces


def split_sections(sections: list[Section], chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """返回 [{section_title, content, page}], 每个 chunk 自带章节上下文前缀."""
    chunks: list[dict] = []
    for sec in sections:
        parts = split_section_content(sec.content, chunk_size, overlap)
        for i, part in enumerate(parts):
            prefix = f"[{sec.title}] " if sec.title and sec.title != "正文" else ""
            text = f"{prefix}{part}"
            if len(text.strip()) < MIN_CHUNK_SIZE:
                continue
            chunks.append({
                "section_title": sec.title,
                "content": text.strip(),
                "page": sec.page,
                "chunk_index": len(chunks),
                "token_count": len(re.sub(r"\s", "", text)),
            })
    return chunks
