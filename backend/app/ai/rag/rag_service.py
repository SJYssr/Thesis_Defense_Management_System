"""RAGService: 统一入口 index / retrieve / answer(roadmap 三十七/九十四章).

文档入库流程(roadmap 三十二章):
文件解析 → 文本清洗 → 章节识别 → Chunk → Embedding → Redis Vector / MySQL → 元数据落库.
"""
import logging
from datetime import datetime
from typing import Generator

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.document.loader import parse_document
from app.ai.document.splitter import split_sections
from app.ai.embedding.factory import EmbeddingFactory
from app.ai.llm.factory import LLMFactory
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
from app.ai.vector.store import vector_store, vec_to_b64
from app.models.ai import AIDocument, AIDocumentChunk, AIUsageLog
from app.shared.storage import storage

logger = logging.getLogger("app.ai.rag")

from app.ai.prompts import DOC_TYPE_CN  # noqa: E402 — 供本模块与外部继续从 rag_service 引用


class RAGService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 索引 ----------

    def index_document(self, document_id: int) -> dict:
        """同步索引一个 AI 文档(生产环境由 Celery Worker 调用)."""
        doc = self.db.get(AIDocument, document_id)
        if doc is None:
            raise ValueError("文档不存在")
        doc.index_status = "PROCESSING"
        self.db.commit()

        try:
            # 1. 解析
            text = None
            if doc.file_id:
                from app.models.system import SysFile

                f = self.db.get(SysFile, doc.file_id)
                if f is None:
                    raise ValueError("源文件不存在")
                path = str(storage.abs_path(f.storage_path))
                sections = parse_document(path)
            elif doc.content_text:
                import re as _re

                from app.ai.document.loader import Section, clean_text, split_by_headings

                raw_text = _re.sub(r"^#{1,6}\s*", "", doc.content_text, flags=_re.MULTILINE)
                sections = [Section(title=t, content=c) for t, c in split_by_headings(clean_text(raw_text))]
            else:
                raise ValueError("文档既无文件也无文本内容")

            doc.content_text = "\n\n".join(f"# {s.title}\n{s.content}" for s in sections)[:200000]

            # 2. 分块
            chunks = split_sections(sections)
            if not chunks:
                raise ValueError("未能从文档中提取有效内容")

            # 3. 清理旧 chunk(重新索引场景)
            old_ids = self.db.execute(
                select(AIDocumentChunk.id).where(AIDocumentChunk.document_id == document_id)
            ).scalars().all()
            if old_ids:
                vector_store.delete_chunks(list(old_ids))
                self.db.execute(delete(AIDocumentChunk).where(AIDocumentChunk.document_id == document_id))

            # 4. Embedding(批量)
            embeddings = EmbeddingFactory.get().embed([c["content"] for c in chunks])

            # 5. 落库 + 写向量
            for chunk, emb in zip(chunks, embeddings):
                row = AIDocumentChunk(
                    document_id=document_id, student_id=doc.student_id, topic_id=doc.topic_id,
                    scope=doc.scope, document_type=doc.document_type,
                    chunk_index=chunk["chunk_index"], section_title=chunk["section_title"],
                    content=chunk["content"], token_count=chunk["token_count"],
                )
                if vector_store.mode == "mysql":
                    row.embedding = vec_to_b64(emb)
                self.db.add(row)
                self.db.flush()
                vector_store.upsert_chunk(row.id, {
                    "document_id": doc.id, "student_id": doc.student_id, "topic_id": doc.topic_id,
                    "document_type": doc.document_type, "scope": doc.scope,
                    "section_title": chunk["section_title"], "content": chunk["content"],
                }, emb)

            doc.chunk_count = len(chunks)
            doc.index_status = "SUCCESS"
            doc.indexed_at = datetime.now()
            doc.error_message = None
            self.db.commit()
            logger.info("文档 %s 索引完成: %d chunks (mode=%s)", document_id, len(chunks), vector_store.mode)
            return {"document_id": document_id, "chunk_count": len(chunks), "status": "SUCCESS"}
        except Exception as exc:
            self.db.rollback()
            doc = self.db.get(AIDocument, document_id)
            if doc:
                doc.index_status = "FAILED"
                doc.error_message = str(exc)[:480]
                self.db.commit()
            logger.exception("文档 %s 索引失败", document_id)
            raise

    def index_stage_reports(self, student_id: int):
        """将学生的开题/中期/终期文本报告纳入私有知识库(无文件的文本型文档)."""
        from app.models.business import FinalDefense, GraduationTopic, MidtermReport, OpeningReport
        from app.models.enums import AIDocumentType

        mapping = [
            (AIDocumentType.OPENING_REPORT.value, OpeningReport, ["title", "research_background", "research_content", "technical_route"]),
            (AIDocumentType.MIDTERM_REPORT.value, MidtermReport, ["progress_description", "completed_content", "next_plan"]),
            (AIDocumentType.FINAL_REPORT.value, FinalDefense, ["summary", "innovation", "technical_solution", "test_result"]),
        ]
        for doc_type, model, fields in mapping:
            report = self.db.execute(select(model).where(model.student_id == student_id, model.status == "APPROVED")).scalar_one_or_none()
            if report is None:
                continue
            text = "\n\n".join(f"## {f}\n{getattr(report, f, '')}" for f in fields if getattr(report, f, None))
            if not text.strip():
                continue
            existing = self.db.execute(
                select(AIDocument).where(AIDocument.student_id == student_id, AIDocument.document_type == doc_type)
            ).scalar_one_or_none()
            if existing and existing.content_text == text:
                continue
            if existing is None:
                doc = AIDocument(student_id=student_id, document_type=doc_type,
                                 document_name=DOC_TYPE_CN.get(doc_type, doc_type), scope="STUDENT",
                                 index_status="WAITING")
                self.db.add(doc)
            else:
                doc = existing
                doc.version += 1
                doc.index_status = "WAITING"
            doc.content_text = text
            self.db.commit()
            self.index_document(doc.id)

    # ---------- 检索 ----------

    def retrieve(self, query: str, student_id: int | None, document_types: list[str] | None = None,
                 top_k: int = 6, history: list[dict] | None = None) -> list[RetrievedChunk]:
        retriever = HybridRetriever(self.db)
        return retriever.retrieve(query, student_id, document_types, top_k=top_k,
                                  rewrite=bool(history), history=history)

    def build_context(self, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return "(未检索到相关材料)"
        parts = []
        for i, c in enumerate(chunks, 1):
            label = f"{DOC_TYPE_CN.get(c.document_type, c.document_type)}·{c.section_title or '正文'}"
            parts.append(f"[{i}] {label}\n{c.content}")
        return "\n\n".join(parts)

    # ---------- 回答 ----------

    def answer_stream(self, query: str, student_id: int | None, history: list[dict] | None = None,
                      user_id: int | None = None, scene: str = "QA") -> Generator[dict, None, None]:
        """RAG 增强回答, SSE 事件流: meta(引用) / delta(增量文本) / done."""
        started = datetime.now()
        chunks = self.retrieve(query, student_id, top_k=6, history=history)
        context = self.build_context(chunks)

        from app.ai.prompts import resolve_system_prompt, untrusted_note

        system = resolve_system_prompt(self.db, "RAG_ANSWER")
        user = (
            f"{untrusted_note()}\n【检索上下文】\n{context}\n\n"
            f"【学生问题】{query}\n请依据上下文回答; 上下文无法支持时明确说明。"
        )

        llm = LLMFactory.get()
        yield {"event": "meta", "data": {
            "provider": llm.name,
            "sources": [{"chunk_id": c.chunk_id, "document_type": c.document_type,
                         "section": c.section_title, "score": c.score} for c in chunks],
        }}
        text_parts = []
        for piece in llm.stream(system, user, history):
            text_parts.append(piece)
            yield {"event": "delta", "data": piece}

        # 用量记录(Mock 时 token 以字符近似)
        full = "".join(text_parts)
        try:
            self.db.add(AIUsageLog(user_id=user_id, scene=scene, model=llm.name,
                                   input_tokens=len(system) + len(user), output_tokens=len(full),
                                   latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
            self.db.commit()
        except Exception:
            self.db.rollback()
        yield {"event": "done", "data": {"length": len(full)}}
