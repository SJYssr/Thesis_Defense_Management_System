"""真 BM25 关键词检索: jieba 分词 + rank_bm25 倒排打分.

替换 retriever 里旧的"拉 3000 行到 Python 算中文二元组重叠"实现, 三个理由:
1. 旧实现带 limit(3000), 召回被 id 前 3000 个 chunk 的窗口焊死, 大库直接漏检;
2. 旧打分是二元组重叠计数, 无 TF 饱和、无长度归一、无 IDF —— 常见二元组
   ("的实"/"实现") 与罕见专业词同权, 分数不可解释;
3. 中文按二元字符组切分会产生大量跨词噪声, jieba 搜索引擎模式按词切分才能
   让 "答辩流程" 真正命中 "答辩"; BM25 的 IDF 会自动压低常见词权重。

安全口径与向量侧完全一致(D43/D44): 建索引时套 student_scope_filter() 与
safe_document_types() 白名单, 学生 A 的索引里物理上不存在学生 B 的 chunk,
不存在 "召回越权 + 后置过滤" 的窗口期。

缓存: 索引按 (数据库绑定, student_id, document_types) 维度进程内 LRU; 每次检索
先用一条 COUNT/MAX 聚合探测指纹 (chunk 总数, max(id)), 指纹未变直接复用已建好的
倒排, 变了才重新取数建索引。指纹探测是单行聚合(走 id/student_id 索引), 比每次
重新 tokenize 全库便宜两个数量级。
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.vector.store import safe_document_types, student_scope_filter
from app.models.ai import AIDocumentChunk

# jieba 首次分词会往 stderr 打 "Building prefix dict ...", 库模块里统一压到 WARNING
jieba.setLogLevel(logging.WARNING)

logger = logging.getLogger("app.ai.rag.bm25")

_MAX_TOKEN_CHARS = 64
# rank_bm25 的小语料缺陷: 词普遍出现在多数文档时 IDF 为负(avg_idf<0 时它用
# eps*avg_idf 兜底, 兜出来还是负的), 整条查询的总分变负, 若按 "分数<=0 即未命中"
# 过滤, 词面明明命中的 chunk 会被全部丢掉(实测 3 篇语料查 "毕业设计状态机 阶段"
# 返回空, 直接违背 D43 的全局 chunk 可见性)。把非正 IDF 钳到正下限: 分数仍是
# BM25 量纲(TF 饱和 + 长度归一保留), 只保证"命中就不判负"。
_IDF_FLOOR = 1e-3
# 至少含一个中英文/数字字符才算有效词条, 纯标点与空白丢弃
_WORD_CHAR = re.compile(r"[0-9a-zA-Z\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """jieba 搜索引擎模式切词并归一(小写 + 去标点).

    用 lcut_for_search 而不是 lcut: 搜索引擎模式会把长词再切出子词
    ("毕业设计" -> "毕业"/"设计"/"毕业设计"), 查询侧与文档侧同时切, 中文短查询
    的召回明显好于精确模式。
    """
    if not text:
        return []
    tokens: list[str] = []
    for raw in jieba.lcut_for_search(str(text)):
        token = raw.strip().lower()
        if not token or len(token) > _MAX_TOKEN_CHARS:
            continue
        if not _WORD_CHAR.search(token):
            continue
        tokens.append(token)
    return tokens


def _with_positive_idf(bm25: BM25Okapi) -> BM25Okapi:
    """把非正 IDF 钳到 _IDF_FLOOR(见 _IDF_FLOOR 注释), 就地修改后返回同一对象."""
    for word, value in list(bm25.idf.items()):
        if value <= 0:
            bm25.idf[word] = _IDF_FLOOR
    return bm25


@dataclass
class _IndexEntry:
    """一份已建好的倒排: chunk_id 顺序与 BM25 语料行下标一一对应."""

    chunk_ids: list[int]
    bm25: BM25Okapi | None
    fingerprint: tuple[int, int]
    document_count: int


# 进程内 LRU: 8 个学生的 8 种文档类型组合已覆盖真实用量, 超出按最久未用淘汰
_CACHE: "OrderedDict[tuple, _IndexEntry]" = OrderedDict()
_CACHE_LOCK = threading.RLock()


def clear_cache() -> None:
    """清空进程内索引缓存(单测/索引重建后手工失效用)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def cache_size() -> int:
    with _CACHE_LOCK:
        return len(_CACHE)


def _bind_key(db: Session) -> tuple:
    """把会话绑定折算成缓存维度.

    id(bind) 用于隔离不同库(测试里每个用例一个内存库, url 都是 sqlite://);
    _IndexEntry 里同时持有 bind 的强引用, 避免对象被回收后 id 复用撞到别的库。
    """
    bind = db.get_bind()
    url = getattr(bind, "url", None)
    try:
        url_text = url.render_as_string(hide_password=False) if url is not None else repr(bind)
    except Exception:  # noqa: BLE001 — 任何 URL 序列化异常都不该挡住检索
        url_text = repr(bind)
    return (url_text, id(bind))


