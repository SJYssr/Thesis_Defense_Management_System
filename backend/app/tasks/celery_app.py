"""Celery 任务定义: 文档索引等重任务由 Worker 执行(roadmap 三十二章).

开发模式(CELERY_ENABLED=false)时 dispatcher 直接同步执行, 无需启动 Worker.
"""
import logging

logger = logging.getLogger("app.tasks")

try:
    from celery import Celery
    from celery.schedules import crontab

    from app.core.config import settings

    celery_app = Celery(
        "graduation",
        broker=f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db + 1}",
        backend=f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db + 2}",
    )
    celery_app.conf.update(task_track_started=True, task_time_limit=1800, timezone="Asia/Shanghai")
    # 每日 01:00 逾期风险扫描(roadmap 八十六章); 由 worker -B 或独立 beat 进程调度
    celery_app.conf.beat_schedule = {
        "daily-overdue-risk-scan": {
            "task": "app.tasks.risk_scan.daily_scan",
            "schedule": crontab(hour=1, minute=0),
        },
    }
    CELERY_AVAILABLE = True
except Exception:  # celery 未安装或 Redis 不可用
    celery_app = None
    CELERY_AVAILABLE = False

# 开发模式默认同步执行, 避免本地必须起 Worker
import os

CELERY_ENABLED = os.getenv("CELERY_ENABLED", "false").lower() == "true"
