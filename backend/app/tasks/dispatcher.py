"""任务分发: Celery 可用且启用时走队列, 否则同步执行(便于本地开发演示)."""
import logging

logger = logging.getLogger("app.tasks")

from app.tasks.celery_app import CELERY_AVAILABLE, CELERY_ENABLED, celery_app


def _run_index(document_id: int) -> dict | None:
    from app.ai.rag.rag_service import RAGService
    from app.db.mysql import SessionLocal

    db = SessionLocal()
    try:
        return RAGService(db).index_document(document_id)
    finally:
        db.close()


# Worker 启动时通过 celery_app.conf.imports 导入本模块并注册, 不能只在 Web 请求中动态注册。
index_document_task = celery_app.task(_run_index, name="tasks.index_document") if celery_app else None


def dispatch_index_task(document_id: int) -> dict | None:
    """返回 None 表示已入队(异步), 返回 dict 表示已同步完成."""
    if CELERY_ENABLED and CELERY_AVAILABLE and celery_app is not None:
        if not celery_app.conf.task_always_eager:
            index_document_task.delay(document_id)
            logger.info("文档 %s 已提交 Celery 索引队列", document_id)
            return None
    # 同步执行
    return _run_index(document_id)


def index_uploaded_material(document_id: int) -> None:
    """材料已提交后独立索引; 模型或队列故障不影响上传, 并允许从知识库重试。"""
    from sqlalchemy import update

    from app.ai.rag.index_diagnostics import IndexIssue
    from app.db.mysql import SessionLocal
    from app.models.ai import AIDocument

    try:
        dispatch_index_task(document_id)
    except Exception:
        logger.exception("材料 %s 自动索引失败", document_id)
        try:
            with SessionLocal() as session:
                session.execute(update(AIDocument).where(AIDocument.id == document_id, AIDocument.index_status == "WAITING")
                                .values(index_status="FAILED", error_message=IndexIssue.QUEUE.value))
                session.commit()
        except Exception:
            logger.exception("材料 %s 索引失败状态记录失败", document_id)
