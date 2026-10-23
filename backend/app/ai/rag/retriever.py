"""Hybrid RAG 检索: 向量 KNN + BM25 关键词 → RRF 融合 → cross-encoder 精排(roadmap 三十六章).

多轮对话时先做 Query Rewrite 生成 standalone question(roadmap 三十八章).

三层结构:
1. 召回: 向量(EmbeddingFactory + vector_store) 与 BM25(jieba + 倒排) 各取
   settings.rerank_recall 条;
2. 融合: RRF(只用名次, 不需要两路分数可比) 得到候选池;
3. 精排: cross-encoder 对候选池逐条打分, 取 settings.rerank_keep 条进上下文;
   精排不可用时保持 RRF 顺序并标记 stage="fallback", 绝不打断问答。
"""
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embedding.factory import EmbeddingFactory
from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.prompts import resolve_system_prompt
from app.ai.rag.bm25 import BM25Index
from app.ai.rerank.factory import get_reranker
from app.ai.vector.store import safe_document_types, student_scope_filter, vector_store
from app.models.ai import AIDocumentChunk

logger = logging.getLogger("app.ai.rag")

# 降级原因只记一次(每进程/每种原因), 避免每个请求都刷一条 info
_LOGGED_FALLBACK: set[str] = set()


def _log_fallback_once(reason: str, message: str, *args) -> None:
    if reason in _LOGGED_FALLBACK:
        return
    _LOGGED_FALLBACK.add(reason)
    logger.info(message, *args)


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_type: str
    section_title: str | None
    content: str
    score: float                       # 该路原始分 / RRF 融合分(保持既有语义)
    source: str = "vector"             # vector / keyword / vector+keyword
    extra: dict = field(default_factory=dict)
    rerank_score: float | None = None  # cross-encoder 相关性分, 未精排为 None
    stage: str = "fused"               # fused(默认占位) / reranked / fallback


# 旧的 _ngrams() 已删除, 理由: 原 keyword_search 用 "select id, content ... limit(3000)"
# 把行拉到 Python 里算中文二元组重叠, 存在三个硬伤 ——
#   1) limit(3000) 把召回窗口焊死在 id 前 3000 个 chunk, 大库直接漏检;
#   2) 重叠计数不是 BM25: 无 TF 饱和、无长度归一、无 IDF, 常见二元组与专业词同权;
#   3) 二元字符组会产生跨词噪声("的实"/"实现"), 无法表达 "答辩" 这种整词匹配。
# 现由 app.ai.rag.bm25(jieba 分词 + rank_bm25 倒排) 承担同一条职责, 接口不变。


