"""AI 模块路由: 文档索引/RAG检索/AI模拟答辩/PPT大纲/进度分析/SSE流式问答(roadmap 六十五章)."""
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher_or_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.db.redis import RedisKeys, redis_client
from app.ai.defense.service import DefenseService
from app.ai.progress.service import ProgressAnalysisService
from app.ai.rag.rag_service import RAGService
from app.core.config import settings
from app.models.ai import AIDocument
from app.models.enums import AIDocumentType

router = APIRouter(prefix="/ai", tags=["AI"])

STATUS_CN = {"WAITING": "待处理", "PROCESSING": "处理中", "SUCCESS": "已完成", "FAILED": "失败"}


def _check_ai_rate(user: CurrentUser, db: Session) -> None:
    """AI 总开关 + 每分钟限流, 阈值均来自系统配置(roadmap 七十七/八十五章)."""
    from app.shared.sysconfig import get_all

    cfg = get_all(db)
    if cfg.get("ai_defense_enabled") != "true":
        raise BusinessException("AI 功能已由管理员暂时关闭", 40300)
    try:
        limit = int(cfg.get("ai_rate_limit_per_minute") or settings.ai_rate_limit_per_minute)
    except ValueError:
        limit = settings.ai_rate_limit_per_minute
    key = RedisKeys.RATE_AI.format(user_id=user.id)
    current = redis_client.incr(key)
    if current == 1:
        redis_client.expire(key, 60)
    if current > limit:
        raise BusinessException(f"AI 接口调用过于频繁(每分钟限 {limit} 次)", 42901)


# ---------- 文档索引 ----------

