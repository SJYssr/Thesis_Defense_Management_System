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
                # D42: 无条件写 MySQL 降级副本 —— Redis 侧随时可能失效/换实例,
                # 有副本才能"失败即降级", 否则降级路径召回恒空。
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
        from app.shared.cycle import get_active_cycle

        # D45: 只取当前 cycle 的已通过报告; 历史实现不带 cycle 过滤, 跨学年两份 APPROVED
        # (旧数据 cycle_id 为 NULL)会触发 MultipleResultsFound, 接口直接 500。
        cycle = get_active_cycle(self.db, student_id)
        for doc_type, model, fields in mapping:
            stmt = select(model).where(model.student_id == student_id, model.status == "APPROVED")
            if cycle is not None:
                stmt = stmt.where(model.cycle_id == cycle.id)
            report = self.db.execute(stmt.order_by(model.id.desc()).limit(1)).scalars().first()
            if report is None:
                continue
            text = "\n\n".join(f"## {f}\n{getattr(report, f, '')}" for f in fields if getattr(report, f, None))
            if not text.strip():
                continue
            # D40(本模块侧防御): 论文重传会留下同类型多份 AIDocument, 这里只取 version 最新的一条,
            # 旧文档作废由 material 模块负责, 但检索侧不能因历史脏数据抛 MultipleResultsFound。
            existing = self.db.execute(
                select(AIDocument)
                .where(AIDocument.student_id == student_id, AIDocument.document_type == doc_type)
                .order_by(AIDocument.version.desc(), AIDocument.id.desc())
                .limit(1)
            ).scalars().first()
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

    # ---------- 回答与用量记录 ----------

    def _write_usage_log(self, *, user_id: int | None, scene: str, model: str, input_tokens: int,
                         output_tokens: int, latency_ms: int, status: str) -> None:
        """写 AI 用量记录(D47): 由调用方在 finally 中调用, 异常与客户端断连都不能丢计费.

        兼容说明: ai_usage_log 目前没有 status 列(models 层不在本模块边界内), 存在该列时
        以 SUCCESS/FAILED 落库; 不存在时把状态写进日志, 便于计费侧按日志对账。
        """
        try:
            log = AIUsageLog(user_id=user_id, scene=scene, model=model,
                             input_tokens=input_tokens, output_tokens=output_tokens,
                             latency_ms=latency_ms)
            if "status" in AIUsageLog.__mapper__.columns:
                log.status = status
            self.db.add(log)
            self.db.commit()
            logger.info("AI usage 记录: scene=%s model=%s in=%d out=%d %dms status=%s",
                        scene, model, input_tokens, output_tokens, latency_ms, status)
        except Exception:  # noqa: BLE001 — 计费落库失败不得影响已出的回答
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("AI 用量记录写入失败(scene=%s status=%s)", scene, status)

    def answer_stream(self, query: str, student_id: int | None, history: list[dict] | None = None,
                      user_id: int | None = None, scene: str = "QA") -> Generator[dict, None, None]:
        """RAG 增强回答, SSE 事件流: meta(引用) / delta(增量文本) / error / done."""
        from app.ai.prompts import resolve_system_prompt, untrusted_note

        started = datetime.now()
        system = ""
        user = ""
        chunks: list[RetrievedChunk] = []
        text_parts: list[str] = []
        full_text = ""
        model_name = "unknown"
        status = "FAILED"
        failure = ""
        try:
            chunks = self.retrieve(query, student_id, top_k=6, history=history)
            context = self.build_context(chunks)
            system = resolve_system_prompt(self.db, "RAG_ANSWER")
            user = (
                f"{untrusted_note()}\n【检索上下文】\n{context}\n\n"
                f"【学生问题】{query}\n请依据上下文回答; 上下文无法支持时明确说明。"
            )

            llm = LLMFactory.get()
            model_name = llm.name
            yield {"event": "meta", "data": {
                "provider": llm.name,
                "sources": [{"chunk_id": c.chunk_id, "document_type": c.document_type,
                             "section": c.section_title, "score": c.score} for c in chunks],
            }}
            for piece in llm.stream(system, user, history):
                text_parts.append(piece)
                yield {"event": "delta", "data": piece}
            status = "SUCCESS"
        except Exception as exc:  # noqa: BLE001 — D47: 任何异常都转成 error 事件, 不中断 SSE 契约
            status = "FAILED"
            failure = str(exc)[:200] or exc.__class__.__name__
            logger.exception("RAG 流式回答失败")
            yield {"event": "error", "data": {"message": "AI 回答生成失败, 请稍后重试"}}
        finally:
            # D47: 用量记录移入 finally —— 异常路径与客户端断连(GeneratorExit)同样留痕
            full_text = "".join(text_parts)
            self._write_usage_log(
                user_id=user_id, scene=scene, model=model_name,
                input_tokens=len(system) + len(user), output_tokens=len(full_text),
                latency_ms=int((datetime.now() - started).total_seconds() * 1000),
                status=status,
            )
        done_payload = {"length": len(full_text), "status": status}
        if failure:
            done_payload["message"] = failure
        yield {"event": "done", "data": done_payload}

