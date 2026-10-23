"""FastAPI 主入口: 装配全部路由/异常处理/CORS/初始化."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.db.mysql import engine
from app.models import all as _all_models  # noqa: F401 — 保证建表前模型已注册

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")


def _alembic_head_present() -> bool:
    """库是否已由 Alembic 接管(存在 alembic_version 且非空)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "alembic_version" not in insp.get_table_names():
        return False
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
    return bool(row and row[0])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动自检: 建表只走 Alembic, create_all 仅作显式逃生口.

    历史上 create_all 与迁移基线并存会让表结构漂移(模型改了而迁移没改),
    这里默认关闭 create_all, 并在库未迁移时直接把原因喊出来, 而不是等第一个请求 500。
    """
    import os

    if os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
        from app.db.base import Base

        Base.metadata.create_all(bind=engine)
        logger.warning("AUTO_CREATE_TABLES=true: 已用 create_all 建表, 该库不再受 Alembic 约束")
    elif not _alembic_head_present():
        raise RuntimeError(
            "数据库未初始化或未迁移: 未找到 alembic_version。"
            "请先执行 alembic upgrade head (cd backend && .venv/bin/python -m alembic upgrade head); "
            "仅本地临时库可设置 AUTO_CREATE_TABLES=true 绕过。"
        )
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://localhost:5173", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

API_PREFIX = "/api/v1"

# 业务模块
from app.modules.auth.router import router as auth_router  # noqa: E402
from app.modules.user.router import org_router, router as user_router  # noqa: E402
from app.modules.topic.router import admin_router as topic_admin_router  # noqa: E402
from app.modules.topic.router import router as topic_router  # noqa: E402
from app.modules.topic.router import teacher_router as topic_teacher_router  # noqa: E402
from app.modules.selection.router import admin_router as sel_admin_router  # noqa: E402
from app.modules.selection.router import router as sel_router  # noqa: E402
from app.modules.selection.router import teacher_router as sel_teacher_router  # noqa: E402
from app.modules.opening.router import admin_router as opening_admin  # noqa: E402
from app.modules.opening.router import student_router as opening_student  # noqa: E402
from app.modules.opening.router import teacher_router as opening_teacher  # noqa: E402
from app.modules.midterm.router import admin_router as midterm_admin  # noqa: E402
from app.modules.midterm.router import student_router as midterm_student  # noqa: E402
from app.modules.midterm.router import teacher_router as midterm_teacher  # noqa: E402
from app.modules.final_defense.router import admin_router as final_admin  # noqa: E402
from app.modules.final_defense.router import student_router as final_student  # noqa: E402
from app.modules.final_defense.router import teacher_router as final_teacher  # noqa: E402
from app.modules.material.router import admin_router as material_admin  # noqa: E402
from app.modules.material.router import router as material_router  # noqa: E402
from app.modules.material.router import teacher_router as material_teacher  # noqa: E402
from app.modules.file.router import router as file_router  # noqa: E402
from app.modules.progress.router import admin_router as progress_admin  # noqa: E402
from app.modules.progress.router import router as progress_router  # noqa: E402
from app.modules.progress.router import teacher_router as progress_teacher  # noqa: E402
from app.modules.announcement.router import admin_router as ann_admin  # noqa: E402
from app.modules.announcement.router import notification_router  # noqa: E402
from app.modules.announcement.router import router as ann_router  # noqa: E402
from app.modules.dashboard.router import router as dashboard_router  # noqa: E402
from app.modules.ai.router import defense as ai_defense_router  # noqa: E402
from app.modules.ai.router import router as ai_router  # noqa: E402
from app.modules.task.router import router as task_router  # noqa: E402
from app.modules.defense.router import router as defense_router  # noqa: E402
from app.modules.defense.router import rubric_router  # noqa: E402
from app.modules.archive.router import router as archive_router  # noqa: E402
from app.modules.audit.router import router as audit_router  # noqa: E402
from app.modules.audit.router import ai_router as ai_usage_router  # noqa: E402
from app.modules.sysconfig.router import router as sysconfig_router  # noqa: E402
from app.modules.aiprompt.router import router as aiprompt_router  # noqa: E402
from app.modules.schedule.router import router as schedule_router  # noqa: E402
from app.modules.schedule.router import admin_router as schedule_admin_router  # noqa: E402

for r in (
    auth_router, user_router, org_router,
    topic_router, topic_teacher_router, topic_admin_router,
    sel_router, sel_teacher_router, sel_admin_router,
    opening_student, opening_teacher, opening_admin,
    midterm_student, midterm_teacher, midterm_admin,
    final_student, final_teacher, final_admin,
    material_router, material_teacher, material_admin,
    file_router,
    progress_router, progress_teacher, progress_admin,
    ann_router, ann_admin, notification_router,
    dashboard_router,
    ai_router, ai_defense_router,
    task_router, defense_router, rubric_router, archive_router, audit_router, sysconfig_router,
    aiprompt_router,
    schedule_router,
    schedule_admin_router,
    ai_usage_router,
):
    app.include_router(r, prefix=API_PREFIX)


# /health 的 status 口径: ok 只给"真实可用"; 完全没配(也没有本地 ollama)时明确说
# not_configured, 让运维一眼看出是没填 Key 而不是配错了; 其余(配了但连不上)为 degraded。
HEALTH_STATUS_OK = "ok"
HEALTH_STATUS_NOT_CONFIGURED = "not_configured"
HEALTH_STATUS_DEGRADED = "degraded"


def _health_status(llm_status: str, embedding_status: str) -> str:
    if llm_status == HEALTH_STATUS_OK and embedding_status == HEALTH_STATUS_OK:
        return HEALTH_STATUS_OK
    if (llm_status == HEALTH_STATUS_NOT_CONFIGURED
            and embedding_status == HEALTH_STATUS_NOT_CONFIGURED):
        return HEALTH_STATUS_NOT_CONFIGURED
    return HEALTH_STATUS_DEGRADED


@app.get(f"{API_PREFIX}/health")
def health():
    """探活: 保留 app/redis/llm_provider 兼容字段, status 由真实 AI 探测决定.

    以前无论模型是否存在都返回 ok, 前端因此看不到"模型没起来"; 现在只有 llm 与
    embedding 真实可用才是 ok, 完全没配置时返回 not_configured, 其余为 degraded。
    HTTP 始终 200 —— 探活本身不因 AI 降级返回 5xx。
    """
    from app.ai.health import probe_all
    from app.db.redis import redis_available

    ai = probe_all()
    llm, embedding = ai["llm"], ai["embedding"]
    return {
        "status": _health_status(llm["status"], embedding["status"]),
        "app": settings.app_name,
        "redis": redis_available(),
        "llm_provider": llm["provider"] or "unavailable",
        "ai": ai,
    }


@app.get(f"{API_PREFIX}/health/ai")
def health_ai():
    """AI 链路明细: LLM / Embedding / Rerank / 向量库 / ollama 的完整探测结构."""
    from app.ai.health import probe_all

    return probe_all()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
