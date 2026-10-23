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
    # P11: AI 配置只有两个自由度 —— 选哪个供应商(ai_provider 表) + 用哪个模型名。
    # Base URL 与 API Key 属于供应商行, 不再按能力各填一遍, 运行期也不读任何 AI 环境变量。
    "ai_chat_provider_id": ("", "会话模型来源供应商 id(ai_provider.id)", "str"),
    "ai_chat_model": ("", "会话模型名(从供应商模型清单中选)", "str"),
    "ai_embedding_provider_id": ("", "向量模型来源供应商 id(ai_provider.id)", "str"),
    "ai_embedding_model": ("", "向量模型名", "str"),
    "ai_embedding_dim": ("", "向量维度(由模型实际输出自动写入; 改动会要求重建知识库)", "str"),
    "ai_rerank_provider_id": ("", "精排模型来源供应商 id(ai_provider.id; 空 = 关闭精排)", "str"),
    "ai_rerank_model": ("", "精排模型名", "str"),
}

# 本表不再保存任何密钥: AI 凭证加密落在 ai_provider.api_key_enc, GET 只回掩码提示。
SECRET_FIELDS: frozenset[str] = frozenset()

# 这些键一旦为空就会让界面"看起来坏了"(侧栏/登录页/浏览器标题全变空白),
# 所以按"必填"处理: 提交空串或纯空白直接拒绝, 而不是先存进去再让前端兜底。
NON_EMPTY_FIELDS: frozenset[str] = frozenset({"system_name"})


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
    normalized: dict[str, str] = {}
    for key, value in items.items():
        if key not in FIELDS:
            raise SystemConfigError(f"未知配置项: {key}")
        vtype = FIELDS[key][2]
        text = str(value)
        if key in NON_EMPTY_FIELDS:
            text = text.strip()
            if not text:
                raise SystemConfigError(f"{FIELDS[key][1]} 不能为空")
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
        normalized[key] = text

    for key, value in normalized.items():
        row = db.get(SystemConfig, key)
        if row is None:
            db.add(SystemConfig(key=key, value=value, updater_id=updater_id))
        else:
            row.value = value
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
