"""Hybrid RAG 检索: 向量 KNN + 关键词匹配 → RRF 融合(roadmap 三十六章).

多轮对话时先做 Query Rewrite 生成 standalone question(roadmap 三十八章).
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embedding.factory import EmbeddingFactory
from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.vector.store import vector_store
from app.models.ai import AIDocumentChunk


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_type: str
    section_title: str | None
    content: str
    score: float
    source: str = "vector"  # vector / keyword
    extra: dict = field(default_factory=dict)


def _ngrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(max(len(text) - 1, 0))}


class HybridRetriever:
    """学生私有库 + 全局知识库双路检索(roadmap 三十一章)."""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 查询重写 ----------

    def rewrite_query(self, query: str, history: list[dict] | None) -> str:
        if not history:
            return query
        recent = [{"role": h["role"], "content": h["content"][:300]} for h in history[-6:]]
        system = (
            "你是查询改写助手。根据多轮对话历史, 将用户的最新问题改写为不依赖上下文即可理解的独立检索问题。"
            '只输出 JSON: {"standalone_query": "改写后的问题"}'
        )
        user = f"【历史对话】{recent}\n【最新问题】{query}\n请输出改写后的独立问题。"
        try:
            data = extract_json(LLMFactory.get().complete(system, user))
            return data.get("standalone_query") or query
        except Exception:
            return query

    # ---------- 关键词检索(MySQL 词面匹配) ----------

    def keyword_search(self, query: str, student_id: int | None, document_types: list[str] | None, top_k: int = 10) -> list[tuple[int, float]]:
        grams = sorted(_ngrams(query))[:12]  # 排序保证跨进程可复现(set 顺序受哈希种子影响)
        if not grams:
            return []
        conds = [AIDocumentChunk.student_id.in_([student_id, None]) if student_id is not None
                 else AIDocumentChunk.student_id.is_(None)]
        if document_types:
            conds.append(AIDocumentChunk.document_type.in_(document_types))
        rows = self.db.execute(
            select(AIDocumentChunk.id, AIDocumentChunk.content).where(*conds).limit(3000)
        ).all()

        query_grams = set(grams)
        scored = []
        for cid, content in rows:
            content_grams = _ngrams(content[:3000])
            if not content_grams:
                continue
            overlap = len(query_grams & content_grams)
            if overlap >= 2:
                scored.append((cid, overlap / max(len(query_grams), 1)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ---------- 融合检索 ----------

    def retrieve(self, query: str, student_id: int | None, document_types: list[str] | None = None,
                 top_k: int = 6, rewrite: bool = False, history: list[dict] | None = None,
                 strategy: str = "hybrid") -> list[RetrievedChunk]:
        """strategy: hybrid(RRF 融合) / vector(仅向量) / keyword(仅关键词), 供评测实验对比(roadmap 九十二章)."""
        if strategy not in ("hybrid", "vector", "keyword"):
            raise ValueError(f"未知检索策略: {strategy}")
        search_query = self.rewrite_query(query, history) if rewrite else query

        embedding = EmbeddingFactory.get().embed_one(search_query)
        vector_results = vector_store.search(embedding, student_id, document_types, top_k=top_k * 2)
        keyword_results = self.keyword_search(search_query, student_id, document_types, top_k=top_k * 2)

        if strategy == "vector":
            ranked = [(cid, score) for cid, score in vector_results[:top_k]]
            sources = {cid: "vector" for cid, _ in ranked}
        elif strategy == "keyword":
            ranked = [(cid, score) for cid, score in keyword_results[:top_k]]
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
            ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        if not ranked:
            return []

        chunks = self.db.execute(
            select(AIDocumentChunk).where(AIDocumentChunk.id.in_([cid for cid, _ in ranked]))
        ).scalars().all()
        chunk_map = {c.id: c for c in chunks}

        results = []
        for cid, fused in ranked:
            c = chunk_map.get(cid)
            if c is None:
                continue
            results.append(RetrievedChunk(
                chunk_id=cid, document_id=c.document_id, document_type=c.document_type,
                section_title=c.section_title, content=c.content[:1200],
                score=round(fused, 6), source=sources.get(cid, "vector"),
            ))
        return results
