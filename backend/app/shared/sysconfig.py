"""SystemConfigService: 系统配置读取/更新(roadmap 八十五章).

读取走 Redis Cache-Aside(roadmap 八十一章), 更新后立即删缓存;
类型约束在 FIELDS 中声明, 非法值在写入前拒绝.
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.redis import redis_client
from app.models.system import SystemConfig

CACHE_KEY = "cache:sysconfig:all"
CACHE_TTL_SECONDS = 300

# key -> (默认值, 说明, 类型 str|bool|int)
FIELDS: dict[str, tuple[str, str, str]] = {
    "system_name": ("毕业设计管理系统", "系统名称(登录页与侧栏展示)", "str"),
    "school_name": ("", "学校名称", "str"),
    "ai_defense_enabled": ("true", "是否开启 AI 功能", "bool"),
    "ai_rate_limit_per_minute": (str(settings.ai_rate_limit_per_minute), "AI 接口每分钟调用上限", "int"),
    "max_upload_size_mb": (str(settings.max_upload_size_mb), "单文件上传大小上限(MB)", "int"),
    # 留空跟随环境变量 LLM_MODEL(roadmap 八十五章 AI 模型配置; Embedding 涉及向量重建, 仍走环境变量)
    "ai_llm_model": ("", "LLM 模型名覆盖(留空跟随服务器环境变量)", "str"),
}


class SystemConfigError(ValueError):
    """配置键不存在或值类型非法."""


def _load_from_db(db: Session) -> dict[str, str]:
    rows = db.execute(select(SystemConfig)).scalars().all()
    stored = {r.key: r.value for r in rows}
    return {key: stored.get(key, default) for key, (default, _, _) in FIELDS.items()}


def get_all(db: Session, use_cache: bool = True) -> dict[str, str]:
    """全部配置(含未落库默认值); Redis 不可用时静默直读数据库."""
    if use_cache:
        try:
            cached = redis_client.get(CACHE_KEY)
            if cached is not None:
                return json.loads(cached)
        except Exception:  # noqa: BLE001 — Redis 故障不阻塞业务
            pass
    values = _load_from_db(db)
    try:
        redis_client.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(values, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass
    return values


def get_bool(db: Session, key: str, default: bool = False) -> bool:
    values = get_all(db)
    return values.get(key, str(default).lower()) == "true"


def get_int(db: Session, key: str, default: int = 0) -> int:
    try:
        return int(get_all(db).get(key, default))
    except (TypeError, ValueError):
        return default


def update_configs(db: Session, items: dict[str, str], updater_id: int | None) -> dict[str, str]:
    """批量更新配置并失效缓存; 未知键/非法值整体拒绝."""
    for key, value in items.items():
        if key not in FIELDS:
            raise SystemConfigError(f"未知配置项: {key}")
        vtype = FIELDS[key][2]
        text = str(value)
        if vtype == "bool" and text not in ("true", "false"):
            raise SystemConfigError(f"{FIELDS[key][1]} 仅允许 true/false")
        if vtype == "int":
            try:
                if int(text) < 1:
                    raise ValueError
            except ValueError as exc:
                raise SystemConfigError(f"{FIELDS[key][1]} 必须为正整数") from exc
        if len(text) > 500:
            raise SystemConfigError(f"{FIELDS[key][1]} 超过长度限制")

    for key, value in items.items():
        row = db.get(SystemConfig, key)
        if row is None:
            db.add(SystemConfig(key=key, value=str(value), updater_id=updater_id))
        else:
            row.value = str(value)
            row.updater_id = updater_id
    db.commit()

    try:
        redis_client.delete(CACHE_KEY)
    except Exception:  # noqa: BLE001
        pass
    return _load_from_db(db)
