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
            # 记录本次索引用的 embedding 指纹, 供 /health/ai 判断"换模型后是否需重建知识库"
            try:
                from app.ai.health import mark_embedding_indexed
                mark_embedding_indexed(self.db)
            except Exception:  # noqa: BLE001 — 指纹标记失败不影响索引结果
                logger.warning("embedding 指纹标记失败", exc_info=True)
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

