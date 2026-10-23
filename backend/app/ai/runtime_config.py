"""AI 运行时配置: 管理员在系统设置里填的 Base URL / API Key / 模型名, 保存即生效(roadmap 八十五章/P10).

优先级: 数据库(system_config 的 ai_* 键) > 环境变量(settings)。
DB 空串 = 未设置 = 跟随环境变量, 因此"全部留空"完全等价于改动前的 .env 行为。

为什么需要这一层: 用户要求"可自定义调用别的 AI 的 api 和 apikey", 且不重启进程、不改 .env;
所以 provider 工厂不能再直读 settings, 必须每次解析"当前生效"的配置。DB 读取有 5s 内存缓存
(避免每次 LLM 调用都查库), 管理端保存接口会显式调用 invalidate_ai_config() 让生效延迟趋近 0;
settings 侧变化(单测/热改环境)通过配置签名识别, 不依赖 TTL。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger("app.ai.runtime")

# DB 读取缓存: 每次 AI 调用都查库没有意义, 但也不能缓存到"改配置不生效"
CACHE_TTL_SECONDS = 5.0
LLM_PROVIDERS = ("auto", "openai", "ollama", "mock")
EMBEDDING_PROVIDERS = ("auto", "openai", "ollama", "hash")
# status=not_configured 时给前端的修复指引(与 /health、管理端共用同一句话术)
FIX_HINT = "在 系统设置 → AI 服务 中填写 Base URL 与 API Key, 或启动本地 ollama(ollama serve)"


def _pick(values: dict[str, str], key: str) -> str:
    return str(values.get(key) or "").strip()


def _norm_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _norm_provider(raw: str, allowed: tuple[str, ...], label: str) -> str:
    """provider 归一化: 空 = auto; 非法值回退 auto 并记 warning(不把 500 抛给用户)."""
    value = (raw or "").strip().lower()
    if not value:
        return "auto"
    if value not in allowed:
        logger.warning("%s 取值非法(%r), 已回退 auto; 可选 %s", label, raw, " | ".join(allowed))
        return "auto"
    return value


def _as_bool(raw: str, default: bool = True) -> bool:
    text = (raw or "").strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def _as_positive_int(raw: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        logger.warning("向量维度配置非法(%r), 按未设置处理(以模型实际维度为准)", raw)
        return None
    return value if value > 0 else None


@dataclass(frozen=True, repr=False)
class EffectiveAIConfig:
    """当前生效的 AI 配置(只读快照). *_from_db 记录哪些字段来自数据库. """

    llm_enabled: bool
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embedding_enabled: bool
    embedding_provider: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_dim: int
    embedding_dim_explicit: bool
    llm_from_db: frozenset = frozenset()
    embedding_from_db: frozenset = frozenset()

    # ---- 派生口径(工厂与健康探测共用, 避免两处各判一套) ----
    @property
    def llm_configured(self) -> bool:
        """是否"用户配过"云端来源: 显式选定 provider, 或给了 API Key / 自建 Base URL.

        not_configured(什么都没配)与 degraded(配了但探测失败)的分界就是它。
        """
        if self.llm_provider != "auto":
            return True
        return bool(self.llm_api_key) or "base_url" in self.llm_from_db

    @property
    def embedding_configured(self) -> bool:
        if self.embedding_provider != "auto":
            return True
        return bool(self.embedding_api_key) or "base_url" in self.embedding_from_db

    @property
    def ollama_llm_base_url(self) -> str:
        """ollama 的 OpenAI 兼容基址: DB 覆盖(视为已含 /v1) 优先, 否则本地 ollama_v1_url."""
        if "base_url" in self.llm_from_db:
            return self.llm_base_url
        return settings.ollama_v1_url

    @property
    def ollama_llm_model(self) -> str:
        if "model" in self.llm_from_db:
            return self.llm_model
        return settings.ollama_llm_model

    @property
    def ollama_embedding_base_url(self) -> str:
        if "base_url" in self.embedding_from_db:
            return self.embedding_base_url
        return settings.ollama_v1_url

    @property
    def ollama_embedding_model(self) -> str:
        if "model" in self.embedding_from_db:
            return self.embedding_model
        return settings.ollama_embedding_model

    @property
    def llm_cache_key(self) -> tuple:
        return ("llm", self.llm_provider, self.llm_base_url, self.llm_model, self.llm_api_key)

    @property
    def embedding_cache_key(self) -> tuple:
        return ("embedding", self.embedding_provider, self.embedding_base_url,
                self.embedding_model, self.embedding_dim, self.embedding_api_key)

    def __repr__(self) -> str:  # 密钥不进日志/终端
        from app.shared.sysconfig import mask_secret

        return (
            "EffectiveAIConfig("
            f"llm_enabled={self.llm_enabled}, llm_provider={self.llm_provider!r}, "
            f"llm_base_url={self.llm_base_url!r}, llm_api_key={mask_secret(self.llm_api_key)!r}, "
            f"llm_model={self.llm_model!r}, "
            f"embedding_enabled={self.embedding_enabled}, "
            f"embedding_provider={self.embedding_provider!r}, "
            f"embedding_base_url={self.embedding_base_url!r}, "
            f"embedding_api_key={mask_secret(self.embedding_api_key)!r}, "
            f"embedding_model={self.embedding_model!r}, embedding_dim={self.embedding_dim}, "
            f"embedding_dim_explicit={self.embedding_dim_explicit}, "
            f"llm_from_db={sorted(self.llm_from_db)}, "
            f"embedding_from_db={sorted(self.embedding_from_db)})"
        )


def _db_values(db=None) -> dict[str, str]:
    """读取数据库里的 ai_* 配置; db=None 时自己开短连接; 任何异常都退回"全部未设置"."""
    own = db is None
    session = db
    try:
        if own:
            from app.db.mysql import SessionLocal

            session = SessionLocal()
        from app.shared.sysconfig import get_all

        return dict(get_all(session))
    except Exception as exc:  # noqa: BLE001 — 库不可用不允许把 AI 调用打成 500
        logger.warning("读取 AI 运行时配置失败, 回退环境变量: %s: %s", type(exc).__name__, exc)
        return {}
    finally:
        if own and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


def _settings_signature() -> tuple:
    """环境变量侧签名: settings 变了立即重算(单测与热改环境不必等 TTL)."""
    return (
        settings.llm_provider, settings.llm_base_url, settings.llm_api_key, settings.llm_model,
        settings.embedding_provider, settings.embedding_base_url, settings.embedding_api_key,
        settings.embedding_model, settings.embedding_dim, settings.ollama_base_url,
        settings.ollama_llm_model, settings.ollama_embedding_model,
    )


def _build(values: dict[str, str]) -> EffectiveAIConfig:
    # ---- LLM ----
    llm_db_provider = _pick(values, "ai_llm_provider")
    llm_db_base = _norm_url(_pick(values, "ai_llm_base_url"))
    llm_db_key = _pick(values, "ai_llm_api_key")
    llm_db_model = _pick(values, "ai_llm_model")
    llm_db_enabled = _pick(values, "ai_llm_enabled")
    llm_from_db = frozenset(
        name for name, given in (
            ("provider", llm_db_provider), ("base_url", llm_db_base),
            ("api_key", llm_db_key), ("model", llm_db_model),
        ) if given
    )

    # ---- Embedding ----
    emb_db_provider = _pick(values, "ai_embedding_provider")
    emb_db_base = _norm_url(_pick(values, "ai_embedding_base_url"))
    emb_db_key = _pick(values, "ai_embedding_api_key")
    emb_db_model = _pick(values, "ai_embedding_model")
    emb_db_dim = _as_positive_int(_pick(values, "ai_embedding_dim"))
    emb_db_enabled = _pick(values, "ai_embedding_enabled")
    embedding_from_db = frozenset(
        name for name, given in (
            ("provider", emb_db_provider), ("base_url", emb_db_base), ("api_key", emb_db_key),
            ("model", emb_db_model), ("dim", emb_db_dim),
        ) if given
    )

    env_dim = int(settings.embedding_dim or 0)
    return EffectiveAIConfig(
        llm_enabled=_as_bool(llm_db_enabled, True) if llm_db_enabled else True,
        llm_provider=_norm_provider(llm_db_provider or settings.llm_provider,
                                    LLM_PROVIDERS, "ai_llm_provider"),
        llm_base_url=llm_db_base or _norm_url(str(settings.llm_base_url or "")),
        llm_api_key=llm_db_key or (settings.llm_api_key or "").strip(),
        llm_model=llm_db_model or (settings.llm_model or "").strip(),
        embedding_enabled=_as_bool(emb_db_enabled, True) if emb_db_enabled else True,
        embedding_provider=_norm_provider(emb_db_provider or settings.embedding_provider,
                                          EMBEDDING_PROVIDERS, "ai_embedding_provider"),
        embedding_base_url=emb_db_base or _norm_url(str(settings.embedding_base_url or "")),
        embedding_api_key=emb_db_key or (settings.embedding_api_key or "").strip(),
        embedding_model=emb_db_model or (settings.embedding_model or "").strip(),
        # 显式维度(DB 填了)优先; 否则跟随环境变量, 且允许按模型实际输出校正
        embedding_dim=emb_db_dim or env_dim,
        embedding_dim_explicit=emb_db_dim is not None,
        llm_from_db=llm_from_db,
        embedding_from_db=embedding_from_db,
    )


_lock = threading.Lock()
_cached: EffectiveAIConfig | None = None
_cached_at = 0.0
_cached_sig: tuple = ()


def resolve_ai_config(db=None, *, use_cache: bool = True) -> EffectiveAIConfig:
    """解析当前生效的 AI 配置; db 可复用请求会话(没有会话时自己开短连接)."""
    global _cached, _cached_at, _cached_sig

    signature = _settings_signature()
    if use_cache:
        with _lock:
            if (_cached is not None and _cached_sig == signature
                    and (time.monotonic() - _cached_at) < CACHE_TTL_SECONDS):
                return _cached
    config = _build(_db_values(db))
    with _lock:
        _cached = config
        _cached_at = time.monotonic()
        _cached_sig = signature
    return config


def invalidate_ai_config() -> None:
    """配置变更后立刻失效: 清缓存 + 重建 provider 单例(否则进程仍用旧 key/model 的客户端)."""
    global _cached, _cached_at, _cached_sig

    with _lock:
        _cached = None
        _cached_at = 0.0
        _cached_sig = ()
    try:
        from app.ai.llm.factory import LLMFactory, reset_ollama_probe_cache

        LLMFactory.reset()
        reset_ollama_probe_cache()
    except Exception as exc:  # noqa: BLE001 — 失效动作失败不应反向打断保存流程
        logger.warning("重置 LLM 工厂失败: %s: %s", type(exc).__name__, exc)
    try:
        from app.ai.embedding.factory import EmbeddingFactory

        EmbeddingFactory.reset()
    except Exception as exc:  # noqa: BLE001
        logger.warning("重置 Embedding 工厂失败: %s: %s", type(exc).__name__, exc)
