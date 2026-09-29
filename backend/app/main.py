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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 开发环境自动建表(生产应使用 Alembic 迁移)
    import os

    if os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true":
        from app.db.base import Base

        Base.metadata.create_all(bind=engine)
        logger.info("数据库表已就绪")
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
):
    app.include_router(r, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health")
def health():
    from app.ai.llm.factory import LLMFactory
    from app.db.redis import redis_available

    return {
        "status": "ok", "app": settings.app_name,
        "redis": redis_available(),
        "llm_provider": LLMFactory.provider_name(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
