"""任务分发: Celery 可用且启用时走队列, 否则同步执行(便于本地开发演示)."""
import logging

logger = logging.getLogger("app.tasks")


def _run_index(document_id: int) -> dict | None:
    from app.ai.rag.rag_service import RAGService
    from app.db.mysql import SessionLocal

    db = SessionLocal()
    try:
        return RAGService(db).index_document(document_id)
    finally:
        db.close()


def dispatch_index_task(document_id: int) -> dict | None:
    """返回 None 表示已入队(异步), 返回 dict 表示已同步完成."""
    from app.tasks.celery_app import CELERY_AVAILABLE, CELERY_ENABLED, celery_app

    if CELERY_ENABLED and CELERY_AVAILABLE and celery_app is not None:
        # 动态注册任务避免循环导入
        if not celery_app.conf.task_always_eager:
            task = celery_app.task(_run_index, name="tasks.index_document")
            task.delay(document_id)
            logger.info("文档 %s 已提交 Celery 索引队列", document_id)
            return None
    # 同步执行
    return _run_index(document_id)
