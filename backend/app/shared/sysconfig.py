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
    # D70: 单用户每日上传总字节配额, 防止任意登录用户无限占用存储
    "max_upload_daily_mb": ("200", "单个用户每日上传总大小上限(MB)", "int"),
    # 留空跟随环境变量 LLM_MODEL(roadmap 八十五章 AI 模型配置)
    "ai_llm_model": ("", "LLM 模型名覆盖(留空跟随服务器环境变量)", "str"),
    # P10: 运行时自定义第三方 OpenAI 兼容服务. 优先级 DB(本表) > 环境变量, 保存即生效(无需重启).
    # 全部留空 = 跟随环境变量; api_key 只写不读, 见 SECRET_FIELDS
    "ai_llm_enabled": ("true", "是否启用 LLM 服务", "bool"),
    "ai_llm_provider": ("", "LLM 提供方(auto/openai/ollama, 留空跟随环境变量)", "str"),
    "ai_llm_base_url": ("", "LLM 接口地址(如 https://api.deepseek.com/v1, 留空跟随环境变量)", "str"),
    "ai_llm_api_key": ("", "LLM API Key(只写不读)", "str"),
    "ai_embedding_enabled": ("true", "是否启用向量模型", "bool"),
    "ai_embedding_provider": ("", "向量模型提供方(auto/openai/ollama, 留空跟随环境变量)", "str"),
    "ai_embedding_base_url": ("", "向量模型接口地址(留空跟随环境变量)", "str"),
    "ai_embedding_api_key": ("", "向量模型 API Key(只写不读)", "str"),
    "ai_embedding_model": ("", "向量模型名(留空跟随环境变量)", "str"),
    "ai_embedding_dim": ("", "向量维度(留空由模型实际维度探测, 改维度需重建知识库)", "str"),
    "ai_rerank_enabled": ("true", "是否启用检索精排", "bool"),
    "ai_rerank_provider": ("", "精排提供方(auto/onnx/http/none, 留空跟随环境变量)", "str"),
    "ai_rerank_base_url": ("", "远端精排接口地址(如 https://api.jina.ai/v1, 留空跟随环境变量)", "str"),
    "ai_rerank_api_key": ("", "远端精排 API Key(只写不读)", "str"),
    "ai_rerank_model": ("", "精排模型名(留空跟随环境变量)", "str"),
    "ai_rerank_model_dir": ("", "本地 ONNX 精排模型目录(留空跟随环境变量)", "str"),
}

# 密钥类配置: 任何 GET 响应都不返回明文, 只回掩码; 只有 ADMIN 能写
SECRET_FIELDS: frozenset[str] = frozenset({"ai_llm_api_key", "ai_embedding_api_key", "ai_rerank_api_key"})


def mask_secret(value: str) -> str:
    """把密钥压成掩码; 空值仍返回空串, 便于前端判断"是否已配置"."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return f"***{tail}" if tail else "***"


def mask_secrets(values: dict[str, str]) -> dict[str, str]:
    """返回一份密钥已掩码的副本, 用于对外响应."""
    masked = dict(values)
    for key in SECRET_FIELDS & masked.keys():
        masked[key] = mask_secret(masked[key])
    return masked


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

    values = _load_from_db(db)
    try:
        # D68: 先失效再写回新值, 不留"空缓存"让下游(如 LLM 模型覆盖)静默回退默认
        redis_client.delete(CACHE_KEY)
        redis_client.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(values, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass
    return values