class BM25Index:
    """按 (student_id, document_types) 维度构建的 BM25 索引.

    student_id/document_types 在构造时确定(即索引维度), search 只吃查询与 top_k。
    """

    def __init__(self, db: Session, student_id: int | None = None,
                 document_types: list[str] | None = None):
        self.db = db
        self.student_id = student_id
        self.document_types = safe_document_types(document_types)
        # 可观测性: 建索引(全量取数)的次数, 单测据此断言缓存是否真的命中
        self.build_count = 0

    # ---------- 维度与过滤 ----------

    @property
    def key(self) -> tuple:
        sid = int(self.student_id) if self.student_id is not None else None
        return (_bind_key(self.db), sid, tuple(self.document_types))

    def _conds(self) -> list:
        conds = [student_scope_filter(self.student_id)]
        if self.document_types:
            conds.append(AIDocumentChunk.document_type.in_(self.document_types))
        return conds

    def _probe(self, conds: list) -> tuple[int, int]:
        """失效指纹: (chunk 总数, max(id)) —— 新增/删除/重建索引都会改变它."""
        row = self.db.execute(
            select(func.count(AIDocumentChunk.id), func.coalesce(func.max(AIDocumentChunk.id), 0)).where(*conds)
        ).one()
        return (int(row[0] or 0), int(row[1] or 0))

    def _load_rows(self, conds: list):
        """索引构建期唯一的一次全量取数(只取必要列, 单测 monkeypatch 计数)."""
        return self.db.execute(
            select(AIDocumentChunk.id, AIDocumentChunk.content)
            .where(*conds)
            .order_by(AIDocumentChunk.id)
        ).all()

    # ---------- 构建与缓存 ----------

    def _build(self, conds: list, fingerprint: tuple[int, int]) -> _IndexEntry:
        rows = self._load_rows(conds)
        self.build_count += 1
        chunk_ids: list[int] = []
        corpus: list[list[str]] = []
        total_tokens = 0
        for cid, content in rows:
            tokens = tokenize(content or "")
            total_tokens += len(tokens)
            chunk_ids.append(int(cid))
            corpus.append(tokens)
        # rank_bm25 在 avgdl=0(全空语料)时会除零, 空语料直接留 None
        bm25 = _with_positive_idf(BM25Okapi(corpus)) if corpus and total_tokens > 0 else None
        logger.info("BM25 索引重建: scope=%s types=%s chunks=%d tokens=%d",
                    self.student_id, self.document_types or "*", len(chunk_ids), total_tokens)
        return _IndexEntry(chunk_ids=chunk_ids, bm25=bm25, fingerprint=fingerprint,
                           document_count=len(chunk_ids))

    def _get_entry(self) -> _IndexEntry | None:
        from app.core.config import settings

        conds = self._conds()
        fingerprint = self._probe(conds)
        key = self.key
        limit = max(1, int(getattr(settings, "bm25_cache_size", 8) or 1))
        # 整段加锁: 构建含数据库 IO, 但只在指纹变化时发生; 用锁换取"同一时刻只有一次
        # 重建 + 缓存读写无竞态", 比双检锁更简单且不会出现两份索引互相覆盖。
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached.fingerprint == fingerprint:
                _CACHE.move_to_end(key)
                return cached
            entry = self._build(conds, fingerprint)
            _CACHE[key] = entry
            _CACHE.move_to_end(key)
            while len(_CACHE) > limit:
                _CACHE.popitem(last=False)
            return entry

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int | None = None) -> list[tuple[int, float]]:
        """返回 [(chunk_id, BM25 原始分)], 按分数降序; 未命中(分数<=0)不返回."""
        from app.core.config import settings

        if not getattr(settings, "bm25_enabled", True):
            return []
        text = (query or "").strip()
        if not text:
            return []
        limit = int(top_k if top_k is not None else getattr(settings, "bm25_top_k", 20))
        if limit <= 0:
            return []
        tokens = tokenize(text)
        if not tokens:
            return []
        entry = self._get_entry()
        if entry is None or entry.bm25 is None:
            return []
        scores = entry.bm25.get_scores(tokens)
        hits = [(cid, float(score)) for cid, score in zip(entry.chunk_ids, scores) if float(score) > 0.0]
        # 分数降序, 同分按 id 升序保证跨进程/跨批次可复现
        hits.sort(key=lambda item: (-item[1], item[0]))
        return hits[:limit]


def bm25_search(db: Session, query: str, student_id: int | None = None,
                document_types: list[str] | None = None, top_k: int | None = None) -> list[tuple[int, float]]:
    """一次性检索入口(便于不持有索引对象的调用点使用)."""
    return BM25Index(db, student_id, document_types).search(query, top_k)
