"""AI 模块路由: 文档索引/RAG检索/AI模拟答辩/PPT大纲/进度分析/SSE流式问答(roadmap 六十五章)."""
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.defense.oral import oral_question
from app.ai.defense.service import DefenseService
from app.ai.progress.service import ProgressAnalysisService
from app.ai.rag.index_diagnostics import public_index_error
from app.ai.rag.practice_sources import practice_upload_filter
from app.ai.rag.rag_service import RAGService
from app.ai.runtime_config import resolve_ai_config
from app.ai.speech import SpeechFactory
from app.core.config import settings
from app.core.exceptions import BusinessException, PermissionDeniedException, ResourceNotFoundException
from app.core.permissions import (
    CurrentUser,
    PermissionService,
    get_current_user,
    require_admin,
    require_student,
    require_teacher_or_admin,
)
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.db.redis import RedisKeys, redis_client
from app.models.ai import AIDefenseSession, AIDefenseTurn, AIDocument
from app.models.enums import AIDocumentType
from app.shared.storage import pathlib_suffix, read_upload
from app.shared.upload_policy import (
    MAX_RECORDING_MB,
    MAX_RECORDING_SECONDS,
    RECORDING_SAMPLE_RATE,
    ensure_daily_quota,
    max_upload_size_mb,
)

logger = logging.getLogger("app.ai.router")

router = APIRouter(prefix="/ai", tags=["AI"])

STATUS_CN = {"WAITING": "待处理", "PROCESSING": "处理中", "SUCCESS": "已完成", "FAILED": "失败"}


def _check_ai_rate(user: CurrentUser, db: Session, *, bucket: str = "") -> None:
    """AI 总开关 + 每分钟限流, 阈值均来自系统配置(roadmap 七十七/八十五章).

    D41: 计数与过期时间放进同一事务型 pipeline 原子提交(SET NX EX 打底 + INCR),
    进程在两步之间崩溃不再留下没有 TTL 的裸 key(否则该用户永久 42901);
    Redis 任何异常都降级放行并记 warning, 不让缓存故障把 AI 打成全站 500。

    计数初值必须是 0: INCR 之后第一次请求才等于 1。若初值写 1, 每个窗口都多算一次,
    "每分钟限 N 次"实际只放行 N-1 次(上限填 1 时第一个请求即 429) —— 阈值就不再按填写值生效。
    """
    from app.shared.sysconfig import get_all

    cfg = get_all(db)
    if cfg.get("ai_defense_enabled") != "true":
        raise BusinessException("AI 功能已由管理员暂时关闭", 40300)
    try:
        limit = int(cfg.get("ai_rate_limit_per_minute") or settings.ai_rate_limit_per_minute)
    except (TypeError, ValueError):
        limit = settings.ai_rate_limit_per_minute
    key = RedisKeys.RATE_AI.format(user_id=user.id)
    if bucket:
        key += ":" + bucket  # 转写、合成、现场对话互不消耗对方额度。
    try:
        pipe = redis_client.pipeline(transaction=True)
        pipe.set(key, 0, nx=True, ex=60)  # 仅首次请求生效: 原子建立计数与 60s 过期
        pipe.incr(key)
        pipe.ttl(key)
        _, current, ttl = pipe.execute()
        current = int(current)
    except Exception as exc:  # noqa: BLE001 — Redis 故障只降级限流, 不阻断 AI 功能
        logger.warning("AI 限流计数失败, 本次放行: %s", exc)
        return
    if current > limit:
        raise BusinessException(f"AI 接口调用过于频繁(每分钟限 {limit} 次)", 42901,
                                headers={"Retry-After": str(max(1, ttl) if ttl >= 0 else 60)})


# ---------- 文档索引 ----------