class HybridRetriever:
    """学生私有库 + 全局知识库双路检索(roadmap 三十一章)."""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 查询重写 ----------

    def rewrite_query(self, query: str, history: list[dict] | None) -> str:
        if not history:
            return query
        recent = [{"role": h["role"], "content": h["content"][:300]} for h in history[-6:]]
        system = resolve_system_prompt(self.db, "QUERY_REWRITE")
        user = f"【历史对话】{recent}\n【最新问题】{query}\n请输出改写后的独立问题。"
        try:
            data = extract_json(LLMFactory.get().complete(system, user))
            return data.get("standalone_query") or query
        except Exception:
            return query

    # ---------- 关键词检索(BM25 倒排) ----------

    def keyword_search(self, query: str, student_id: int | None, document_types: list[str] | None,
                       top_k: int = 10) -> list[tuple[int, float]]:
        """返回 [(chunk_id, BM25 分)], 分数降序.

        范围(student/全局 + 文档类型白名单)在索引构建时下推 SQL, 与向量侧同口径
        (D43/D44); settings.bm25_enabled=False 时返回空, 由调用方退化到单路召回。
        """
        if not query or not query.strip():
            return []
        index = BM25Index(self.db, student_id, document_types)
        hits = index.search(query, top_k)
        if not hits:
            _log_fallback_once("keyword-empty", "BM25 关键词召回为空(索引为空或词面不匹配), 本查询退化为向量单路")
        return hits

    # ---------- 融合检索 ----------

    def retrieve(self, query: str, student_id: int | None, document_types: list[str] | None = None,
                 top_k: int = 6, rewrite: bool = False, history: list[dict] | None = None,
                 strategy: str = "hybrid", rerank: bool = True) -> list[RetrievedChunk]:
        """strategy: hybrid(RRF 融合) / vector(仅向量) / keyword(仅关键词), 供评测实验对比(roadmap 九十二章).

        rerank=True(默认)时三条策略都要过精排 —— 只精排 hybrid 会让
        "rerank 前后对照"混入策略差异, 评测结果不可比。
        """
        from app.core.config import settings

        if strategy not in ("hybrid", "vector", "keyword"):
            raise ValueError(f"未知检索策略: {strategy}")
        search_query = self.rewrite_query(query, history) if rewrite else query

        recall = max(int(getattr(settings, "rerank_recall", 20) or 0), int(top_k))
        embedding = EmbeddingFactory.get().embed_one(search_query)
        vector_results = vector_store.search(embedding, student_id, document_types, top_k=recall)
        keyword_results = self.keyword_search(search_query, student_id, document_types, top_k=recall)

        if strategy == "vector":
            ranked = [(cid, score) for cid, score in vector_results[:recall]]
            sources = {cid: "vector" for cid, _ in ranked}
        elif strategy == "keyword":
            ranked = [(cid, score) for cid, score in keyword_results[:recall]]
            sources = {cid: "keyword" for cid, _ in ranked}
        else:
            # Reciprocal Rank Fusion
            rrf_scores: dict[int, float] = {}
            sources = {}
            k = 60
            for rank, (cid, _) in enumerate(vector_results):
                rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank + 1)
                sources[cid] = "vector"
            for rank, (cid, _) in enumerate(keyword_results):
                rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank + 1)
                sources[cid] = sources.get(cid, "vector+keyword") if sources.get(cid) else "keyword"
            ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:recall]

        if not ranked:
            return []

        # D44: 回表必须带同样的 student 过滤 —— 向量库索引残留(重新索引/删除失败/Redis 越权写入)
        # 不能成为跨学生泄露通道; 命中他人 chunk 时这里直接取不到行。
        chunk_map = {
            c.id: c for c in self.db.execute(
                select(AIDocumentChunk).where(
                    AIDocumentChunk.id.in_([cid for cid, _ in ranked]),
                    student_scope_filter(student_id),
                )
            ).scalars().all()
        }
        # 裁剪 ranked 到实际取到的行, 避免"有排名无内容"的空占位与后续计分偏差;
        # 精排必须在裁剪之后做 —— 拿出不回的 chunk 文本去送模型毫无意义。
        pool = [(cid, fused) for cid, fused in ranked if cid in chunk_map]
        if not pool:
            return []

        reranked = self._apply_rerank(search_query, pool, chunk_map, top_k) if rerank else None
        if reranked is None:
            keep = int(getattr(settings, "rerank_keep", 6) or 0)
            limit = top_k if keep <= 0 else min(keep, top_k)
            rows, stage, rerank_scores = pool[:max(limit, 1)], "fallback", {}
            if rerank:
                _log_fallback_once(
                    "rerank-unavailable",
                    "rerank 不可用(缺模型/缺依赖/显式关闭), 本进程检索按 RRF 顺序降级, 最终保留 %d 条",
                    limit,
                )
        else:
            rows, stage, rerank_scores = reranked, "reranked", dict(reranked)

        results = []
        for cid, fused in rows:
            c = chunk_map.get(cid)
            if c is None:
                continue
            results.append(RetrievedChunk(
                chunk_id=cid, document_id=c.document_id, document_type=c.document_type,
                section_title=c.section_title, content=c.content[:1200],
                score=round(fused, 6), source=sources.get(cid, "vector"),
                rerank_score=(None if cid not in rerank_scores else round(rerank_scores[cid], 6)),
                stage=stage,
            ))
        return results

    # ---------- 精排 ----------

    def _apply_rerank(self, query: str, pool: list[tuple[int, float]], chunk_map: dict,
                      top_k: int) -> list[tuple[int, float]] | None:
        """对候选池做 cross-encoder 精排, 返回按精排分降序的 [(chunk_id, rrf 分)].

        返回 None 表示"没有精排可用", 调用方按 fallback 走 —— 精排失败绝不上抛。
        """
        from app.core.config import settings

        reranker = get_reranker()
        if not getattr(reranker, "available", False):
            return None
        keep = int(getattr(settings, "rerank_keep", 6) or 0)
        limit = top_k if keep <= 0 else min(keep, top_k)
        limit = max(limit, 1)
        texts = [(chunk_map[cid].content or "")[:4000] for cid, _ in pool]
        try:
            scored = reranker.rerank(query, texts, top_k=limit)
        except Exception as exc:  # noqa: BLE001 — 精排层任何异常都只降级, 不打断问答
            _log_fallback_once("rerank-error", "rerank 调用异常, 本次检索按 RRF 顺序降级: %s", exc)
            return None
        if not scored:
            _log_fallback_once("rerank-empty", "rerank 返回空结果, 本次检索按 RRF 顺序降级")
            return None
        out = []
        for idx, score in scored:
            if 0 <= idx < len(pool):
                out.append((pool[idx][0], float(score)))
        return out or None
