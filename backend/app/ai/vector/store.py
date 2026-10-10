"""向量存储: 优先 Redis Search(KNN/HNSW/TAG 过滤), 无 Search 模块时自动降级为 MySQL 暴力余弦.

数据隔离(roadmap 三十/三十一章): 检索强制携带 student_id 过滤,
学生 A 的答辩检索绝不返回学生 B 的论文 Chunk(RAG 安全).
"""
import base64
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embedding.factory import EmbeddingFactory
from app.db.redis import RedisKeys, redis_client
from app.models.ai import AIDocumentChunk

logger = logging.getLogger("app.ai.vector")


def vec_to_bytes(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _parse_ftsearch_rows(rows: list) -> list[tuple[object, list]]:
    """兼容 FT.SEARCH 两种应答格式(不同 Redis 版本):
    扁平:  [total, key1, [f1, v1, ...], key2, [f2, v2, ...], ...]
    嵌套:  [total, [key1, [f1, v1, ...]], [key2, [f2, v2, ...]], ...]
    """
    entries = rows[1:] if len(rows) > 1 else []
    parsed: list[tuple[object, list]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        if isinstance(entry, (list, tuple)):
            parsed.append((entry[0] if len(entry) > 0 else None, list(entry[1]) if len(entry) > 1 else []))
            i += 1
        else:
            key = entry
            fields: list = []
            if i + 1 < len(entries) and isinstance(entries[i + 1], (list, tuple)):
                fields = list(entries[i + 1])
            parsed.append((key, fields))
            i += 2
    return parsed


def vec_to_b64(vec: list[float]) -> str:
    return base64.b64encode(vec_to_bytes(vec)).decode()


def b64_to_vec(data: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(data), dtype=np.float32)


class VectorStore:
    """统一向量库接口."""

    def __init__(self):
        self._mode: str | None = None

    @property
    def mode(self) -> str:
        if self._mode is None:
            self._mode = "redis" if self._try_create_index() else "mysql"
            logger.info("Vector store mode: %s", self._mode)
        return self._mode

    def _try_create_index(self) -> bool:
        try:
            redis_client.execute_command("FT.INFO", RedisKeys.RAG_INDEX)
            return True
        except Exception:
            pass
        try:
            dim = EmbeddingFactory.get().dim
            redis_client.execute_command(
                "FT.CREATE", RedisKeys.RAG_INDEX, "ON", "HASH", "PREFIX", "1", "rag:chunk:",
                "SCHEMA",
                "document_id", "TAG",
                "student_id", "TAG",
                "topic_id", "TAG",
                "document_type", "TAG",
                "scope", "TAG",
                "section", "TEXT",
                "content", "TEXT",
                "embedding", "VECTOR", "HNSW", "6", "TYPE", "FLOAT32", "DIM", dim, "DISTANCE_METRIC", "COSINE",
            )
            return True
        except Exception as exc:
            logger.warning("RediSearch 不可用, 降级 MySQL 向量检索: %s", exc)
            return False

    # ---------- 写入 ----------

    def upsert_chunk(self, chunk_id: int, metadata: dict, embedding: list[float]) -> None:
        if self.mode == "redis":
            key = RedisKeys.RAG_CHUNK.format(chunk_id=chunk_id)
            mapping = {
                "document_id": str(metadata.get("document_id") or 0),
                "student_id": str(metadata.get("student_id") or 0),
                "topic_id": str(metadata.get("topic_id") or 0),
                "document_type": metadata.get("document_type") or "",
                "scope": metadata.get("scope") or "STUDENT",
                "section": (metadata.get("section_title") or "")[:200],
                "content": metadata.get("content") or "",
                "embedding": vec_to_bytes(embedding),
            }
            redis_client.hset(key, mapping=mapping)
        # mysql 模式: embedding 直接写入 AIDocumentChunk.embedding 列(由调用方落库)

    def delete_chunks(self, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        if self.mode == "redis":
            keys = [RedisKeys.RAG_CHUNK.format(chunk_id=cid) for cid in chunk_ids]
            redis_client.delete(*keys)

    # ---------- 检索 ----------

    def search(self, query_embedding: list[float], student_id: int | None, document_types: list[str] | None,
               top_k: int = 8) -> list[tuple[int, float]]:
        """返回 [(chunk_id, 相似度分数)], 分数越大越相似."""
        if self.mode == "redis":
            return self._search_redis(query_embedding, student_id, document_types, top_k)
        return self._search_mysql(query_embedding, student_id, document_types, top_k)

    def _search_redis(self, query_embedding, student_id, document_types, top_k) -> list[tuple[int, float]]:
        # 全局知识库 chunk 的 student_id 标签存为 0
        sid = str(student_id) if student_id is not None else "0"
        filter_parts = [f"@student_id:{{{sid}|0}}"]
        if document_types:
            types = "|".join(t for t in document_types)
            filter_parts.append(f"@document_type:{{{types}}}")
        query = "(" + " ".join(filter_parts) + f")=>[KNN {top_k} @embedding $BLOB AS __score]"
        try:
            rows = redis_client.execute_command(
                "FT.SEARCH", RedisKeys.RAG_INDEX, query,
                "PARAMS", "2", "BLOB", vec_to_bytes(query_embedding),
                "SORTBY", "__score", "DIALECT", "2", "LIMIT", "0", str(top_k),
            )
        except Exception as exc:
            logger.warning("Redis KNN 失败, 降级 MySQL: %s", exc)
            return self._search_mysql(query_embedding, student_id, document_types, top_k)

        results = []
        for chunk_key, fields in _parse_ftsearch_rows(rows):
            if isinstance(chunk_key, bytes):
                chunk_key = chunk_key.decode()
            try:
                cid = int(chunk_key.rsplit(":", 1)[-1])
            except (ValueError, AttributeError):
                continue
            score = 1.0
            if fields:
                for i in range(0, len(fields) - 1, 2):
                    if fields[i] in (b"__score", "__score"):
                        try:
                            score = float(fields[i + 1])
                        except (TypeError, ValueError):
                            score = 1.0
            results.append((cid, 1.0 - score))  # cosine distance → similarity
        return results

    def _search_mysql(self, query_embedding, student_id, document_types, top_k) -> list[tuple[int, float]]:
        from app.db.mysql import SessionLocal

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        db: Session = SessionLocal()
        try:
            conds = [AIDocumentChunk.embedding.is_not(None),
                     AIDocumentChunk.student_id.in_([student_id, None]) if student_id is not None
                     else AIDocumentChunk.student_id.is_(None)]
            if document_types:
                conds.append(AIDocumentChunk.document_type.in_(document_types))
            rows = db.execute(select(AIDocumentChunk.id, AIDocumentChunk.embedding).where(*conds)).all()
            scored = []
            for cid, emb in rows:
                try:
                    vec = b64_to_vec(emb)
                except Exception:
                    continue
                v_norm = np.linalg.norm(vec)
                if v_norm == 0 or len(vec) != len(q):
                    continue
                similarity = float(np.dot(q, vec) / (q_norm * v_norm))
                scored.append((cid, similarity))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]
        finally:
            db.close()


vector_store = VectorStore()
