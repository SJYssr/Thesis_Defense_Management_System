"""DefenseService: AI 模拟答辩核心(roadmap 四十~四十八章).

流程: 生成问题库 → 创建Session → 逐题问答(RAG评价+动态追问) → 答辩报告 → PPT大纲.
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.prompts import (
    DEFENSE_EVALUATE_SYSTEM,
    DEFENSE_QUESTION_SYSTEM,
    DEFENSE_REPORT_SYSTEM,
    PPT_OUTLINE_SYSTEM,
    defense_evaluate_prompt,
    defense_question_prompt,
    defense_report_prompt,
    ppt_outline_prompt,
)
from app.ai.rag.rag_service import RAGService
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.models.ai import (
    AIDefenseQuestion,
    AIDefenseSession,
    AIDefenseTurn,
    AIUsageLog,
)
from app.models.business import GraduationTopic
from app.models.enums import QuestionType, QUESTION_GENERATION_PLAN


class DefenseService:
    def __init__(self, db: Session):
        self.db = db
        self.rag = RAGService(db)

    # ---------- 前置: 学生的题目 ----------

    def _get_topic(self, student_id: int) -> GraduationTopic:
        from app.models.business import TopicSelection

        sel = self.db.execute(
            select(TopicSelection).where(TopicSelection.student_id == student_id,
                                         TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
        ).scalar_one_or_none()
        if sel is None:
            raise BusinessException("尚未通过选题, 无法使用AI模拟答辩")
        topic = self.db.get(GraduationTopic, sel.topic_id)
        if topic is None:
            raise ResourceNotFoundException("题目不存在")
        return topic

    def _has_knowledge(self, student_id: int) -> bool:
        from sqlalchemy import func

        from app.models.ai import AIDocumentChunk

        count = self.db.execute(
            select(func.count(AIDocumentChunk.id)).where(AIDocumentChunk.student_id == student_id)
        ).scalar()
        return (count or 0) > 0

    # ---------- 问题生成 ----------

    def generate_questions(self, student_id: int, user_id: int) -> list[dict]:
        topic = self._get_topic(student_id)
        if not self._has_knowledge(student_id):
            raise BusinessException("知识库为空: 请先上传毕业论文, 等待索引完成后再生成问题")

        # 按维度检索论文内容作为生成依据
        contexts = []
        for qt in QUESTION_GENERATION_PLAN:
            chunks = self.rag.retrieve(self._dim_hint(qt), student_id, top_k=3)
            contexts.extend(chunks)
        context = self.rag.build_context(contexts[:12])

        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(
            DEFENSE_QUESTION_SYSTEM,
            defense_question_prompt(topic.title, context),
        )
        data = extract_json(raw)
        questions_data = data.get("questions") or []

        if not questions_data:  # 兜底: 固定模板问题
            questions_data = [
                {"question": f"请介绍「{topic.title}」的整体架构与模块划分。", "question_type": "DESIGN", "difficulty": "MEDIUM"},
                {"question": "系统采用了哪些核心技术? 分别解决什么问题?", "question_type": "TECHNOLOGY", "difficulty": "MEDIUM"},
                {"question": "数据库是如何设计的? 有哪些核心表?", "question_type": "IMPLEMENTATION", "difficulty": "MEDIUM"},
            ]

        # 覆盖旧的未使用问题
        olds = self.db.execute(
            select(AIDefenseQuestion).where(AIDefenseQuestion.student_id == student_id, AIDefenseQuestion.used == 0)
        ).scalars().all()
        for old in olds:
            self.db.delete(old)

        items = []
        for q in questions_data:
            qtype = q.get("question_type", "BASIC")
            try:
                QuestionType(qtype)
            except ValueError:
                qtype = "BASIC"
            row = AIDefenseQuestion(
                student_id=student_id, question=q.get("question", ""),
                question_type=qtype, difficulty=q.get("difficulty", "MEDIUM"),
                reference_answer=q.get("reference"),
            )
            self.db.add(row)
            items.append({"id": row.id, "question": row.question, "question_type": row.question_type,
                          "difficulty": row.difficulty})
        self.db.add(AIUsageLog(user_id=user_id, scene="GENERATE_QUESTIONS", model=llm.name,
                               input_tokens=len(context), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()
        return items

    @staticmethod
    def _dim_hint(qt: QuestionType) -> str:
        hints = {
            QuestionType.BASIC: "系统背景 目标 功能概述",
            QuestionType.TECHNOLOGY: "技术选型 框架 中间件",
            QuestionType.DESIGN: "系统架构 模块设计 流程设计",
            QuestionType.IMPLEMENTATION: "实现细节 核心功能 代码",
            QuestionType.DATABASE: "数据库设计 表结构",
            QuestionType.TEST: "系统测试 测试用例",
            QuestionType.INNOVATION: "创新点 特色",
            QuestionType.LIMITATION: "不足 局限 问题",
            QuestionType.EXTENSION: "展望 扩展 未来工作",
        }
        return hints.get(qt, "系统设计")

    # ---------- 会话管理 ----------

    def create_session(self, student_id: int, question_count: int = 8) -> dict:
        topic = self._get_topic(student_id)
        available = self.db.execute(
            select(AIDefenseQuestion).where(AIDefenseQuestion.student_id == student_id, AIDefenseQuestion.used == 0)
        ).scalars().all()
        if not available:
            raise BusinessException("请先生成答辩问题库")

        session = AIDefenseSession(student_id=student_id, topic_id=topic.id,
                                   status="CREATED", question_count=min(question_count, len(available)))
        self.db.add(session)
        self.db.commit()
        return {"session_id": session.id, "question_count": session.question_count,
                "available_questions": len(available)}

    def start_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id)
        if session.status not in ("CREATED",):
            raise BusinessException("会话已开始或已结束")
        session.status = "RUNNING"
        session.started_at = datetime.now()
        turn = self._next_turn(self.db, session)
        if turn is None:
            raise BusinessException("没有可用的问题")
        self.db.commit()
        question_type = None
        if turn.question_id:
            q = self.db.get(AIDefenseQuestion, turn.question_id)
            question_type = q.question_type if q else None
        return {"session_id": session.id, "turn_no": turn.turn_no, "question": turn.question,
                "question_type": question_type}

    def _own_session(self, student_id: int, session_id: int) -> AIDefenseSession:
        session = self.db.get(AIDefenseSession, session_id)
        if session is None or session.student_id != student_id:
            raise ResourceNotFoundException("答辩会话不存在")
        return session

    def _next_turn(self, db: Session, session: AIDefenseSession) -> AIDefenseTurn | None:
        """从未使用问题中选取下一题(优先选择未覆盖的类型)."""
        used_types = db.execute(
            select(AIDefenseTurn.question).where(AIDefenseTurn.session_id == session.id)
        ).scalars().all()
        candidates = db.execute(
            select(AIDefenseQuestion).where(AIDefenseQuestion.student_id == session.student_id,
                                            AIDefenseQuestion.used == 0)
        ).scalars().all()
        if not candidates:
            return None
        # 简单策略: 先按类型多样性选, 再按id顺序
        picked = None
        seen_types = set()
        existing = db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session.id)
        ).scalars().all()
        # 找出该会话已问过的题目文本
        asked = {t.question for t in existing}
        for c in candidates:
            if c.question in asked:
                continue
            if c.question_type not in seen_types:
                picked = c
                break
        if picked is None:
            for c in candidates:
                if c.question not in asked:
                    picked = c
                    break
        if picked is None:
            return None
        picked.used = 1
        turn_no = len(existing) + 1
        turn = AIDefenseTurn(session_id=session.id, question_id=picked.id, turn_no=turn_no,
                             question=picked.question)
        turn.is_follow_up = 0
        # 将 question_type 存到 reference_chunks 字段冗余? 不, 用 question_id 关联查询
        db.add(turn)
        return turn

    # ---------- 回答评价 ----------

    def evaluate_answer(self, student_id: int, session_id: int, question: str, answer: str,
                        user_id: int) -> dict:
        """评价学生回答: RAG检索论文原文 → 结构化评分 → 动态追问判定(roadmap 四十六)."""
        session = self._own_session(student_id, session_id)
        if session.status != "RUNNING":
            raise BusinessException("会话不在进行中")

        topic = self.db.get(GraduationTopic, session.topic_id)
        topic_title = topic.title if topic else ""

        # 检索论文依据
        history = [{"role": "user", "content": question}, ]
        chunks = self.rag.retrieve(question, student_id, top_k=5)
        context = self.rag.build_context(chunks)

        # 之前轮次概要(最近6轮, roadmap 三十九章裁剪策略)
        prev_turns = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
            .order_by(AIDefenseTurn.id.desc()).limit(6)
        ).scalars().all()
        summary = " ".join(f"Q:{t.question[:40]} A:{(t.student_answer or '')[:60]}" for t in reversed(prev_turns))

        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(
            DEFENSE_EVALUATE_SYSTEM,
            defense_evaluate_prompt(topic_title, question, answer, context, summary),
        )
        try:
            data = extract_json(raw)
        except ValueError:
            data = {"accuracy": 22, "logic": 16, "clarity": 14, "depth": 12,
                    "feedback": raw[:800], "follow_up_needed": False, "follow_up_question": None}

        # 记录 turn: 找当前未回答的最新turn, 或新建
        turn = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id,
                                        AIDefenseTurn.student_answer.is_(None))
            .order_by(AIDefenseTurn.id.desc())
        ).scalars().first()
        if turn is None:
            turn = AIDefenseTurn(session_id=session_id, turn_no=len(prev_turns) + 1, question=question)
            self.db.add(turn)
        turn.student_answer = answer
        turn.ai_feedback = data.get("feedback", "")
        turn.accuracy_score = int(data.get("accuracy", 0))
        turn.logic_score = int(data.get("logic", 0))
        turn.clarity_score = int(data.get("clarity", 0))
        turn.depth_score = int(data.get("depth", 0))
        turn.total_score = turn.accuracy_score + turn.logic_score + turn.clarity_score + turn.depth_score
        turn.reference_chunks = json.dumps([c.chunk_id for c in chunks])
        # 追问仅一层: 追问轮不再追问(roadmap 四十六: 一到两轮)
        follow_up_needed = bool(data.get("follow_up_needed")) and not turn.follow_up_question and not turn.is_follow_up
        if follow_up_needed and data.get("follow_up_question"):
            turn.follow_up_question = data["follow_up_question"]
        from sqlalchemy import func

        session.answered_count = self.db.execute(
            select(func.count(AIDefenseTurn.id)).where(AIDefenseTurn.session_id == session_id,
                                                       AIDefenseTurn.student_answer.is_not(None))
        ).scalar()
        self.db.add(AIUsageLog(user_id=user_id, scene="DEFENSE_QA", model=llm.name,
                               input_tokens=len(context), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()

        result = {
            "turn_no": turn.turn_no,
            "scores": {"accuracy": turn.accuracy_score, "logic": turn.logic_score,
                       "clarity": turn.clarity_score, "depth": turn.depth_score,
                       "total": turn.total_score},
            "feedback": turn.ai_feedback,
            "sources": [{"chunk_id": c.chunk_id, "section": c.section_title,
                         "document_type": c.document_type} for c in chunks],
            "finished": False,
        }
        return result

    def next_question(self, student_id: int, session_id: int) -> dict:
        """下一题: 优先追问, 否则新问题; 题目用尽或达到设定数量则结束并生成报告."""
        session = self._own_session(student_id, session_id)
        if session.status != "RUNNING":
            raise BusinessException("会话不在进行中")

        last_turn = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id)
            .order_by(AIDefenseTurn.id.desc())
        ).scalars().first()

        # 追问轮: 上一轮有 follow_up_question 且未被回答过追问
        if last_turn and last_turn.follow_up_question and not last_turn.is_follow_up:
            turn = AIDefenseTurn(session_id=session_id, question_id=last_turn.question_id,
                                 turn_no=last_turn.turn_no + 1, question=last_turn.follow_up_question,
                                 is_follow_up=1)
            self.db.add(turn)
            self.db.commit()
            return {"turn_no": turn.turn_no, "question": turn.question, "is_follow_up": True,
                    "finished": False}

        total_answered = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id,
                                        AIDefenseTurn.student_answer.is_not(None))
        ).scalars().all()
        if len(total_answered) >= (session.question_count or 8):
            report = self.finish_session(student_id, session_id)
            return {"finished": True, "report": report}

        turn = self._next_turn(self.db, session)
        if turn is None:
            report = self.finish_session(student_id, session_id)
            return {"finished": True, "report": report}
        self.db.commit()
        question_type = None
        if turn.question_id:
            q = self.db.get(AIDefenseQuestion, turn.question_id)
            question_type = q.question_type if q else None
        return {"turn_no": turn.turn_no, "question": turn.question, "is_follow_up": False,
                "question_type": question_type, "finished": False}

    # ---------- 报告与结束 ----------

    def finish_session(self, student_id: int, session_id: int) -> dict:
        session = self._own_session(student_id, session_id)
        turns = self.db.execute(
            select(AIDefenseTurn).where(AIDefenseTurn.session_id == session_id,
                                        AIDefenseTurn.student_answer.is_not(None))
            .order_by(AIDefenseTurn.id)
        ).scalars().all()
        topic = self.db.get(GraduationTopic, session.topic_id)
        topic_title = topic.title if topic else ""

        llm = LLMFactory.get()
        turns_data = [{"question": t.question, "student_answer": t.student_answer, "total_score": t.total_score}
                      for t in turns]
        try:
            data = extract_json(llm.complete(DEFENSE_REPORT_SYSTEM, defense_report_prompt(topic_title, turns_data)))
        except ValueError:
            avg = int(sum(t.total_score or 0 for t in turns) / max(len(turns), 1))
            data = {"overall_score": avg, "summary": "报告生成失败, 已使用平均分兜底。",
                    "dimension_scores": {}, "strengths": [], "weaknesses": [], "risky_questions": [], "suggestions": []}

        session.status = "FINISHED"
        session.finished_at = datetime.now()
        session.overall_score = data.get("overall_score")
        session.summary = json.dumps(data, ensure_ascii=False)
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
        report["turns"] = [{
            "turn_no": t.turn_no, "question": t.question, "student_answer": t.student_answer,
            "ai_feedback": t.ai_feedback, "total_score": t.total_score,
            "scores": {"accuracy": t.accuracy_score, "logic": t.logic_score, "clarity": t.clarity_score, "depth": t.depth_score},
        } for t in turns]
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
            "turns": [{
                "turn_no": t.turn_no, "question": t.question, "student_answer": t.student_answer,
                "ai_feedback": t.ai_feedback, "is_follow_up": bool(t.is_follow_up),
                "total_score": t.total_score,
            } for t in turns],
        }

    # ---------- PPT 大纲 ----------

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
        raw = llm.complete(PPT_OUTLINE_SYSTEM, ppt_outline_prompt(topic.title, context, defense_summary))
        data = extract_json(raw)
        self.db.add(AIUsageLog(user_id=user_id, scene="PPT_OUTLINE", model=llm.name,
                               input_tokens=len(context), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()
        return data
