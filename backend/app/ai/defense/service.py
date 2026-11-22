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
from app.ai.rag.retriever import RetrievedChunk
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
                                         TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
        ).scalar_one_or_none() if cycle else None
        if sel is None:
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

    def generate_questions(self, student_id: int, user_id: int, document_ids: list[int] | None = None,
                           question_count: int | None = None) -> list[dict]:
        topic = self._get_topic(student_id)
        sources = self._generation_sources(student_id, document_ids)
        requested = question_count or 22
        prompt = defense_question_prompt(topic.title, sources, requested)
        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(resolve_system_prompt(self.db, "DEFENSE_GENERATE_QUESTIONS"), prompt)
        try:
            data = extract_json(raw)
        except ValueError:
            data = {}
        supplied = data.get("questions") if isinstance(data, dict) else None
        by_id = {s["chunk_id"]: s for s in sources}
        rows = []
        seen = set()
        for candidate in supplied if isinstance(supplied, list) else []:
            if not isinstance(candidate, dict):
                continue
            question = candidate.get("question")
            cited = _citations(candidate.get("source_ids"), by_id)
            if not isinstance(question, str) or not question.strip() or not cited:
                continue
            normalized = _normalize_question(question)
            if normalized in seen:
                continue
            rubric = marking_points(candidate.get("rubric"), cited)
            if not rubric:
                continue
            seen.add(normalized)
            qtype = candidate.get("question_type")
            difficulty = candidate.get("difficulty")
            rows.append(AIDefenseQuestion(student_id=student_id, question=question.strip()[:1200],
                                          question_type=qtype if qtype in tuple(QuestionType) else "BASIC",
                                          difficulty=difficulty if difficulty in ("EASY", "MEDIUM", "HARD") else "MEDIUM",
                                          reference_chunks=mark_snapshot(cited, rubric),
                                          reference_answer="；".join(p["expectation"] for p in rubric)))
            if len(rows) == requested:
                break
        if question_count is not None and len(rows) < requested:
            raise BusinessException(f"本次模型返回了 {len(rows)} 道可核对且不重复的题目，未达到所选 {requested} 道。"
                                    "请重试、减少题数或补充材料。", 50302)
        has_generated_questions = bool(rows)
        # 当模型没有给出可核对的题目，使用实际选段发问；确保代码和书面报告各有入口。
        available = {s["document_type"] == "SOURCE_CODE" for s in sources}
        covered = {s["document_type"] == "SOURCE_CODE" for r in rows for s in _stored_sources(r.reference_chunks)}
        for code in sorted(available - covered):
            source = next(s for s in sources if (s["document_type"] == "SOURCE_CODE") == code)
            anchor = source["section"] or source["document_name"]
            rubric = marking_points([{"expectation": source["excerpt"][:160], "source_ids": [source["chunk_id"]]}], [source])
            if not rubric:
                continue
            angles = (("IMPLEMENTATION", "这段逻辑解决了什么问题"), ("TEST", "如何验证这段逻辑的边界情况"),
                      ("LIMITATION", "这段实现在哪种情况下需要修改")) if code else (
                ("DESIGN", "这一设计在整体架构中承担什么职责"), ("TEST", "如何验证这一设计满足预期"),
                ("LIMITATION", "哪些输入或使用场景可能影响预期结果"))
            for qtype, angle in angles[:1] if has_generated_questions else angles:
                rows.insert(0, AIDefenseQuestion(student_id=student_id, question=f"请先概述《{anchor}》所引片段的内容，再说明{angle}？",
                                                  question_type=qtype, difficulty="MEDIUM",
                                                  reference_chunks=mark_snapshot([source], rubric),
                                                  reference_answer=rubric[0]["expectation"]))
        # 把两类材料的代表题排在前面，即使只练习三题也能覆盖实现与设计。
        representatives = []
        for code in sorted(available):
            representative = next((r for r in rows if any(
                (s["document_type"] == "SOURCE_CODE") == code for s in _stored_sources(r.reference_chunks))), None)
            if representative is not None and representative not in representatives:
                representatives.append(representative)
        rows = (representatives + [r for r in rows if r not in representatives])[:requested]
        if not rows:
            raise BusinessException("材料片段不足以形成可核对的评分要点，请补充报告或源码后重试", 50302)
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
        self.db.add(AIUsageLog(user_id=user_id, scene="GENERATE_QUESTIONS", model=llm.name,
                               input_tokens=len(prompt), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
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

    def start_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        transition(self.db, PRACTICE_SESSION, session, "start", None)
        session.started_at = datetime.now()
        turn = self._next_turn(self.db, session)
        if turn is None:
            raise BusinessException("没有可用的问题")
        self.db.commit()
        return {"session_id": session.id, **self._turn_item(turn)}

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
        if session.status != "RUNNING":
            raise BusinessException("会话不在进行中")
        answer = answer.strip()
        turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
                              .order_by(AIDefenseTurn.id.desc()).limit(1))
        if turn is None or _normalize_question(question) != _normalize_question(turn.question):
            raise BusinessException("提交的题目与当前轮次不一致，请刷新后重试", 40003)
        if len(answer) < 5:
            raise BusinessException("请至少输入五个字符的回答")
        if turn.student_answer is not None:
            if answer == turn.student_answer:
                return {**self._turn_item(turn), "feedback": turn.ai_feedback, "finished": False}
            raise BusinessException("此轮已评分，请查看反馈并继续下一题", 40900)

        pinned = _stored_sources(turn.reference_chunks)
        if pinned:
            hits = self.rag.practice_chunks(student_id, [s["chunk_id"] for s in pinned])
            # 重新索引会产生新 chunk；不把旧快照当作仍然可用的引用继续评分。
            if len(hits) != len(pinned):
                raise BusinessException("本题的材料已更新或移除，请使用最新材料重新生成练习")
        else:
            # 兼容升级前已在进行中的练习。无已授权的真实材料时保留待答状态。
            hits = self.rag.retrieve_practice(turn.question, student_id, top_k=4)
        if not hits:
            raise BusinessException("缺少可核对的报告或源码，请先完成材料索引再作答")
        sources = [_source(hit) for hit in hits]
        docs = list(dict.fromkeys(hit.document_id for hit in hits))
        further = self.rag.retrieve_practice(turn.question + " " + answer[:240], student_id, top_k=3, document_ids=docs)
        seen = {s["chunk_id"] for s in sources}
        sources.extend(_source(hit) for hit in further if hit.chunk_id not in seen)
        previous = self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None)
        ).order_by(AIDefenseTurn.id.desc()).limit(4)).all()
        history = "\n".join(f"问：{t.question[:100]}\n答：{t.student_answer[:200]}" for t in reversed(previous))
        topic = self.db.get(GraduationTopic, session.topic_id)
        rubric = snapshot(turn.reference_chunks).get("rubric", [])
        if rubric and marking_points(rubric, sources) != rubric:
            raise BusinessException("本题材料与已固定的要点发生变化，请使用最新材料重新生成练习")
        prompt = defense_evaluate_prompt(topic.title if topic else "", turn.question, answer, sources, history, rubric)
        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(resolve_system_prompt(self.db, "DEFENSE_EVALUATE_ANSWER"), prompt)
        try:
            payload = extract_json(raw)
            evaluation = _Evaluation.model_validate(payload)
        except (ValueError, TypeError) as exc:
            raise BusinessException("AI 没有返回完整有效的分项评分，请保持本题并重试", 50302) from exc
        cited = _citations(evaluation.source_ids, {s["chunk_id"]: s for s in sources})
        if not cited:
            raise BusinessException("AI 反馈未引用本题可核对的材料，请重试", 50302)
        assessment = assess_points(payload.get("assessment"), rubric, answer) if rubric else []
        next_rubric = []
        if evaluation.follow_up_needed and not turn.is_follow_up:
            next_rubric = marking_points(payload.get("follow_up_rubric"), cited)
            if rubric and not next_rubric:
                raise BusinessException("追问缺少可核对的专属材料要点，请重试", 50302)
        # Preserve every immutable marking anchor even when the model cites fewer passages in feedback.
        needed = {i for point in rubric for i in point["source_ids"]}
        cited_ids = {s["chunk_id"] for s in cited}
        cited.extend(s for s in sources if s["chunk_id"] in needed - cited_ids)
        accuracy = accuracy_from_evidence(assessment) if rubric else evaluation.accuracy
        turn.student_answer = answer
        turn.ai_feedback = evaluation.feedback
        turn.accuracy_score = accuracy
        turn.logic_score = evaluation.logic
        turn.clarity_score = evaluation.clarity
        turn.depth_score = evaluation.depth
        turn.total_score = turn.accuracy_score + evaluation.logic + evaluation.clarity + evaluation.depth
        turn.reference_chunks = mark_snapshot(cited, rubric, assessment=assessment, follow_up_rubric=next_rubric)
        if evaluation.follow_up_needed and not turn.is_follow_up:
            turn.follow_up_question = evaluation.follow_up_question
        if not turn.is_follow_up:
            session.answered_count = (session.answered_count or 0) + 1
        self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_QA", model=llm.name,
                               input_tokens=len(prompt), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()
        return {**self._turn_item(turn), "feedback": turn.ai_feedback, "finished": False}

    def next_question(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.status == "FINISHED":
            return {"finished": True, "report": json.loads(session.summary)}
        if session.status != "RUNNING":
            raise BusinessException("会话不在进行中")
        last_turn = self.db.scalar(select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
                                   .order_by(AIDefenseTurn.id.desc()).limit(1))
        if last_turn and last_turn.student_answer is None:
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
        self.db.commit()
        return {**self._turn_item(turn), "finished": False}

    def finish_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id, lock=True)
        if session.status == "FINISHED":
            return json.loads(session.summary)
        turns = self.db.scalars(select(AIDefenseTurn).where(
            AIDefenseTurn.session_id == session_id, AIDefenseTurn.student_answer.is_not(None)
        ).order_by(AIDefenseTurn.id)).all()
        if not turns or any(t.total_score is None for t in turns):
            raise BusinessException("当前会话尚无完整的评分记录")
        topic = self.db.get(GraduationTopic, session.topic_id)
        overall_score = round(sum(t.total_score for t in turns) / len(turns))
        records = [{"turn_no": t.turn_no, "question": t.question, "student_answer": t.student_answer[:1100],
                    "feedback": (t.ai_feedback or "")[:500], "total_score": t.total_score,
                    "scores": {"accuracy": t.accuracy_score, "logic": t.logic_score,
                               "clarity": t.clarity_score, "depth": t.depth_score},
                    "sources": [{"document_name": s["document_name"], "section": s["section"]}
                                for s in _stored_sources(t.reference_chunks)]} for t in turns]
        system = resolve_system_prompt(self.db, "DEFENSE_REPORT")
        prompt = defense_report_prompt(topic.title if topic else "", records, overall_score)
        started = datetime.now()
        llm, raw = None, ""
        try:
            llm = LLMFactory.get()
            raw = llm.complete(system, prompt)
            data = _Report.model_validate(extract_json(raw)).model_dump()
            data["summary_source"] = "model"
        except (ValueError, TypeError):
            logger.warning("模拟答辩文字总评格式无效，保留逐轮评分汇总 session_id=%s", session_id)
            data = _scored_report(turns)
        except BusinessException as exc:
            if exc.code not in (50301, 50303):
                raise
            logger.warning("模拟答辩文字总评服务不可用 session_id=%s code=%s", session_id, exc.code)
            data = _scored_report(turns)
        if llm is not None:
            user_id = self.db.scalar(select(StudentProfile.user_id).where(StudentProfile.id == student_id))
            self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_REPORT", model=llm.name,
                                   input_tokens=len(prompt), output_tokens=len(raw),
                                   latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        data["overall_score"] = overall_score
        session.finished_at = datetime.now()
        session.overall_score = overall_score
        session.summary = json.dumps(data, ensure_ascii=False)
        transition(self.db, PRACTICE_SESSION, session, "finish", None)
        self.db.commit()
        return data

    def get_report(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id)
        if session.status != "FINISHED" or not session.summary:
            raise BusinessException("该会话尚未结束, 暂无答辩报告")
        turns = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id,
                                        AIDefenseTurn.student_answer.is_not(None)).order_by(AIDefenseTurn.id)
        ).scalars().all()
        report = json.loads(session.summary)
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
            "answered_count": s.answered_count, "overall_score": s.overall_score,
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
                                           AIDefenseSession.status == "FINISHED")
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
