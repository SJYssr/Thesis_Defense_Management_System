"""RAGService: 统一入口 index / retrieve / answer(roadmap 三十七/九十四章).

文档入库流程(roadmap 三十二章):
文件解析 → 文本清洗 → 章节识别 → Chunk → Embedding → Redis Vector / MySQL → 元数据落库.
"""
import logging
from collections.abc import Generator
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.document.loader import Section, clean_text, parse_document, split_by_headings
from app.ai.document.source_archive import SourceArchiveError, parse_source_archive
from app.ai.document.splitter import split_sections
from app.ai.embedding.factory import EmbeddingFactory
from app.ai.llm.factory import AIServiceUnavailable, LLMFactory
from app.ai.rag.index_diagnostics import IndexIssue, public_index_error
from app.ai.rag.practice_sources import REPORTS, material_files, practice_documents, report_text
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
from app.ai.vector.store import vec_to_b64, vector_store
from app.core.exceptions import AIServiceException, BusinessException, FileException
from app.models.ai import AIDocument, AIDocumentChunk, AIUsageLog
from app.models.system import SysFile
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

        # Keep this outer lock across the savepoint rollback and the failure status write.
        doc = self.db.get(AIDocument, document_id, with_for_update=True, populate_existing=True)
        if doc is None:
            raise FileException("文档已移除")
        try:
            with self.db.begin_nested():
                old_ids, writes = self._replace_chunks(doc)
                doc.index_status = "SUCCESS"
                doc.indexed_at = datetime.now()
                doc.chunk_count = len(writes)
                doc.error_message = None
        except Exception as exc:
            if isinstance(exc, (FileException, AIServiceUnavailable)):
                error = exc
            else:
                error = AIServiceException(IndexIssue.UNKNOWN.value)
            doc.index_status = "FAILED"
            doc.error_message = error.message
            self.db.commit()
            logger.warning("文档 %s 索引失败 (%s)", document_id, type(exc).__name__)
            raise error from None
        self.db.commit()

        # MySQL is now authoritative. Until its commit the old Redis keys remain usable.
        try:
            self._sync_vector_cache(document_id, old_ids, writes)
        except Exception as exc:
            self.db.rollback()
            logger.warning("文档 %s 向量缓存同步失败 (%s)", document_id, type(exc).__name__)
        try:
            from app.ai.health import mark_embedding_indexed
            mark_embedding_indexed(self.db)
        except Exception:
            logger.warning("embedding 指纹标记失败", exc_info=True)
        return {"document_id": document_id, "chunk_count": len(writes), "status": "SUCCESS"}

    def _read_sections(self, doc: AIDocument) -> list[Section]:
        if doc.file_id is None:
            return [Section(title=t, content=c) for t, c in split_by_headings(clean_text(doc.content_text or ""))]
        file = self.db.get(SysFile, doc.file_id)
        if file is None or file.deleted_at is not None:
            raise FileException(IndexIssue.FILE_MISSING.value)
        try:
            path = str(storage.abs_path(file.storage_path))
            sections = parse_source_archive(path) if doc.document_type == "SOURCE_CODE" else parse_document(path)
        except SourceArchiveError as exc:
            raise FileException(str(exc)) from None
        except Exception:
            raise FileException(IndexIssue.FILE_UNREADABLE.value) from None
        preview = "\n\n".join(f"# {section.title}\n{section.content}" for section in sections)
        doc.content_text = preview.encode("utf-8")[:65000].decode("utf-8", errors="ignore")
        return sections

    def _replace_chunks(self, doc: AIDocument):
        chunks = split_sections(self._read_sections(doc))
        if not chunks:
            raise FileException(IndexIssue.NO_TEXT.value)
        try:
            embedder = EmbeddingFactory.get()
            embeddings = []
            for start in range(0, len(chunks), 32):
                batch = chunks[start:start + 32]
                vectors = embedder.embed([chunk["content"] for chunk in batch])
                dimension = len(embeddings[0]) if embeddings else embedder.dim
                if not dimension or len(vectors) != len(batch) or any(len(v) != dimension for v in vectors):
                    raise ValueError("unexpected embedding shape")
                embeddings.extend(vectors)
        except Exception as exc:
            if isinstance(exc, BusinessException) and exc.code == 50303:
                # The SDK adapter has already translated this into a provider-independent message.
                raise AIServiceUnavailable(public_index_error(exc.message), exc.code) from None
            raise AIServiceUnavailable(IndexIssue.EMBEDDING.value) from None

        old_ids = list(self.db.scalars(select(AIDocumentChunk.id).where(AIDocumentChunk.document_id == doc.id)))
        writes = []
        for chunk, emb in zip(chunks, embeddings):
            metadata = {
                "document_id": doc.id, "student_id": doc.student_id, "topic_id": doc.topic_id,
                "scope": doc.scope, "document_type": doc.document_type,
                "section_title": chunk["section_title"], "content": chunk["content"],
            }
            row = AIDocumentChunk(**metadata, chunk_index=chunk["chunk_index"], token_count=chunk["token_count"],
                                  embedding=vec_to_b64(emb))
            self.db.add(row)
            self.db.flush()
            writes.append((row.id, metadata, emb))
        if old_ids:
            self.db.execute(delete(AIDocumentChunk).where(AIDocumentChunk.id.in_(old_ids)))
        return old_ids, writes

    def _sync_vector_cache(self, document_id, old_ids, writes):
        # A second worker may have replaced these IDs since commit. Holding the same document
        # lock and rechecking IDs stops a delayed cache writer from resurrecting retired chunks.
        self.db.get(AIDocument, document_id, with_for_update=True, populate_existing=True)
        current_ids = set(self.db.scalars(select(AIDocumentChunk.id).where(AIDocumentChunk.document_id == document_id)))
        for chunk_id, metadata, embedding in writes:
            if chunk_id in current_ids:
                vector_store.upsert_chunk(chunk_id, metadata, embedding)
        if old_ids:
            vector_store.delete_chunks(old_ids)
        self.db.commit()

    def index_stage_reports(self, student_id: int) -> dict:
        """Synchronize current uploads and approved stage reports; failed documents remain retryable."""
        from app.models.business import GraduationCycle
        from app.shared.cycle import get_active_cycle

        cycle = get_active_cycle(self.db, student_id)
        cycle_id = cycle.id if cycle else None
        pending = []
        if cycle:
            self.db.get(GraduationCycle, cycle.id, with_for_update=True)
            for row in material_files(self.db, cycle):
                doc = self.db.scalar(select(AIDocument).where(
                    AIDocument.student_id == student_id, AIDocument.cycle_id == cycle_id,
                    AIDocument.file_id == row.id, AIDocument.document_type == row.material_type,
                ).order_by(AIDocument.id.desc()).limit(1).with_for_update())
                if doc is None:
                    doc = AIDocument(student_id=student_id, cycle_id=cycle_id, topic_id=cycle.topic_id,
                                     document_type=row.material_type, file_id=row.id, document_name=row.original_name,
                                     scope="STUDENT", index_status="WAITING", version=1)
                    self.db.add(doc)
                if doc.index_status in ("WAITING", "FAILED"):
                    pending.append(doc)

        for doc_type, (model, fields) in REPORTS.items():
            stmt = select(model).where(model.student_id == student_id, model.status == "APPROVED",
                                        model.cycle_id == cycle_id)
            report = self.db.scalar(stmt.order_by(model.id.desc()).limit(1))
            if report is None:
                continue
            content = report_text(report, fields)
            if not content:
                continue
            doc = self.db.scalar(select(AIDocument).where(
                AIDocument.student_id == student_id, AIDocument.cycle_id == cycle_id,
                AIDocument.document_type == doc_type, AIDocument.file_id.is_(None),
            ).order_by(AIDocument.id.desc()).limit(1).with_for_update())
            if doc is None:
                doc = AIDocument(student_id=student_id, cycle_id=cycle_id, topic_id=report.topic_id,
                                 document_type=doc_type, document_name=DOC_TYPE_CN[doc_type],
                                 scope="STUDENT", version=1)
                self.db.add(doc)
            elif doc.content_text == content and doc.index_status in ("SUCCESS", "PROCESSING"):
                continue
            elif doc.content_text != content:
                doc.version += 1
            doc.topic_id = report.topic_id
            doc.content_text = content
            doc.index_status = "WAITING"
            pending.append(doc)
        self.db.flush()
        ids = [doc.id for doc in pending]
        self.db.commit()
        failed = []
        for doc_id in ids:
            try:
                self.index_document(doc_id)
            except Exception:
                # The document retains its reason; a bad archive must not skip the remaining reports.
                failed.append(doc_id)
        return {"indexed": len(ids) - len(failed), "failed_document_ids": failed}

    def practice_documents(self, student_id: int) -> list[AIDocument]:
        return practice_documents(self.db, student_id)

    def retrieve_practice(self, query: str, student_id: int, document_types: list[str] | None = None,
                          top_k: int = 6, *, document_ids: list[int] | None = None) -> list[RetrievedChunk]:
        docs = {d.id: d for d in self.practice_documents(student_id)
                if (document_ids is None or d.id in document_ids)
                and (not document_types or d.document_type in document_types)}
        if not docs:
            return []
        hits = HybridRetriever(self.db, document_ids=list(docs)).retrieve(query, student_id, document_types, top_k=top_k)
        for hit in hits:
            hit.extra["document_name"] = docs[hit.document_id].document_name
        return hits

    def practice_chunks(self, student_id: int, chunk_ids: list[int]) -> list[RetrievedChunk]:
        docs = {d.id: d for d in self.practice_documents(student_id)}
        if not docs or not chunk_ids:
            return []
        rows = self.db.scalars(select(AIDocumentChunk).where(
            AIDocumentChunk.id.in_(chunk_ids), AIDocumentChunk.document_id.in_(list(docs)),
            AIDocumentChunk.student_id == student_id, AIDocumentChunk.scope == "STUDENT"))
        chunks = {c.id: RetrievedChunk(c.id, c.document_id, c.document_type, c.section_title, c.content[:1200],
                                       score=0, source="citation", extra={"document_name": docs[c.document_id].document_name})
                  for c in rows}
        return [chunks[cid] for cid in chunk_ids if cid in chunks]

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

    # ---------- Agent 多步问答(P10-C) ----------

    @staticmethod
    def _invalid_citations(text: str, source_count: int) -> list[int]:
        """解析 [n] 标记, 返回越界编号(升序去重)."""
        import re

        bad: list[int] = []
        for match in re.finditer(r"\[(\d{1,3})\]", text or ""):
            number = int(match.group(1))
            if number < 1 or number > source_count:
                if number not in bad:
                    bad.append(number)
        return sorted(bad)

    @staticmethod
    def _strip_markers(text: str, numbers: list[int]) -> str:
        """二次核验仍越界时丢弃这些标记(保留正文, 不改事实)."""
        import re

        out = text or ""
        for number in numbers:
            out = re.sub(r"\[" + str(number) + r"\]", "", out)
        return out

    @staticmethod
    def _chunks(text: str, size: int = 64) -> list[str]:
        return [text[i:i + size] for i in range(0, len(text), size)]

    def _regenerate_for_citations(self, query: str, runner, invalid: list[int],
                                  history: list[dict] | None) -> str:
        """引用越界后重生成一次: 把"引用了不存在的来源"作为追加约束再问一遍."""
        from app.ai.prompts import resolve_system_prompt

        system = (
            resolve_system_prompt(self.db, "AGENT_CITATION_CHECK").rstrip()
            + "\n\n"
            + resolve_system_prompt(self.db, "RAG_ANSWER")
        )
        bad = ", ".join(f"[{n}]" for n in invalid)
        user = (
            runner.answer_user_prompt()
            + f"\n\n【上一次回答的引用错误】你引用了不存在的来源: {bad}; "
              f"本次只允许引用 [1]..[{max(1, len(runner.sources))}] 之间真实存在的编号, "
              f"不得保留错误编号。"
        )
        try:
            return "".join(runner.llm.stream(system, user, history))
        except Exception:  # noqa: BLE001 — 重生成失败保留原答案, 由 done 标 degraded
            logger.exception("引用核验重生成失败, 保留首次答案")
            return runner.answer_text

    def answer_agent_stream(self, query: str, student_id: int | None,
                            history: list[dict] | None = None, user_id: int | None = None,
                            scene: str = "QA") -> Generator[dict, None, None]:
        """多步 agent 问答: meta → step* → delta* → citations → done.

        与 answer_stream 的兼容性:
        - 事件名只在原有 meta/delta/error/done 之外**新增** step / citations;
        - done 额外带 agent/degraded/steps/forced, 原有 length/status 不变;
        - 任何异常都会先发 error, 再回退单轮 answer_stream 的等价逻辑, 绝不 500。
        """
        import time as _time

        from app.ai.agent.loop import AgentRunner
        from app.ai.agent.tools import AgentContext, AgentToolbox
        from app.ai.agent.trace import agent_trace
        from app.core.config import settings

        started = datetime.now()
        wall = _time.monotonic()
        model_name = "unknown"
        status = "FAILED"
        degraded = False
        failure = ""
        text = ""
        emitted_delta = False
        skip_usage = False
        runner = None
        sources: list[dict] = []
        citations = {"checked": False, "regenerated": False, "invalid": []}

        try:
            ctx = AgentContext(student_id=student_id, user_id=user_id, scene=scene, history=history)
        except Exception as exc:  # noqa: BLE001 — 身份非法(如 student_id 缺失)直接降级
            ctx = None
            failure = f"AgentContext 构建失败: {exc}"

        toolbox = AgentToolbox()
        llm = None
        try:
            llm = LLMFactory.get()
            model_name = llm.name
        except Exception as exc:  # noqa: BLE001 — 模型不可用时后面统一走降级
            degraded = True
            failure = f"模型不可用: {exc}"
            logger.exception("Agent 问答取模型失败, 将降级")

        citation_check = bool(getattr(settings, "agent_citation_check", True))

        try:
            yield {"event": "meta", "data": {
                "provider": model_name,
                "agent": True,
                "tools": [item["name"] for item in toolbox.describe()],
                "tool_details": toolbox.describe(),
                "sources": [],
                "max_steps": int(getattr(settings, "agent_max_steps", 4) or 4),
                "scene": scene,
            }}
            if ctx is None:
                raise RuntimeError(failure or "Agent 上下文不可用")

            runner = AgentRunner(self.db, ctx, toolbox=toolbox, llm=llm,
                                 stream_deltas=not citation_check)
            for event in runner.run(query, history):
                name = event["event"]
                if name == "answer_text":
                    text = str(event["data"].get("text") or "")
                    continue
                if name == "delta":
                    emitted_delta = True
                yield event

            sources = [
                {"index": item["index"], "chunk_id": item.get("chunk_id"),
                 "document_type": item.get("document_type"), "section": item.get("section"),
                 "score": item.get("score")}
                for item in runner.sources
            ]

            # 引用核验: 越界 → 重生成一次; 二次仍越界 → 丢弃标记并在 citations 里报 invalid
            if citation_check:
                citations["checked"] = True
                invalid = self._invalid_citations(text, len(runner.sources))
                if invalid:
                    logger.info("agent 引用越界 %s, 触发一次重生成", invalid)
                    text = self._regenerate_for_citations(query, runner, invalid, history)
                    citations["regenerated"] = True
                    still_bad = self._invalid_citations(text, len(runner.sources))
                    if still_bad:
                        citations["invalid"] = still_bad
                        text = self._strip_markers(text, still_bad)
                        degraded = True

            if not emitted_delta:
                # 先核验后下发(或执行期出错情况): 这里才把最终文本按块推给前端
                for piece in self._chunks(text):
                    emitted_delta = True
                    yield {"event": "delta", "data": piece}

            yield {"event": "citations", "data": {
                "sources": sources,
                "checked": citations["checked"],
                "regenerated": citations["regenerated"],
                "invalid": citations["invalid"],
            }}

            if runner.forced:
                degraded = True
            status = "DEGRADED" if degraded else "SUCCESS"
        except GeneratorExit:
            status = "CANCELLED"
            raise
        except Exception as exc:  # noqa: BLE001 — D47 延伸: 多步失败必须退回单轮, 不允许白屏
            status = "DEGRADED"
            degraded = True
            failure = f"{type(exc).__name__}: {exc}"[:200]
            logger.exception("Agent 问答失败, 回退单轮检索回答")
            yield {"event": "error", "data": {
                "message": "智能问答多步执行失败, 已回退单轮检索回答",
                "detail": failure,
            }}
            if not emitted_delta:
                skip_usage = True  # 由 answer_stream 自己记一次用量, 避免重复计费
                for event in self.answer_stream(query, student_id, history,
                                                user_id=user_id, scene=scene):
                    if event["event"] in ("done", "meta"):
                        continue  # meta 已在前面发过, done 统一在末尾发
                    if event["event"] == "delta":
                        emitted_delta = True
                        text += event["data"]
                    yield event
        finally:
            elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
            steps = runner.step_count if runner is not None else 0
            forced = runner.forced if runner is not None else failure
            tool_calls = list(runner.tool_calls) if runner is not None else []
            source_count = len(runner.sources) if runner is not None else len(sources)
            if not skip_usage:
                self._write_usage_log(
                    user_id=user_id, scene=scene, model=model_name,
                    input_tokens=len(query or ""), output_tokens=len(text),
                    latency_ms=elapsed_ms, status=status,
                )
            try:
                agent_trace.record(query=query, student_id=student_id, steps=steps,
                                   elapsed_ms=elapsed_ms, tool_calls=tool_calls,
                                   status=status, degraded=degraded,
                                   forced=forced or "", sources=source_count)
            except Exception:  # noqa: BLE001 — 轨迹记录失败不影响回答
                logger.exception("agent trace 记录失败")

        logger.info("agent 问答完成: status=%s steps=%s degraded=%s %dms",
                    status, steps, degraded, int((_time.monotonic() - wall) * 1000))
        yield {"event": "done", "data": {
            "length": len(text),
            "status": status,
            "agent": True,
            "degraded": degraded,
            "steps": steps,
            "forced": forced or "",
            "sources": len(sources),
            "invalid_citations": citations["invalid"],
        }}

