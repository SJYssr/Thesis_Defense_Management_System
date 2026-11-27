"""DefenseService: AI 模拟答辩核心(roadmap 四十~四十八章).

流程: 生成问题库 → 创建Session → 逐题问答(RAG评价+动态追问) → 答辩报告 → PPT大纲.
"""
import json
import logging
import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ai.defense.dialogue import LiveQuestion, choose_question, usable_question, usable_quote
from app.ai.defense.oral import offers_choices, oral_question, question_key
from app.ai.defense.rubric import accuracy_from_evidence, assess_points, mark_snapshot, marking_points, snapshot
from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.prompts import (
    defense_evaluate_prompt,
    defense_question_prompt,
    defense_report_prompt,
    ppt_outline_prompt,
    resolve_system_prompt,
)
from app.ai.rag.rag_service import RAGService
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.models.ai import (
    AIDefenseObservation,
    AIDefenseQuestion,
    AIDefenseSession,
    AIDefenseTurn,
    AIDocumentChunk,
    AIUsageLog,
)
from app.models.business import GraduationTopic
from app.models.enums import QuestionType
from app.models.user import StudentProfile
from app.shared.state_machine import PRACTICE_SESSION, transition

logger = logging.getLogger("app.ai.defense")


def _normalize_question(text: str | None) -> str:
    """题面比较用的归一化: 去空白 + 全角/半角标点差异不参与判定."""
    return re.sub(r"\s+", "", (text or "")).strip()


