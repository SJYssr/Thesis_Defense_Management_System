"""教师 AI 毕设进度分析: 规则引擎结论 + 材料文本 → LLM 解释与建议(roadmap 四十九~五十一章).

进度/风险由确定性规则计算(ProgressService), LLM 只负责解释与材料质量分析.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.prompts import PROGRESS_ANALYSIS_SYSTEM, progress_analysis_prompt
from app.models.ai import AIUsageLog
from app.core.exceptions import BusinessException
from app.models.business import (
    FinalDefense,
    GraduationMaterial,
    GraduationTopic,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.enums import ProcessStage
from app.models.system import ReviewRecord
from app.shared.progress import ProgressService, STAGE_LABELS
from app.shared.workflow import GraduationWorkflowService


class ProgressAnalysisService:
    def __init__(self, db: Session):
        self.db = db

    def analyse(self, student_id: int, user_id: int) -> dict:
        # 1. 规则引擎先行(确定性)
        ProgressService.recompute(self.db, student_id)
        self.db.commit()
        process = GraduationWorkflowService.get_process(self.db, student_id)
        if process is None:
            raise BusinessException("该学生尚未开始毕业设计流程(未通过选题)")

        stage_cn = STAGE_LABELS.get(ProcessStage(process.current_stage), process.current_stage)
        snapshot = {
            "current_stage": process.current_stage,
            "current_stage_cn": stage_cn,
            "progress_percent": process.progress_percent,
            "rule_risk": process.risk_level,
            "rule_reason": process.risk_reason,
            "deadline_info": ProgressService.stage_deadline_info(self.db, process.current_stage),
        }

        # 2. 聚合材料文本
        materials = self._collect_materials(student_id)
        topic = self.db.get(GraduationTopic, process.topic_id) if process.topic_id else None
        if topic:
            materials = f"题目: {topic.title}\n要求: {(topic.requirements or '')[:500]}\n\n" + materials

        # 3. LLM 解释
        llm = LLMFactory.get()
        started = datetime.now()
        raw = llm.complete(PROGRESS_ANALYSIS_SYSTEM, progress_analysis_prompt(snapshot, materials[:6000]))
        try:
            data = extract_json(raw)
        except ValueError:
            data = {"risk_level": process.risk_level, "summary": raw[:500],
                    "problems": [], "suggestions": [], "recommended_reminder": ""}

        # 规则引擎结论覆盖 AI 风险等级, 防止 LLM 随意改判
        data["rule_snapshot"] = snapshot
        data["risk_level"] = process.risk_level if process.risk_level == "HIGH" else data.get("risk_level", process.risk_level)
        self.db.add(AIUsageLog(user_id=user_id, scene="PROGRESS_ANALYSIS", model=llm.name,
                               input_tokens=len(materials), output_tokens=len(raw),
                               latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
        self.db.commit()
        return data

    def _collect_materials(self, student_id: int) -> str:
        from app.shared.workflow import GraduationWorkflowService

        parts: list[str] = []
        snap = GraduationWorkflowService.get_stage_snapshot(self.db, student_id)
        if snap["opening"]:
            opening = snap["opening"]
            parts.append(f"[开题报告·{opening.status}]\n{opening.research_content or ''}\n{opening.technical_route or ''}")
        if snap["midterm"]:
            midterm = snap["midterm"]
            parts.append(f"[中期报告·{midterm.status}]\n{(midterm.completed_content or '')[:800]}\n问题: {(midterm.existing_problems or '')[:300]}")
        if snap["final"]:
            final = snap["final"]
            parts.append(f"[终期材料·{final.status}]\n{(final.summary or '')[:800]}\n测试: {(final.test_result or '')[:300]}")

        rejects = self.db.execute(
            select(ReviewRecord).where(ReviewRecord.student_id == student_id,
                                       ReviewRecord.action.in_(["REJECT", "RETURN"]))
            .order_by(ReviewRecord.id.desc()).limit(5)
        ).scalars().all()
        if rejects:
            parts.append("[近期驳回意见]\n" + "\n".join(f"- {r.comment}" for r in rejects if r.comment))
        return "\n\n".join(p for p in parts if p.strip()) or "(暂无任何已提交材料)"