@router.get("/documents")
def list_documents(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                   user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """学生知识库文档列表(D50): 依赖只注入一次, 分页下推到 SQL, size 上限 100."""
    from sqlalchemy.orm import defer

    from app.models.system import SysFile
    from app.shared.query import paginate

    ids, total = paginate(db, select(AIDocument.id).order_by(AIDocument.id.desc()),
                          filters=[AIDocument.student_id == user.profile_id], page=page, size=size)
    if not ids:
        return ResponseUtil.page([], total, page, size)
    practice_upload = practice_upload_filter(user.id, AIDocument.cycle_id)
    rows = db.execute(
        select(AIDocument, SysFile.size, practice_upload).options(defer(AIDocument.content_text))
        .outerjoin(SysFile, AIDocument.file_id == SysFile.id)
        .where(AIDocument.id.in_(ids), AIDocument.student_id == user.profile_id)
        .order_by(AIDocument.id.desc())
    ).all()
    ready = {doc.id for doc in RAGService(db).practice_documents(user.profile_id)}
    items = [{
        "practice_eligible": d.id in ready, "practice_upload": bool(uploaded),
        "id": d.id, "document_name": d.document_name, "document_type": d.document_type,
        "index_status": d.index_status, "status_cn": STATUS_CN.get(d.index_status, d.index_status),
        "chunk_count": d.chunk_count, "version": d.version, "error_message": public_index_error(d.error_message),
        "file_size": file_size, "file_id": d.file_id,
        "indexed_at": d.indexed_at.isoformat() if d.indexed_at and d.index_status == "SUCCESS" else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    } for d, file_size, uploaded in rows]
    return ResponseUtil.page(items, total, page, size)


@router.get("/rag/config")
def rag_config(_: CurrentUser = Depends(require_student)):
    """学生端知识准备页展示的检索配置(只读, 与 RAGService 实际行为一致)."""
    return ResponseUtil.success({
        "personal_scope": ["论文", "源代码（ZIP）", "毕业设计", "说明文档", "开题报告", "中期报告", "终期报告"],
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

    try:
        result = dispatch_index_task(document_id)
    except ValueError as exc:
        raise BusinessException(str(exc)) from exc
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
                                 "chunk_count": doc.chunk_count, "error_message": public_index_error(doc.error_message)})


@router.post("/documents/index-reports")
def index_stage_reports(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """将已通过的开题/中期/终期报告文本纳入知识库."""
    _check_ai_rate(user, db)
    rag = RAGService(db)
    result = rag.index_stage_reports(user.profile_id)
    failed = len(result["failed_document_ids"])
    message = f"已同步 {result['indexed']} 份材料"
    if failed:
        message += f"，{failed} 份索引失败，请查看文档状态后重试"
    return ResponseUtil.success(result, message=message)


# ---------- RAG 检索测试 ----------

class RAGQueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    # D50: 文档类型枚举强校验, 非法值在入口即 422, 不再有机会拼进 RediSearch TAG 表达式
    document_types: list[AIDocumentType] | None = None
    top_k: int = Field(default=6, ge=1, le=20)


@router.post("/rag/query")
def rag_query(body: RAGQueryRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    rag = RAGService(db)
    doc_types = [t.value for t in body.document_types] if body.document_types else None
    chunks = rag.retrieve(body.query, user.profile_id, doc_types, top_k=body.top_k)
    return ResponseUtil.success({
        "query": body.query,
        "chunks": [{"chunk_id": c.chunk_id, "document_id": c.document_id, "document_type": c.document_type,
                    "section": c.section_title, "content": c.content, "score": c.score, "source": c.source}
                   for c in chunks],
    })


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    history: list[dict] | None = Field(default=None, description='[{"role":"user","content":"..."}]')
    # None = 跟随系统配置 agent_enabled; True/False 显式覆盖(灰度/排障用)
    agent: bool | None = Field(default=None, description="是否使用多步 Agent 问答")


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    """RAG 问答 SSE 流式输出(roadmap 七十二章).

    agent=True(或未显式指定时运维开关 AGENT_ENABLED=settings.agent_enabled 为真)走多步 Agent 路径:
    注意该开关是部署级环境变量, 不在「系统设置」里, 只能在 .env / compose 里改并重启:
    事件名只在 meta/delta/error/done 之外新增 step/citations, 旧事件语义不变;
    多步路径内部任何异常都会先发 error 再回退单轮 answer_stream, 前端不会白屏。
    """
    _check_ai_rate(user, db)
    use_agent = bool(settings.agent_enabled) if body.agent is None else bool(body.agent)

    def event_stream():
        # 独立会话: 流式生成中不做数据库写入的依赖
        from app.db.mysql import SessionLocal

        local_db = SessionLocal()
        try:
            local_rag = RAGService(local_db)
            if use_agent:
                events = local_rag.answer_agent_stream(
                    body.query, user.profile_id, body.history, user_id=user.id)
            else:
                events = local_rag.answer_stream(
                    body.query, user.profile_id, body.history, user_id=user.id)
            for event in events:
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            local_db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/agent/trace")
def agent_trace_recent(limit: int = Query(default=20, ge=1, le=200),
                       user: CurrentUser = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Agent 问答轨迹(只读, 进程内环形缓冲 P10-C).

    权限理由: 轨迹含 query 原文与 student_id, 属学生学习数据, 不是运维公共信息。
    - ADMIN: 可看全部, 用于排障(步数/耗时/是否降级);
    - STUDENT: 只能看到自己的记录(按 profile_id 过滤), 看不到别人的提问内容;
    - 教师与其他角色: 不开放 —— 教师已有专门的学情接口, 此处不做学生提问内容的二次暴露。
    数据来源是进程内 deque(settings.agent_trace_keep), 重启清空、多 worker 各自独立,
    所以这里显式返回 scope 说明, 避免前端误以为是完整审计日志。
    """
    from app.ai.agent.trace import agent_trace
    from app.models.enums import Role

    role = str(getattr(user, "role", "") or "")
    if role == Role.ADMIN.value:
        rows = agent_trace.recent(limit)
    elif role == Role.STUDENT.value:
        rows = [item for item in agent_trace.recent(10000)
                if item.get("student_id") == user.profile_id][-limit:]
    else:
        raise PermissionDeniedException("仅管理员或学生本人可查看 Agent 轨迹")
    rows = list(reversed(rows))  # 最新在前
    return ResponseUtil.success({
        "items": rows,
        "total": len(rows),
        "scope": "process-local-ring-buffer",
        "keep": int(getattr(settings, "agent_trace_keep", 50) or 50),
    })


# ---------- AI 模拟答辩 ----------

defense = APIRouter(prefix="/ai/defense", tags=["AI-Defense"])


class PracticeMaterials(BaseModel):
    document_ids: list[int] | None = Field(default=None, min_length=1, max_length=30)
    question_count: int | None = Field(default=None, ge=3, le=22)


class PracticeQuestionBank(BaseModel):
    question_ids: list[int] = Field(min_length=1, max_length=22)


@defense.post("/questions/generate")
def generate_questions(body: PracticeMaterials | None = None, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    service = DefenseService(db)
    items = service.generate_questions(user.profile_id, user.id, body.document_ids if body else None,
                                       body.question_count if body else None)
    return ResponseUtil.success({"questions": items, "count": len(items)}, message="问题库已生成")


@defense.post("/sessions")
def create_session(body: PracticeQuestionBank | None = None, question_count: int = Query(8, ge=3, le=22),
                   user: CurrentUser = Depends(require_student),
                   db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).create_session(
        user.profile_id, question_count, body.question_ids if body else None))


@defense.post("/sessions/{session_id}/start")
def start_session(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db)
    return ResponseUtil.success(DefenseService(db).start_session(user.profile_id, session_id))


class AnswerRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=2)
    answer: str = Field(min_length=1, max_length=8000)


class LiveStart(BaseModel):
    request_id: UUID
    question_count: int = Field(default=8, ge=3, le=8)


class LiveAnswer(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    turn_no: int = Field(ge=1, le=16)
    answer: str = Field(min_length=1, max_length=8000)


@defense.post("/live/start")
def start_live(body: LiveStart, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db, bucket="live-start")
    return ResponseUtil.success(DefenseService(db).start_live(user.profile_id, str(body.request_id), body.question_count))


@defense.post("/sessions/{session_id}/live/advance")
def advance_live(session_id: int, body: LiveAnswer, user: CurrentUser = Depends(require_student),
                 db: Session = Depends(get_db)):
    _check_ai_rate(user, db, bucket="live-advance")
    return ResponseUtil.success(DefenseService(db).advance_live(user.profile_id, session_id, body.turn_no, body.answer))


class PracticeObservations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    no_face: int = Field(ge=0, le=65535, strict=True)
    multiple_faces: int = Field(ge=0, le=65535, strict=True)
    tab_hidden: int = Field(ge=0, le=65535, strict=True)


@defense.put("/sessions/{session_id}/observations")
def save_observations(session_id: int, body: PracticeObservations, user: CurrentUser = Depends(require_student),
                      db: Session = Depends(get_db)):
    DefenseService(db).record_observations(user.profile_id, session_id, str(body.run_id),
                                           body.no_face, body.multiple_faces, body.tab_hidden)
    return ResponseUtil.success()


@defense.get("/speech-capabilities")
def speech_capabilities(_: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    config = resolve_ai_config(db)
    max_bytes = min(MAX_RECORDING_MB, max_upload_size_mb(db)) * 1024 * 1024
    return ResponseUtil.success({
        "speech": config.speech_slot.enabled and config.speech_slot.configured,
        "transcription": config.transcription_slot.enabled and config.transcription_slot.configured,
        "max_recording_seconds": min(MAX_RECORDING_SECONDS, (max_bytes - 44) // (RECORDING_SAMPLE_RATE * 2)),
    })


@defense.post("/sessions/{session_id}/turns/{turn_no}/transcription")
def transcribe_answer(session_id: int, turn_no: int, file: UploadFile,
                      user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    turn = db.execute(select(AIDefenseTurn.turn_no, AIDefenseTurn.student_answer, AIDefenseSession.status)
                      .join(AIDefenseSession).where(AIDefenseSession.id == session_id,
                                                    AIDefenseSession.student_id == user.profile_id)
                      .order_by(AIDefenseTurn.id.desc()).limit(1)).first()
    if turn is None:
        raise ResourceNotFoundException("未找到可作答的答辩题目")
    if turn.turn_no != turn_no or turn.student_answer is not None or turn.status != "RUNNING":
        raise BusinessException("仅当前尚未作答的题目可以转写录音，请刷新练习", 40900)
    _check_ai_rate(user, db, bucket="transcription")
    voice = SpeechFactory.get(db, "transcription")
    if pathlib_suffix(file.filename) != ".wav":
        raise BusinessException("回答录音需要使用 WAV 格式", 42200)
    audio, _, _, size, _ = read_upload(file, min(MAX_RECORDING_MB, max_upload_size_mb(db)))
    ensure_daily_quota(db, user.id, size)
    db.rollback()
    return ResponseUtil.success({"text": voice.transcribe(audio)})


@defense.get("/sessions/{session_id}/turns/{turn_no}/speech", response_class=Response,
             responses={200: {"content": {"audio/wav": {}}}})
def read_question_aloud(session_id: int, turn_no: int, user: CurrentUser = Depends(require_student),
                        db: Session = Depends(get_db)):
    question = db.scalar(select(AIDefenseTurn.question).join(AIDefenseSession).where(
        AIDefenseSession.id == session_id, AIDefenseSession.student_id == user.profile_id,
        AIDefenseTurn.turn_no == turn_no))
    if question is None:
        raise ResourceNotFoundException("未找到可朗读的答辩题目")
    if not oral_question(question):
        raise BusinessException("这道旧题需要更新，请继续练习以获取口头问题", 40900)
    _check_ai_rate(user, db, bucket="synthesis")
    voice = SpeechFactory.get(db)
    db.rollback()  # 读事务结束后再等待模型合成。
    return Response(voice.synthesize(question), media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


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


@defense.post("/sessions/{session_id}/report/retry")
def retry_live_report(session_id: int, user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    _check_ai_rate(user, db, bucket="live-report")
    return ResponseUtil.success(DefenseService(db).retry_live_report(user.profile_id, session_id))


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