class _Evaluation(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    accuracy: int = Field(ge=0, le=35)
    logic: int = Field(ge=0, le=25)
    clarity: int = Field(ge=0, le=20)
    depth: int = Field(ge=0, le=20)
    feedback: str = Field(min_length=1, max_length=5000)
    source_ids: list[int] = Field(min_length=1, max_length=5)
    follow_up_needed: bool
    follow_up_question: str | None = Field(default=None, max_length=1200)

    @model_validator(mode="after")
    def require_follow_up(self):
        if self.follow_up_needed and not self.follow_up_question:
            raise ValueError("追问缺少题目")
        return self


class _Report(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    overall_score: int = Field(ge=0, le=100)
    dimension_scores: dict[str, Annotated[int, Field(ge=0, le=100)]]
    summary: str = Field(min_length=1)
    strengths: list[str]
    weaknesses: list[str]
    risky_questions: list[str]
    suggestions: list[str]


def _scored_report(turns: list[AIDefenseTurn]) -> dict:
    """仅汇总实际逐题评分；旧记录缺失的维度不作推算。"""
    dimensions = {}
    for label, column, maximum in (("知识准确性", "accuracy_score", 35), ("逻辑完整性", "logic_score", 25),
                                   ("表达清晰度", "clarity_score", 20), ("技术深度", "depth_score", 20)):
        observed = [score for turn in turns if (score := getattr(turn, column)) is not None]
        if observed:
            dimensions[label] = round(sum(observed) * 100 / (maximum * len(observed)))
    main = sum(not turn.is_follow_up for turn in turns)
    return {
        "summary_source": "scored_turns", "dimension_scores": dimensions,
        "summary": f"AI 文字总评暂不可用。本次完成 {main} 道正题、{len(turns) - main} 道追问。"
                   "综合分为已评分轮次的平均分，各维度按对应满分折算为百分制，可在逐题记录中核对。",
        "strengths": [], "weaknesses": [], "risky_questions": [], "suggestions": [],
    }


def _source(hit: RetrievedChunk) -> dict:
    return {"chunk_id": hit.chunk_id, "document_id": hit.document_id, "document_type": hit.document_type,
            "document_name": hit.extra.get("document_name") or "", "section": hit.section_title or "",
            "excerpt": hit.content.strip()[:750]}


def _citations(ids: object, available: dict[int, dict]) -> list[dict]:
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in available for i in ids):
        return []
    return [available[i] for i in dict.fromkeys(ids)][:5]


def _stored_sources(raw: str | None) -> list[dict]:
    return snapshot(raw)["sources"]


class DefenseService:
    def __init__(self, db: Session):
        self.db = db
        self.rag = RAGService(db)

    # ---------- 前置: 学生的题目 ----------

    def _get_topic(self, student_id: int) -> GraduationTopic:
        from app.models.business import TopicSelection
        from app.shared.cycle import get_active_cycle

        cycle = get_active_cycle(self.db, student_id)
        sel = self.db.execute(
            select(TopicSelection).where(TopicSelection.cycle_id == cycle.id,
                                         TopicSelection.student_id == student_id,
                                         TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
        ).scalar_one_or_none() if cycle else None
        if sel is None or (cycle.topic_id is not None and cycle.topic_id != sel.topic_id):
            raise BusinessException("尚未通过选题, 无法使用AI模拟答辩")
        topic = self.db.get(GraduationTopic, sel.topic_id)
        if topic is None:
            raise ResourceNotFoundException("题目不存在")
        return topic

    def _generation_sources(self, student_id: int, document_ids: list[int] | None) -> list[dict]:
        docs = self.rag.practice_documents(student_id)
        if document_ids is not None:
            wanted = set(document_ids)
            if not wanted or wanted - {d.id for d in docs}:
                raise BusinessException("所选材料不可用于本次答辩，请刷新材料列表重新选择")
            docs = [d for d in docs if d.id in wanted]
        groups = ([d.id for d in docs if d.document_type != "SOURCE_CODE"],
                  [d.id for d in docs if d.document_type == "SOURCE_CODE"])
        groups = [ids for ids in groups if ids]
        sources = []
        for ids in groups:
            hits = self.rag.retrieve_practice("核心设计 关键实现 数据流 方案验证 测试 架构", student_id,
                                              top_k=12 // len(groups), document_ids=ids)
            if not hits:
                # 出题可从一章/一个函数开始阅读；所有样本仍须通过私有材料回查。
                first_ids = self.db.scalars(select(func.min(AIDocumentChunk.id)).where(
                    AIDocumentChunk.document_id.in_(ids), AIDocumentChunk.student_id == student_id
                ).group_by(AIDocumentChunk.document_id).order_by(AIDocumentChunk.document_id).limit(12 // len(groups)))
                hits = self.rag.practice_chunks(student_id, list(first_ids))
            sources.extend(_source(hit) for hit in hits if hit.content.strip())
        if not sources:
            raise BusinessException("当前毕业流程尚无可读取的材料，请上传报告或源码并完成索引")
        return sources

    def _draft_questions(self, student_id: int, user_id: int | None, title: str, sources: list[dict],
                         count: int, *, preceding: dict | None = None,
                         avoid: list[str] | None = None) -> list[AIDefenseQuestion]:
        by_id = {s["chunk_id"]: s for s in sources}
        available = {s["document_type"] == "SOURCE_CODE" for s in sources}
        required = available if count >= len(available) else set()
        representatives = {}
        rows = []
        closed_count = 0
        seen = {question_key(q) for q in avoid or []}
        llm = LLMFactory.get()
        system = resolve_system_prompt(self.db, "DEFENSE_GENERATE_QUESTIONS")
        for _ in range(2):
            missing = required - representatives.keys()
            needed = max(count - len(rows), len(missing))
            prompt = defense_question_prompt(title, sources, needed, avoid=[*(avoid or []), *(r.question for r in rows)],
                                             required=["项目实现" if k else "书面材料" for k in sorted(missing)],
                                             preceding=preceding)
            started = datetime.now()
            raw = llm.complete(system, prompt)
            self.db.add(AIUsageLog(user_id=user_id, scene="GENERATE_QUESTIONS", model=llm.name,
                                   input_tokens=len(prompt), output_tokens=len(raw),
                                   latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
            try:
                data = extract_json(raw)
            except ValueError:
                data = {}
            items = data.get("questions") if isinstance(data, dict) else None
            for item in items if isinstance(items, list) else ():
                if not isinstance(item, dict) or not oral_question(item.get("question")):
                    continue
                question = item["question"].strip()
                key = question_key(question)
                cited = _citations(item.get("source_ids"), by_id)
                points = marking_points(item.get("rubric"), cited)
                closed = offers_choices(question)
                if count >= 3 and closed and closed_count >= max(1, count // 3):
                    continue
                if not points or key in seen:
                    continue
                seen.add(key)
                closed_count += int(closed)
                qtype, difficulty = item.get("question_type"), item.get("difficulty")
                row = AIDefenseQuestion(student_id=student_id, question=question,
                                        question_type=qtype if qtype in tuple(QuestionType) else "BASIC",
                                        difficulty=difficulty if difficulty in ("EASY", "MEDIUM", "HARD") else "MEDIUM",
                                        reference_chunks=mark_snapshot(cited, points),
                                        reference_answer="；".join(p["expectation"] for p in points))
                rows.append(row)
                for point in points:
                    for cid in point["source_ids"]:
                        representatives.setdefault(by_id[cid]["document_type"] == "SOURCE_CODE", row)
                if len(rows) >= count and required <= representatives.keys():
                    # Start with evidence from both report and implementation when available.
                    first = list(dict.fromkeys(representatives[k] for k in sorted(representatives)))
                    return (first + [r for r in rows if r not in first])[:count]
        raise BusinessException(f"模型提供了 {len(rows)} 道可核对的口头问题，未能按材料完整生成 {count} 道。"
                                "请重试，或补充项目设计、实现和测试依据。", 50302)

    def generate_questions(self, student_id: int, user_id: int, document_ids: list[int] | None = None,
                           question_count: int | None = None) -> list[dict]:
        topic = self._get_topic(student_id)
        sources = self._generation_sources(student_id, document_ids)
        rows = self._draft_questions(student_id, user_id, topic.title, sources, question_count or 22)
        self.db.execute(select(StudentProfile.id).where(StudentProfile.id == student_id).with_for_update())
        replaceable = delete(AIDefenseQuestion).where(AIDefenseQuestion.student_id == student_id,
                                                       AIDefenseQuestion.session_id.is_(None),
                                                       AIDefenseQuestion.used == 0)
        # 升级前的会话没有预留题目，需要继续使用当时尚未回答的旧题。
        legacy_session = self.db.scalar(select(AIDefenseSession.id).where(
            AIDefenseSession.student_id == student_id, AIDefenseSession.status.in_(("CREATED", "RUNNING")),
            ~select(AIDefenseQuestion.id).where(AIDefenseQuestion.session_id == AIDefenseSession.id).exists()
        ).limit(1))
        if legacy_session is not None:
            replaceable = replaceable.where(AIDefenseQuestion.reference_chunks.is_not(None))
        self.db.execute(replaceable)
        self.db.add_all(rows)
        self.db.flush()
        items = [{"id": q.id, "question": q.question, "question_type": q.question_type, "difficulty": q.difficulty,
                  "sources": _stored_sources(q.reference_chunks)} for q in rows]
        self.db.commit()
        return items

    def create_session(self, student_id: int, question_count: int = 8, question_ids: list[int] | None = None) -> dict:
        topic = self._get_topic(student_id)
        query = select(AIDefenseQuestion).where(AIDefenseQuestion.student_id == student_id,
                                                AIDefenseQuestion.session_id.is_(None), AIDefenseQuestion.used == 0,
                                                AIDefenseQuestion.reference_chunks.is_not(None))
        if question_ids is not None:
            query = query.where(AIDefenseQuestion.id.in_(question_ids))
        available = list(self.db.scalars(query.order_by(AIDefenseQuestion.id).with_for_update()))
        if question_ids is not None and len(available) != len(set(question_ids)):
            raise BusinessException("题库已被使用或重新生成，请重新选择材料并生成题目", 40900)
        if not available:
            raise BusinessException("请先生成答辩问题库")
        # 前两道是书面材料及源码的代表题，后续优先覆盖不同题型。
        ordered = available[:2]
        types = {q.question_type for q in ordered}
        for q in available[2:]:
            if q.question_type not in types:
                ordered.append(q)
                types.add(q.question_type)
        selected = (ordered + [q for q in available if q not in ordered])[:question_count]
        material_ids = {d.id for d in self.rag.practice_documents(student_id)}
        if any(not _stored_sources(q.reference_chunks) or any(
                s["document_id"] not in material_ids for s in _stored_sources(q.reference_chunks)) for q in selected):
            raise BusinessException("题库与当前材料不一致，请重新生成问题")
        session = AIDefenseSession(student_id=student_id, topic_id=topic.id, status="CREATED",
                                   question_count=len(selected))
        self.db.add(session)
        self.db.flush()
        for row in selected:
            row.session_id = session.id
        self.db.commit()
        return {"session_id": session.id, "question_count": len(selected), "available_questions": len(available)}

    def _live_material(self, student_id, document_ids, previous, answer=""):
        """Select authorized report and code excerpts; revisit exact anchors of cached questions."""
        docs = [d for d in self.rag.practice_documents(student_id) if d.id in document_ids]
        opened = [snapshot(t.reference_chunks) for t in previous]
        seen_docs = [src["document_id"] for turn in opened for src in turn["sources"]]
        seen_chunks = [src["chunk_id"] for turn in opened for src in turn["sources"]]
        used = {p["expectation"] for turn in opened for p in turn.get("rubric", [])
                if isinstance(p, dict) and isinstance(p.get("expectation"), str)}
        keys = {question_key(t.question) for t in previous}
        docs.sort(key=lambda d: (seen_docs.count(d.id), d.id))
        # Include both kinds even when the student has many report versions.
        chosen = [group[0] for code in (False, True) if (group := [
            d for d in docs if (d.document_type == "SOURCE_CODE") == code])]
        chosen += [d for d in docs if d not in chosen]
        chunk_ids = []
        for doc in chosen[:8]:
            ids = list(self.db.scalars(select(AIDocumentChunk.id).where(
                AIDocumentChunk.document_id == doc.id, AIDocumentChunk.student_id == student_id,
                AIDocumentChunk.scope == "STUDENT"
            ).order_by(AIDocumentChunk.id.in_(seen_chunks), AIDocumentChunk.id).limit(5)))
            chunk_ids.extend(ids)
        sources = [_source(s) for s in self.rag.practice_chunks(student_id, chunk_ids) if s.content.strip()]
        sources.sort(key=lambda s: (seen_docs.count(s["document_id"]), seen_chunks.count(s["chunk_id"])))
        # Interleave documents instead of spending the entire prompt on the first report.
        first_per_doc = {}
        for source in sources:
            first_per_doc.setdefault(source["document_id"], source)
        # Search the entire authorized project for the latest spoken answer (including implementation
        # details deep in a source archive); no embedding request is required during the conversation.
        matches = []
        if answer and docs:
            ranked = HybridRetriever(self.db, document_ids=[d.id for d in docs]).keyword_search(
                answer[:500], student_id, None, top_k=4)
            matches = [_source(hit) for hit in self.rag.practice_chunks(student_id, [cid for cid, _ in ranked])]
        sample = matches + [s for s in first_per_doc.values() if s not in matches]
        sample += [s for s in sources if s not in sample]
        cached = None
        allowed = {d.id for d in docs}
        candidates = self.db.scalars(select(AIDefenseQuestion).where(
            AIDefenseQuestion.student_id == student_id, AIDefenseQuestion.reference_chunks.is_not(None)
        ).order_by(AIDefenseQuestion.id.desc()).limit(64))
        for row in candidates:
            evidence = snapshot(row.reference_chunks)
            points = evidence.get("rubric")
            pinned = evidence["sources"]
            if (not points or not pinned or not usable_question(row.question, keys)
                    or not {s["document_id"] for s in pinned} <= allowed
                    or not isinstance(points, list)
                    or any(not isinstance(p, dict) or not isinstance(p.get("expectation"), str)
                           or p["expectation"] in used for p in points)):
                continue
            current = [_source(s) for s in self.rag.practice_chunks(student_id, [s["chunk_id"] for s in pinned])]
            by_id = {src["chunk_id"]: src for src in current}
            if (len(current) == len(pinned) and marking_points(points, current) == points
                    and all(usable_quote(by_id[cid], point["expectation"])
                            for point in points for cid in point["source_ids"])):
                cached = LiveQuestion(row.question, False, mark_snapshot(current, points))
                break
        return sample[:10], used, cached

    def _ask_live(self, student_id, title, sources, used, cached, *, asked, question, answer, may_follow, may_advance):
        draft = choose_question(title=title, sources=sources, question=question, answer=answer, asked=asked, used=used,
                                may_follow=may_follow, may_advance=may_advance, cached=cached)
        if draft or not may_advance:
            return draft
        # Unfamiliar code may have no spoken equivalent without a translator. Request one grounded
        # main question when the low-latency path could not interpret a still-unused code passage.
        untranslated = [s for s in sources if (s["document_type"] == "SOURCE_CODE" or
                        not re.search(r"[\u3400-\u9fff]", s["excerpt"]))
                        and usable_quote(s, s["excerpt"]) and not any(quote in s["excerpt"] for quote in used)]
        if untranslated:
            row, = self._draft_questions(student_id, None, title, untranslated, 1, avoid=asked)
            generated = snapshot(row.reference_chunks)
            current = {s["chunk_id"]: s for s in generated["sources"]}
            if (usable_question(row.question, {question_key(q) for q in asked}) and
                    all(usable_quote(current[cid], p["expectation"])
                        for p in generated["rubric"] for cid in p["source_ids"])):
                return LiveQuestion(row.question, False, row.reference_chunks)
            raise BusinessException("未能将本人的材料转换为可口述的问题，请重试或补充设计说明", 50302)
        return None

    def start_live(self, student_id: int, request_id: str, question_count: int) -> dict:
        def existing():
            return self.db.scalar(select(AIDefenseSession).where(
                AIDefenseSession.student_id == student_id, AIDefenseSession.live_request_id == request_id))
        session = existing()
        if session:
            return self._live_current(session)
        topic = self._get_topic(student_id)
        title, topic_id = topic.title, topic.id
        doc_ids = [d.id for d in self.rag.practice_documents(student_id)]
        sources, used, cached = self._live_material(student_id, doc_ids, [])
        self.db.rollback()
        draft = self._ask_live(student_id, title, sources, used, cached, question="", answer="", asked=[],
                               may_follow=False, may_advance=True)
        if not draft:
            raise BusinessException("未能从本人的报告或源码生成可核对的首题，请重试或补充材料", 50302)
        self.db.execute(select(StudentProfile.id).where(StudentProfile.id == student_id).with_for_update())
        session = existing()
        if session:
            result = self._live_current(session)
            self.db.commit()
            return result
        self._confirm_live_evidence(student_id, doc_ids, draft)
        session = AIDefenseSession(student_id=student_id, topic_id=topic_id, status="CREATED",
                                   question_count=question_count, live_request_id=request_id,
                                   live_document_ids=json.dumps(doc_ids))
        self.db.add(session)
        self.db.flush()
        transition(self.db, PRACTICE_SESSION, session, "start", None)
        session.started_at = datetime.now()
        turn = AIDefenseTurn(session_id=session.id, turn_no=1, question=draft.text, is_follow_up=0,
                             reference_chunks=draft.evidence)
        self.db.add(turn)
        self.db.flush()
        result = {"session_id": session.id, "finished": False, **self._turn_item(turn)}
        self.db.commit()
        return result

    def _confirm_live_evidence(self, student_id, doc_ids, draft):
        evidence = snapshot(draft.evidence)
        sources = [_source(hit) for hit in self.rag.practice_chunks(student_id, [
            s["chunk_id"] for s in evidence["sources"]])]
        if (not sources or not {s["document_id"] for s in sources} <= set(doc_ids)
                or marking_points(evidence["rubric"], sources) != evidence["rubric"]):
            raise BusinessException("本轮材料已更新，请重新进入本人的视频答辩", 40900)

    def _live_current(self, session: AIDefenseSession) -> dict:
        if session.status == "FINISHED":
            return {"session_id": session.id, "finished": True}
        turn = self.db.scalar(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session.id, AIDefenseTurn.student_answer.is_(None)
        ).order_by(AIDefenseTurn.turn_no.desc()).limit(1))
        if turn is None:
            raise BusinessException("没有可继续的问答轮次", 40900)
        return {"session_id": session.id, "finished": False, **self._turn_item(turn)}

    def _live_answer(self, session: AIDefenseSession, turn_no: int, answer: str, *, lock: bool = False):
        if not session.live_request_id:
            raise BusinessException("该练习不是实时答辩，请通过原有流程继续", 40900)
        query = select(AIDefenseTurn).where(AIDefenseTurn.session_id == session.id,
                                            AIDefenseTurn.turn_no == turn_no)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        turn = self.db.scalar(query)
        if turn is None:
            raise BusinessException("回答与当前轮次不一致", 40900)
        if turn.student_answer == answer:
            return None  # Idempotent replay, including a lost last-turn response.
        if turn.student_answer is not None or session.status != "RUNNING":
            raise BusinessException("该轮已结束，请继续当前问题", 40900)
        return turn

    def advance_live(self, student_id: int, session_id: int, turn_no: int, answer: str) -> dict:
        session = self._own_session(student_id, session_id)
        turn = self._live_answer(session, turn_no, answer)
        if turn is None:
            return self._live_current(session)
        previous = list(self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session_id).order_by(AIDefenseTurn.turn_no)))
        main_index = (session.answered_count or 0) + int(not turn.is_follow_up)
        may_advance, may_follow = main_index < session.question_count, not turn.is_follow_up
        decision = None
        if may_advance or may_follow:
            topic = self.db.get(GraduationTopic, session.topic_id)
            doc_ids = json.loads(session.live_document_ids or "[]")
            sources, used, cached = self._live_material(student_id, doc_ids, previous, answer)
            title, question = topic.title if topic else "", turn.question
            asked = [t.question for t in previous]
            self.db.rollback()
            decision = self._ask_live(student_id, title, sources, used, cached, question=question,
                                      answer=answer, asked=asked, may_follow=may_follow, may_advance=may_advance)
        # Commit the answer and its successor (or completion) together. Recheck after model I/O.
        session = self._own_session(student_id, session_id, lock=True)
        turn = self._live_answer(session, turn_no, answer, lock=True)
        if turn is not None:
            turn.student_answer = answer
            if not turn.is_follow_up:
                session.answered_count = (session.answered_count or 0) + 1
            if decision is None:
                session.finished_at = datetime.now()
                session.question_count = session.answered_count
                session.report_status = "pending"
                transition(self.db, PRACTICE_SESSION, session, "finish", None)
            else:
                self._confirm_live_evidence(student_id, doc_ids, decision)
                if decision.follow:
                    turn.follow_up_question = decision.text
                self.db.add(AIDefenseTurn(session_id=session_id, turn_no=turn_no + 1,
                                          is_follow_up=int(decision.follow), question=decision.text,
                                          reference_chunks=decision.evidence))
            self.db.flush()
        result = self._live_current(session)
        self.db.commit()
        return result

    def start_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        transition(self.db, PRACTICE_SESSION, session, "start", None)
        session.started_at = datetime.now()
        turn = self._next_turn(self.db, session)
        if turn is None:
            raise BusinessException("没有可用的问题")
        self._prepare_spoken_turn(session, turn)
        self.db.commit()
        return {"session_id": session.id, **self._turn_item(turn)}

    def _prepare_spoken_turn(self, session: AIDefenseSession, turn: AIDefenseTurn) -> None:
        """Repair old, unspoken prompts from the same still-authorized passages."""
        if oral_question(turn.question):
            return
        pinned = _stored_sources(turn.reference_chunks)
        hits = self.rag.practice_chunks(session.student_id, [s["chunk_id"] for s in pinned]) if pinned else []
        if not hits or len(hits) != len(pinned):
            raise BusinessException("旧题缺少可核对的材料，请重新开始一次视频答辩", 40900)
        previous = list(self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session.id, AIDefenseTurn.turn_no < turn.turn_no
        ).order_by(AIDefenseTurn.turn_no)))
        preceding = None
        if turn.is_follow_up and previous:
            preceding = {"question": previous[-1].question, "answer": previous[-1].student_answer}
        topic = self.db.get(GraduationTopic, session.topic_id)
        user_id = self.db.scalar(select(StudentProfile.user_id).where(StudentProfile.id == session.student_id))
        draft, = self._draft_questions(session.student_id, user_id, topic.title if topic else "",
                                       [_source(hit) for hit in hits], 1, preceding=preceding,
                                       avoid=[t.question for t in previous])
        turn.question, turn.reference_chunks = draft.question, draft.reference_chunks
        if turn.is_follow_up and previous:
            previous[-1].follow_up_question = turn.question

    def _own_session(self, student_id: int, session_id: int, *, lock: bool = False) -> AIDefenseSession:
        query = select(AIDefenseSession).where(AIDefenseSession.id == session_id,
                                               AIDefenseSession.student_id == student_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        session = self.db.scalar(query)
        if session is None:
            raise ResourceNotFoundException("答辩会话不存在")
        return session

    def record_observations(self, student_id: int, session_id: int, run_id: str,
                            no_face: int, multiple_faces: int, tab_hidden: int) -> None:
        session = self._own_session(student_id, session_id, lock=True)
        # Allow a browser to finish syncing its observations after the report has been generated.
        if session.status not in ("RUNNING", "FINISHED"):
            raise BusinessException("请在练习开始后启用观察", 40900)
        row = self.db.get(AIDefenseObservation, (session_id, run_id), with_for_update=True)
        if row is None:
            row = AIDefenseObservation(session_id=session_id, run_id=run_id, no_face=no_face,
                                       multiple_faces=multiple_faces, tab_hidden=tab_hidden)
            self.db.add(row)
        else:
            # A delayed or replayed snapshot must never count twice or undo a newer observation.
            row.no_face = max(no_face, row.no_face)
            row.multiple_faces = max(multiple_faces, row.multiple_faces)
            row.tab_hidden = max(tab_hidden, row.tab_hidden)
        self.db.commit()

    def _observation_counts(self, session_id: int) -> dict | None:
        observation = AIDefenseObservation
        totals = self.db.execute(select(func.sum(observation.no_face), func.sum(observation.multiple_faces),
                                        func.sum(observation.tab_hidden)).where(observation.session_id == session_id)).one()
        if totals[0] is None:
            return None
        return {"no_face": int(totals[0]), "multiple_faces": int(totals[1]), "tab_hidden": int(totals[2])}

    def _next_turn(self, db: Session, session: AIDefenseSession) -> AIDefenseTurn | None:
        """从未使用问题中选取下一题(D51): 两轮策略——先排已覆盖题型, 再按 id 顺序兜底."""
        reserved = db.scalar(select(AIDefenseQuestion.id).where(
            AIDefenseQuestion.session_id == session.id).limit(1))
        pool = (AIDefenseQuestion.session_id == session.id if reserved else
                AIDefenseQuestion.session_id.is_(None) & AIDefenseQuestion.reference_chunks.is_(None))
        candidates = db.scalars(select(AIDefenseQuestion).where(
            AIDefenseQuestion.student_id == session.student_id, pool, AIDefenseQuestion.used == 0
        ).order_by(AIDefenseQuestion.id).with_for_update()).all()
        if not candidates:
            return None
        existing = db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session.id)
        ).scalars().all()
        # 找出该会话已问过的题目文本
        asked = {t.question for t in existing}
        # D51: 已覆盖题型必须真正写入集合(历史实现从不 add, "优先未覆盖题型"恒失效)
        seen_types: set[str] = set()
        asked_qids = [t.question_id for t in existing if t.question_id]
        if asked_qids:
            seen_types = {
                q.question_type for q in db.execute(
                    select(AIDefenseQuestion).where(AIDefenseQuestion.id.in_(asked_qids))
                ).scalars().all()
            }
        # 第一轮: 只看未覆盖题型; 第二轮: 任意未问过的题
        picked = next((c for c in candidates
                       if c.question not in asked and c.question_type not in seen_types), None)
        if picked is None:
            picked = next((c for c in candidates if c.question not in asked), None)
        if picked is None:
            return None
        picked.used = 1
        turn_no = len(existing) + 1
        turn = AIDefenseTurn(session_id=session.id, question_id=picked.id, turn_no=turn_no,
                             question=picked.question, reference_chunks=picked.reference_chunks)
        turn.is_follow_up = 0
        # 将 question_type 存到 reference_chunks 字段冗余? 不, 用 question_id 关联查询
        db.add(turn)
        return turn

    # ---------- 回答评价 ----------

    def _turn_item(self, turn: AIDefenseTurn) -> dict:
        question = self.db.get(AIDefenseQuestion, turn.question_id) if turn.question_id else None
        evidence = snapshot(turn.reference_chunks)
        return {"turn_no": turn.turn_no, "question": turn.question,
                "question_type": question.question_type if question else None,
                "is_follow_up": bool(turn.is_follow_up), "student_answer": turn.student_answer,
                "ai_feedback": turn.ai_feedback, "total_score": turn.total_score,
                "scores": {"accuracy": turn.accuracy_score, "logic": turn.logic_score,
                           "clarity": turn.clarity_score, "depth": turn.depth_score, "total": turn.total_score},
                "sources": evidence["sources"],
                "assessment": evidence.get("assessment", []) if turn.student_answer is not None else []}

    def evaluate_answer(self, student_id: int, session_id: int, question: str, answer: str, user_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.live_request_id or session.status != "RUNNING":
            raise BusinessException("会话不在原有评分流程中", 40900)
        answer = answer.strip()
        turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
                              .order_by(AIDefenseTurn.id.desc()).limit(1))
        if turn is None or _normalize_question(question) != _normalize_question(turn.question):
            raise BusinessException("提交的题目与当前轮次不一致，请刷新后重试", 40003)
        if not answer:
            raise BusinessException("没有识别到回答，请再回答一次")
        if turn.student_answer is not None:
            if answer == turn.student_answer:
                return {**self._turn_item(turn), "feedback": turn.ai_feedback, "finished": False}
            raise BusinessException("此轮已评分，请查看反馈并继续下一题", 40900)

        self._grade(session, turn, answer, user_id)
        self.db.commit()
        return {**self._turn_item(turn), "feedback": turn.ai_feedback, "finished": False}

    def _claimed(self, session_id: int, claim: str, *, lock: bool = False) -> AIDefenseSession:
        query = select(AIDefenseSession).where(AIDefenseSession.id == session_id,
            AIDefenseSession.status == "FINISHED", AIDefenseSession.report_status == "pending",
            AIDefenseSession.report_claim == claim, AIDefenseSession.report_lease_until > datetime.now())
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        session = self.db.scalar(query)
        if session is None:
            raise BusinessException("当前评分任务的租约已失效", 40900)
        return session

    def score_live_turn(self, session_id: int, claim: str, turn_id: int) -> None:
        session = self._claimed(session_id, claim)
        turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.id == turn_id,
            AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None)))
        if turn is None:
            raise ResourceNotFoundException("评分轮次不存在")
        if turn.total_score is not None:
            return
        user_id = self.db.scalar(select(StudentProfile.user_id).where(StudentProfile.id == session.student_id))
        self._grade(session, turn, turn.student_answer, user_id, claim=claim)
        self.db.commit()

    def _grade(self, session: AIDefenseSession, turn: AIDefenseTurn, answer: str, user_id: int | None,
               *, claim: str | None = None) -> None:
        student_id, session_id, turn_id = session.student_id, session.id, turn.id
        deferred = claim is not None
        pinned = _stored_sources(turn.reference_chunks)
        if pinned:
            hits = self.rag.practice_chunks(student_id, [s["chunk_id"] for s in pinned])
            # 重新索引会产生新 chunk；不把旧快照当作仍然可用的引用继续评分。
            if len(hits) != len(pinned):
                raise BusinessException("本题的材料已更新或移除，请使用最新材料重新生成练习")
        else:
            # 兼容升级前已在进行中的练习。无已授权的真实材料时保留待答状态。
            document_ids = json.loads(session.live_document_ids or "[]") if deferred else None
            hits = self.rag.retrieve_practice(turn.question, student_id, top_k=4, document_ids=document_ids)
        if not hits:
            raise BusinessException("缺少可核对的报告或源码，请先完成材料索引再作答")
        sources = [_source(hit) for hit in hits]
        docs = list(dict.fromkeys(hit.document_id for hit in hits))
        rubric = snapshot(turn.reference_chunks).get("rubric", [])
        if not deferred or not rubric:
            query = turn.question if deferred else turn.question + " " + answer[:240]
            further = self.rag.retrieve_practice(query, student_id, top_k=3, document_ids=docs)
            seen = {s["chunk_id"] for s in sources}
            sources.extend(_source(hit) for hit in further if hit.chunk_id not in seen)
        previous = self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None),
            AIDefenseTurn.turn_no < turn.turn_no
        ).order_by(AIDefenseTurn.turn_no.desc()).limit(4)).all()
        history = "\n".join(f"问：{t.question[:100]}\n答：{t.student_answer[:200]}" for t in reversed(previous))
        topic = self.db.get(GraduationTopic, session.topic_id)
        if rubric and marking_points(rubric, sources) != rubric:
            raise BusinessException("本题材料与已固定的要点发生变化，请使用最新材料重新生成练习")
        llm = LLMFactory.get()
        if deferred:
            context = {"topic_title": topic.title if topic else "", "question": turn.question, "chunks": sources}
            system = resolve_system_prompt(self.db, "DEFENSE_OFFLINE_GRADE")
            rubric_system = resolve_system_prompt(self.db, "DEFENSE_LEGACY_RUBRIC") if not rubric else None
            self.db.rollback()
            if rubric_system:
                # Older recordings have no anchors. Select them without seeing the answer,
                # and persist under the lease before grading so a retry uses the same criteria.
                rubric_prompt = json.dumps(context, ensure_ascii=False)
                began = datetime.now()
                rubric_raw = llm.complete(rubric_system, rubric_prompt)
                try:
                    draft = extract_json(rubric_raw)
                except (TypeError, ValueError):
                    draft = None
                rubric = marking_points(draft.get("rubric") if isinstance(draft, dict) else None, sources)
                if not rubric:
                    raise BusinessException("没有取得与本轮问题可核对的材料要点", 50302)
                self._claimed(session_id, claim, lock=True)
                turn = self.db.get(AIDefenseTurn, turn_id, populate_existing=True)
                turn.reference_chunks = mark_snapshot(sources, rubric)
                self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_QA", model=llm.name,
                                       input_tokens=len(rubric_prompt), output_tokens=len(rubric_raw),
                                       latency_ms=int((datetime.now() - began).total_seconds() * 1000)))
                self.db.commit()
            prompt = json.dumps({**context, "student_answer": answer, "rubric": rubric,
                                 "previous_turns": history}, ensure_ascii=False)
        else:
            prompt = defense_evaluate_prompt(topic.title if topic else "", turn.question, answer, sources, history, rubric)
            system = resolve_system_prompt(self.db, "DEFENSE_EVALUATE_ANSWER")
        started = datetime.now()
        raw = llm.complete(system, prompt)
        try:
            payload = extract_json(raw)
            evaluation = _Evaluation.model_validate({**payload, "follow_up_needed": False, "follow_up_question": None}
                                                    if deferred else payload)
        except (ValueError, TypeError) as exc:
            raise BusinessException("AI 没有返回完整有效的分项评分，请保持本题并重试", 50302) from exc
        cited = _citations(evaluation.source_ids, {s["chunk_id"]: s for s in sources})
        if not cited:
            raise BusinessException("AI 反馈未引用本题可核对的材料，请重试", 50302)
        assessment = assess_points(payload.get("assessment"), rubric, answer) if rubric else []
        next_rubric = []
        if not deferred and evaluation.follow_up_needed and not turn.is_follow_up:
            next_rubric = marking_points(payload.get("follow_up_rubric"), cited)
            if rubric and not next_rubric:
                raise BusinessException("追问缺少可核对的专属材料要点，请重试", 50302)
            if not oral_question(evaluation.follow_up_question):
                draft, = self._draft_questions(student_id, user_id, topic.title if topic else "", cited, 1,
                                               preceding={"question": turn.question, "answer": answer},
                                               avoid=[turn.question, *(t.question for t in previous)])
                evaluation.follow_up_question = draft.question
                next_rubric = snapshot(draft.reference_chunks)["rubric"]
        # Preserve every immutable marking anchor even when the model cites fewer passages in feedback.
        needed = {i for point in rubric for i in point["source_ids"]}
        cited_ids = {s["chunk_id"] for s in cited}
        cited.extend(s for s in sources if s["chunk_id"] in needed - cited_ids)
        accuracy = accuracy_from_evidence(assessment) if rubric else evaluation.accuracy
        if deferred:
            claimed_session = self._claimed(session_id, claim, lock=True)
            turn = self.db.get(AIDefenseTurn, turn_id, populate_existing=True)
            if turn.total_score is not None:
                return
            claimed_session.report_attempts = 0
            claimed_session.report_claim = claimed_session.report_lease_until = None
        turn.student_answer = answer
        turn.ai_feedback = evaluation.feedback
        turn.accuracy_score = accuracy
        turn.logic_score = evaluation.logic
        turn.clarity_score = evaluation.clarity
        turn.depth_score = evaluation.depth
        turn.total_score = turn.accuracy_score + evaluation.logic + evaluation.clarity + evaluation.depth
        turn.reference_chunks = mark_snapshot(cited, rubric, assessment=assessment, follow_up_rubric=next_rubric)
        if not deferred and evaluation.follow_up_needed and not turn.is_follow_up:
            turn.follow_up_question = evaluation.follow_up_question
        if not deferred and not turn.is_follow_up:
            session.answered_count = (session.answered_count or 0) + 1
        self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_QA", model=llm.name,
                               input_tokens=len(prompt), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))

    def next_question(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.live_request_id:
            return self._live_current(session)
        if session.status == "FINISHED":
            return {"finished": True, "report": json.loads(session.summary)}
        if session.status != "RUNNING":
            raise BusinessException("会话不在进行中")
        last_turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
                                   .order_by(AIDefenseTurn.id.desc()).limit(1))
        if last_turn and last_turn.student_answer is None:
            self._prepare_spoken_turn(session, last_turn)
            self.db.commit()
            return {**self._turn_item(last_turn), "finished": False}
        if last_turn and last_turn.follow_up_question and not last_turn.is_follow_up:
            prior = snapshot(last_turn.reference_chunks)
            turn = AIDefenseTurn(session_id=session_id, question_id=last_turn.question_id,
                                 turn_no=last_turn.turn_no + 1, question=last_turn.follow_up_question,
                                 is_follow_up=1, reference_chunks=mark_snapshot(prior["sources"], prior.get("follow_up_rubric", [])))
            self.db.add(turn)
        else:
            answered = self.db.scalar(select(func.count(AIDefenseTurn.id)).where(
                AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None),
                AIDefenseTurn.is_follow_up == 0))
            turn = self._next_turn(self.db, session) if answered < session.question_count else None
            if turn is None:
                return {"finished": True, "report": self.finish_session(student_id, session_id)}
        self._prepare_spoken_turn(session, turn)
        self.db.commit()
        return {**self._turn_item(turn), "finished": False}

    def finish_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.status == "FINISHED":
            return json.loads(session.summary)
        data = self._compose_report(session)
        session.finished_at = datetime.now()
        session.overall_score = data["overall_score"]
        session.summary = json.dumps(data, ensure_ascii=False)
        transition(self.db, PRACTICE_SESSION, session, "finish", None)
        self.db.commit()
        return data

    def build_live_report(self, session_id: int, claim: str) -> None:
        session = self._claimed(session_id, claim)
        data = self._compose_report(session, deferred=True)
        session = self._claimed(session_id, claim, lock=True)
        session.overall_score = data["overall_score"]
        session.summary = json.dumps(data, ensure_ascii=False)
        session.report_status = "ready"
        session.report_attempts = 0
        session.report_claim = session.report_lease_until = None
        self.db.commit()

    def _compose_report(self, session: AIDefenseSession, *, deferred: bool = False) -> dict:
        student_id, session_id = session.student_id, session.id
        turns = self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None)
        ).order_by(AIDefenseTurn.turn_no)).all()
        if not turns or any(t.total_score is None for t in turns):
            raise BusinessException("当前会话尚无完整的评分记录")
        topic = self.db.get(GraduationTopic, session.topic_id)
        # New practice sessions use a fixed set of main questions for the composite.
        # Adaptive follow-ups retain their own scores without moving the main-question baseline.
        scored = [t for t in turns if not t.is_follow_up] if deferred else turns
        overall_score = round(sum(t.total_score for t in scored) / len(scored))
        fallback = _scored_report(scored)
        if deferred:
            fallback["summary"] = (f"AI 文字总评暂不可用。本次完成 {len(scored)} 道正题、{len(turns) - len(scored)} 道追问。"
                                   "综合分及维度分仅基于正题，追问得分单独展示。")
        records = [{"turn_no": t.turn_no, "question": t.question, "student_answer": t.student_answer[:1100],
                    "feedback": (t.ai_feedback or "")[:500], "total_score": t.total_score,
                    "scores": {"accuracy": t.accuracy_score, "logic": t.logic_score,
                               "clarity": t.clarity_score, "depth": t.depth_score},
                    "sources": [{"document_name": s["document_name"], "section": s["section"]}
                                for s in _stored_sources(t.reference_chunks)]} for t in turns]
        system = resolve_system_prompt(self.db, "DEFENSE_REPORT")
        prompt = defense_report_prompt(topic.title if topic else "", records, overall_score)
        user_id = self.db.scalar(select(StudentProfile.user_id).where(StudentProfile.id == student_id))
        if deferred:
            self.db.rollback()
        started = datetime.now()
        llm, raw = None, ""
        try:
            llm = LLMFactory.get()
            raw = llm.complete(system, prompt)
            data = _Report.model_validate(extract_json(raw)).model_dump()
            data["summary_source"] = "model"
        except (ValueError, TypeError):
            logger.warning("模拟答辩文字总评格式无效，保留逐轮评分汇总 session_id=%s", session_id)
            data = fallback
        except BusinessException as exc:
            if exc.code not in (50301, 50303):
                raise
            logger.warning("模拟答辩文字总评服务不可用 session_id=%s code=%s", session_id, exc.code)
            data = fallback
        if llm is not None:
            self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_REPORT", model=llm.name,
                                   input_tokens=len(prompt), output_tokens=len(raw),
                                   latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        data["overall_score"] = overall_score
        if deferred:
            data["dimension_scores"] = fallback["dimension_scores"]
        return data

    def retry_live_report(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.status != "FINISHED" or session.report_status is None:
            raise BusinessException("没有可以重试的实时答辩报告", 40900)
        if session.report_status == "failed":
            session.report_status = "pending"
            session.report_attempts = 0
            session.report_claim = session.report_lease_until = None
        self.db.commit()
        return {"session_id": session_id, "report_status": session.report_status}

    def get_report(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id)
        if session.status == "FINISHED" and session.report_status in {"pending", "failed"}:
            return {"session_id": session_id, "report_status": session.report_status}
        if session.status != "FINISHED" or not session.summary:
            raise BusinessException("该会话尚未结束, 暂无答辩报告")
        turns = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id,
                                        AIDefenseTurn.student_answer.is_not(None)).order_by(AIDefenseTurn.id)
        ).scalars().all()
        report = json.loads(session.summary)
        if session.report_status == "ready":
            report["report_status"] = "ready"
            report["score_basis"] = "main_questions"
        report["turns"] = [self._turn_item(t) for t in turns]
        report["observation_counts"] = self._observation_counts(session.id)
        report["session_id"] = session.id
        report["overall_score"] = session.overall_score
        report["finished_at"] = session.finished_at.isoformat() if session.finished_at else None
        return report

    def list_sessions(self, student_id: int) -> list[dict]:
        sessions = self.db.execute(
            select(AIDefenseSession).where(AIDefenseSession.student_id == student_id)
            .order_by(AIDefenseSession.id.desc())
        ).scalars().all()
        return [{
            "id": s.id, "status": s.status, "question_count": s.question_count,
            "answered_count": s.answered_count, "overall_score": s.overall_score, "report_status": s.report_status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        } for s in sessions]

    def session_detail(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id)
        turns = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id).order_by(AIDefenseTurn.id)
        ).scalars().all()
        return {
            "id": session.id, "status": session.status, "question_count": session.question_count,
            "live": bool(session.live_request_id),
            "answered_count": session.answered_count,
            "turns": [self._turn_item(t) for t in turns],
        }

    # ---------- PPT 大纲 ----------

    @staticmethod
    def _fallback_ppt_outline(topic_title: str) -> dict:
        """PPT 大纲的固定模板兜底(D48): 保证接口永远返回可展示结构."""
        return {
            "title": f"{topic_title} 毕业设计答辩",
            "pages": [
                {"page": 1, "title": "封面", "goal": "呈现题目与个人信息",
                 "key_points": [topic_title, "姓名 / 指导教师 / 日期"], "speaker_notes": "开场自我介绍",
                 "recommended_visual": "校徽+标题"},
                {"page": 2, "title": "研究背景与意义", "goal": "说明选题来源与研究价值",
                 "key_points": ["行业现状与痛点", "研究价值"], "speaker_notes": "用例子说明痛点",
                 "recommended_visual": "背景图"},
                {"page": 3, "title": "系统总体架构", "goal": "讲清系统分层与组件职责",
                 "key_points": ["前端 Vue3", "后端 FastAPI", "MySQL + Redis"], "speaker_notes": "按调用链讲一遍",
                 "recommended_visual": "架构图"},
                {"page": 4, "title": "核心功能实现", "goal": "展示核心业务流程的技术方案",
                 "key_points": ["毕业流程状态机", "审核工作流", "进度跟踪"], "speaker_notes": "重点讲状态机设计",
                 "recommended_visual": "流程图"},
                {"page": 5, "title": "系统测试", "goal": "用数据证明功能达标",
                 "key_points": ["功能测试用例", "测试结果"], "speaker_notes": "给出关键指标",
                 "recommended_visual": "表格"},
                {"page": 6, "title": "总结与展望", "goal": "收束工作并指出改进方向",
                 "key_points": ["完成工作", "不足与改进"], "speaker_notes": "致谢",
                 "recommended_visual": "简洁文字"},
            ],
        }

    def generate_ppt_outline(self, student_id: int, user_id: int) -> dict:
        topic = self._get_topic(student_id)
        chunks = self.rag.retrieve("系统架构 设计 实现 测试 创新", student_id, top_k=8)
        context = self.rag.build_context(chunks)
        last_session = self.db.execute(
            select(AIDefenseSession).where(AIDefenseSession.student_id == student_id,
                                           AIDefenseSession.status == "FINISHED",
                                           AIDefenseSession.summary.is_not(None))
            .order_by(AIDefenseSession.id.desc())
        ).scalars().first()
        defense_summary = ""
        if last_session and last_session.summary:
            try:
                report = json.loads(last_session.summary)
                defense_summary = f"综合得分{report.get('overall_score')}; 薄弱点: {'; '.join(report.get('weaknesses', [])[:3])}"
            except json.JSONDecodeError:
                pass
        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(resolve_system_prompt(self.db, "PPT_OUTLINE"), ppt_outline_prompt(topic.title, context, defense_summary))
        try:
            data = extract_json(raw)
        except ValueError:
            # D48: 同问题生成, PPT 大纲解析失败回落固定模板
            logger.warning("PPT 大纲返回非 JSON, 已回落固定模板: %s", raw[:200])
            data = self._fallback_ppt_outline(topic.title)
        self.db.add(AIUsageLog(user_id=user_id, scene="PPT_OUTLINE", model=llm.name,
                               input_tokens=len(context), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()
        return data