@router.get("/documents")
def list_documents(_: CurrentUser = Depends(require_student), db: Session = Depends(get_db), user: CurrentUser = Depends(require_student)):
    from app.models.system import SysFile

    rows = db.execute(
        select(AIDocument, SysFile.size)
        .outerjoin(SysFile, AIDocument.file_id == SysFile.id)
        .where(AIDocument.student_id == user.profile_id).order_by(AIDocument.id.desc())
    ).all()
    return ResponseUtil.success([{
        "id": d.id, "document_name": d.document_name, "document_type": d.document_type,
        "index_status": d.index_status, "status_cn": STATUS_CN.get(d.index_status, d.index_status),
        "chunk_count": d.chunk_count, "version": d.version, "error_message": d.error_message,
        "file_size": size, "file_id": d.file_id,
        "indexed_at": d.indexed_at.isoformat() if d.indexed_at and d.index_status == "SUCCESS" else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d, size in rows])


@router.get("/rag/config")
def rag_config(_: CurrentUser = Depends(require_student)):
    """学生端知识准备页展示的检索配置(只读, 与 RAGService 实际行为一致)."""
    return ResponseUtil.success({
        "personal_scope": ["论文", "开题报告", "中期报告", "终期报告", "题目信息"],
        "global_scope": ["答辩规范", "专业知识文档"],
        "default_top_k": 6,
        "retrieval": "向量 KNN + 关键词检索, RRF 融合排序",
        "query_rewrite": "多轮对话自动重写为独立问题",
    })


@router.post("/documents/{document_id}/index")
def index_document(document_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    doc = db.get(AIDocument, document_id)
    if doc is None or (user.role == "STUDENT" and doc.student_id != user.profile_id):
        raise BusinessException("文档不存在")
    from app.tasks.dispatcher import dispatch_index_task

    result = dispatch_index_task(document_id)
    if result is None:  # 已进入 Celery 队列
        return ResponseUtil.success({"document_id": document_id, "status": "PROCESSING",
                                     "message": "已提交后台索引任务"}, message="索引任务已提交")
    return ResponseUtil.success(result)


@router.get("/documents/{document_id}/status")
def document_status(document_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    doc = db.get(AIDocument, document_id)
    if doc is None or (user.role == "STUDENT" and doc.student_id != user.profile_id):
        raise BusinessException("文档不存在")
    return ResponseUtil.success({"id": doc.id, "index_status": doc.index_status,
                                 "status_cn": STATUS_CN.get(doc.index_status, doc.index_status),
                                 "chunk_count": doc.chunk_count, "error_message": doc.error_message})


@router.post("/documents/index-reports")
def index_stage_reports(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """将已通过的开题/中期/终期报告文本纳入知识库."""
    _check_ai_rate(user, db)
    rag = RAGService(db)
    rag.index_stage_reports(user.profile_id)
    return ResponseUtil.success(message="阶段报告已纳入知识库")


# ---------- RAG 检索测试 ----------

class RAGQueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    document_types: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=20)


@router.post("/rag/query")
def rag_query(body: RAGQueryRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    rag = RAGService(db)
    chunks = rag.retrieve(body.query, user.profile_id, body.document_types, top_k=body.top_k)
    return ResponseUtil.success({
        "query": body.query,
        "chunks": [{"chunk_id": c.chunk_id, "document_id": c.document_id, "document_type": c.document_type,
                    "section": c.section_title, "content": c.content, "score": c.score, "source": c.source}
                   for c in chunks],
    })


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    history: list[dict] | None = Field(default=None, description='[{"role":"user","content":"..."}]')


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """RAG 问答 SSE 流式输出(roadmap 七十二章)."""
    _check_ai_rate(user, db)
    rag = RAGService(db)

    def event_stream():
        # 独立会话: 流式生成中不做数据库写入的依赖
        from app.db.mysql import SessionLocal

        local_db = SessionLocal()
        try:
            local_rag = RAGService(local_db)
            for event in local_rag.answer_stream(body.query, user.profile_id, body.history, user_id=user.id):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            local_db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- AI 模拟答辩 ----------

defense = APIRouter(prefix="/ai/defense", tags=["AI-Defense"])


@defense.post("/questions/generate")
def generate_questions(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    service = DefenseService(db)
    items = service.generate_questions(user.profile_id, user.id)
    return ResponseUtil.success({"questions": items, "count": len(items)}, message="问题库已生成")


@defense.post("/sessions")
def create_session(question_count: int = Query(8, ge=3, le=22), user: CurrentUser = Depends(require_student),
                   db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).create_session(user.profile_id, question_count))


@defense.post("/sessions/{session_id}/start")
def start_session(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).start_session(user.profile_id, session_id))


class AnswerRequest(BaseModel):
    question: str = Field(min_length=2)
    answer: str = Field(min_length=5, max_length=8000)


@defense.post("/sessions/{session_id}/answer")
def answer_question(session_id: int, body: AnswerRequest, user: CurrentUser = Depends(require_student),
                    db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    service = DefenseService(db)
    result = service.evaluate_answer(user.profile_id, session_id, body.question, body.answer, user.id)
    return ResponseUtil.success(result)


@defense.post("/sessions/{session_id}/next")
def next_question(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).next_question(user.profile_id, session_id))


@defense.get("/sessions")
def list_sessions(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    return ResponseUtil.success(DefenseService(db).list_sessions(user.profile_id))


@defense.get("/sessions/{session_id}")
def session_detail(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    return ResponseUtil.success(DefenseService(db).session_detail(user.profile_id, session_id))


@defense.get("/sessions/{session_id}/report")
def get_report(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    return ResponseUtil.success(DefenseService(db).get_report(user.profile_id, session_id))


# ---------- PPT 大纲 ----------

@router.post("/ppt-outline/generate")
def generate_ppt_outline(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).generate_ppt_outline(user.profile_id, user.id))


class PptOutlinePage(BaseModel):
    page: int | str | None = None
    title: str = ""
    goal: str | None = None
    key_points: list[str] = Field(default_factory=list, max_length=10)
    speaker_notes: str | None = None
    recommended_visual: str | None = None


class PptOutlineExportRequest(BaseModel):
    title: str | None = None
    pages: list[PptOutlinePage] = Field(min_length=1, max_length=30)


@router.post("/ppt-outline/export")
def export_ppt_outline(body: PptOutlineExportRequest, _: CurrentUser = Depends(require_student)):
    """把当前大纲导出为真实 .pptx(roadmap 四十八章); 纯内存生成不调 LLM."""
    from urllib.parse import quote

    from fastapi.responses import Response

    from app.ai.defense.pptx_export import build_pptx

    try:
        content = build_pptx(body.model_dump())
    except ValueError as exc:
        raise BusinessException(str(exc), 40001)
    filename = quote("答辩PPT大纲.pptx")
    return Response(content=content,
                    media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


# ---------- 教师进度分析 ----------

@router.post("/progress/student/{student_id}/analyse")
def analyse_progress(student_id: int, user: CurrentUser = Depends(require_teacher_or_admin),
                     db: Session = Depends(get_db)):
    PermissionService.ensure_teacher_can_access_student(db, user, student_id)
    _check_ai_rate(user, db)
    return ResponseUtil.success(ProgressAnalysisService(db).analyse(student_id, user.id))


# ---------- RAG 检索质量评测(roadmap 九十一/九十二章, 管理员) ----------

class RagEvalRequest(BaseModel):
    dataset_name: str | None = Field(default=None, description="内置数据集名, 目前提供 demo_thesis")
    dataset: dict | None = Field(default=None, description="自定义数据集(与 dataset_name 二选一)")
    top_k: int = Field(default=5, ge=1, le=20)
    strategies: list[str] = Field(default_factory=lambda: ["vector", "keyword", "hybrid"])


@router.post("/admin/rag/evaluate")
def admin_rag_evaluate(body: RagEvalRequest, _: CurrentUser = Depends(require_admin),
                       db: Session = Depends(get_db)):
    from pathlib import Path

    from app.ai.rag.evaluation import STRATEGIES, evaluate_dataset, load_dataset_file

    if body.dataset:
        dataset = {**body.dataset, "top_k": body.top_k}
    elif body.dataset_name:
        path = Path(__file__).resolve().parents[3] / "eval_datasets" / f"{body.dataset_name}.json"
        if not path.is_file():
            raise BusinessException(f"内置评测数据集不存在: {body.dataset_name}")
        dataset = {**load_dataset_file(str(path)), "top_k": body.top_k}
    else:
        raise BusinessException("请提供 dataset 或 dataset_name")
    strategies = tuple(s for s in body.strategies if s in STRATEGIES) or STRATEGIES
    return ResponseUtil.success(evaluate_dataset(db, dataset, strategies))
