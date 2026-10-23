"""AI 运行时配置: 只读数据库(供应商表 + 能力绑定键), 运行期不跟随任何 AI 环境变量(P11).

为什么去掉环境变量: 一个带管理后台的系统若同时接受 .env 与数据库两个来源, "当前到底
用哪套"就不可回答 —— 用户删掉 .env 里的 Key 之后, 系统会静默回退到另一套来源,
线上排查成本远大于所谓的灵活性。P11 起口径统一为:
  - Base URL / API Key / 模型名 全部来自 ai_provider 表 + system_config 的绑定键;
  - 环境变量只剩 AI_TEST_MODE=mock|hash 一个测试替身开关(APP_ENV=prod 时启动即失败);
  - 本地 ollama 地址是供应商预设的默认 base_url, 不是环境变量。

缓存: 5s 内存缓存(每次 LLM 调用都查库没有意义) + invalidate_ai_config() 立即失效,
所以管理端保存后生效延迟趋近 0, 不依赖 TTL。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from app.ai.providers import ProviderSnapshot, load_provider_snapshots, openai_base_url
from app.core.config import settings

logger = logging.getLogger("app.ai.runtime")

CACHE_TTL_SECONDS = 5.0
SLOT_LABELS = {"chat": "会话模型", "embedding": "向量模型", "rerank": "精排模型"}

# 唯一的 AI 环境变量: 测试替身。不参与真实配置解析, 只在单测/离线演示时替换实现。
TEST_MODES = ("", "mock", "hash")


def _pick(values: dict[str, str], key: str) -> str:
    return str(values.get(key) or "").strip()


def _as_int(raw: object) -> int | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _as_bool(raw: object, default: bool = True) -> bool:
    text = str(raw if raw is not None else "").strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def provider_kind_to_llm(kind: str) -> str:
    """供应商类型 -> 会话模型实现: ollama 走原生客户端, 其余一律 OpenAI 兼容."""
    clean = (kind or "").strip().lower()
    if clean == "ollama":
        return "ollama"
    if clean in ("mock", "null"):
        return "mock"
    if clean in ("hash",):
        return "hash"
    return "openai"


def test_mode() -> str:
    """测试替身模式; 非空时对应能力位直接换成替身实现(见 app/ai/llm、embedding 工厂)."""
    value = str(getattr(settings, "ai_test_mode", "") or "").strip().lower()
    return value if value in TEST_MODES else ""


@dataclass(frozen=True)
class AISlot:
    """一个能力位(chat / embedding / rerank)的生效配置快照."""

    slot: str
    kind: str
    enabled: bool
    provider_id: int | None
    provider_name: str
    base_url: str
    api_key: str
    model: str
    dim: int = 0
    dim_explicit: bool = False
    models: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        """是否"配过": 选了供应商并给了模型名(mock/hash 替身恒为已配置)."""
        if self.kind in ("auto", "none"):
            return False
        if self.kind in ("mock", "hash"):
            return True
        return bool(self.provider_id and self.model)

    @property
    def openai_base(self) -> str:
        return openai_base_url(self.base_url)

    @property
    def cache_key(self) -> tuple:
        return (self.slot, self.kind, self.openai_base, self.model, self.api_key, self.dim)


@dataclass(frozen=True)
class EffectiveAIConfig:
    """当前生效的 AI 配置(只读快照).

    对外仍暴露 llm_* / embedding_* / rerank_* 三组扁平属性, 但底层只有三个 AISlot,
    避免"三个能力各一套解析代码"再次分叉。
    """

    chat: AISlot
    embedding_slot: AISlot
    rerank_slot: AISlot

    # ---- 会话模型 ----
    @property
    def llm_enabled(self) -> bool:
        return self.chat.enabled

    @property
    def llm_provider(self) -> str:
        return self.chat.kind

    @property
    def llm_base_url(self) -> str:
        return self.chat.openai_base

    @property
    def llm_api_key(self) -> str:
        return self.chat.api_key

    @property
    def llm_model(self) -> str:
        return self.chat.model

    @property
    def llm_configured(self) -> bool:
        return self.chat.configured

    @property
    def llm_cache_key(self) -> tuple:
        return self.chat.cache_key

    @property
    def ollama_llm_base_url(self) -> str:
        return self.chat.openai_base

    @property
    def ollama_llm_model(self) -> str:
        return self.chat.model

    # ---- 向量模型 ----
    @property
    def embedding_enabled(self) -> bool:
        return self.embedding_slot.enabled

    @property
    def embedding_provider(self) -> str:
        return self.embedding_slot.kind

    @property
    def embedding_base_url(self) -> str:
        return self.embedding_slot.openai_base

    @property
    def embedding_api_key(self) -> str:
        return self.embedding_slot.api_key

    @property
    def embedding_model(self) -> str:
        return self.embedding_slot.model

    @property
    def embedding_dim(self) -> int:
        return self.embedding_slot.dim

    @property
    def embedding_dim_explicit(self) -> bool:
        return self.embedding_slot.dim_explicit

    @property
    def embedding_configured(self) -> bool:
        return self.embedding_slot.configured

    @property
    def embedding_cache_key(self) -> tuple:
        return self.embedding_slot.cache_key

    # ---- 精排模型 ----
    @property
    def rerank_enabled(self) -> bool:
        return self.rerank_slot.enabled

    @property
    def rerank_provider(self) -> str:
        return self.rerank_slot.kind

    @property
    def rerank_base_url(self) -> str:
        return self.rerank_slot.openai_base

    @property
    def rerank_api_key(self) -> str:
        return self.rerank_slot.api_key

    @property
    def rerank_model(self) -> str:
        return self.rerank_slot.model

    @property
    def rerank_configured(self) -> bool:
        return self.rerank_slot.configured

    def __repr__(self) -> str:  # 密钥不进日志/终端
        from app.ai.crypto import key_hint

        def show(slot: AISlot) -> str:
            return (f"kind={slot.kind!r}, provider={slot.provider_name!r}, "
                    f"base_url={slot.base_url!r}, model={slot.model!r}, "
                    f"key={key_hint(slot.api_key)!r}, enabled={slot.enabled}")

        return (f"EffectiveAIConfig(chat=({show(self.chat)}), "
                f"embedding=({show(self.embedding_slot)}), rerank=({show(self.rerank_slot)}))")


EMPTY_SNAPSHOTS: dict[int, ProviderSnapshot] = {}


def _slot(
    values: dict[str, str], snapshots: dict[int, ProviderSnapshot], *,
    name: str, id_key: str, model_key: str, enabled: bool, dim_key: str | None = None,
) -> AISlot:
    provider_id = _as_int(values.get(id_key))
    snap = snapshots.get(provider_id) if provider_id else None
    model = _pick(values, model_key)
    dim = _as_int(values.get(dim_key)) if dim_key else None
    if snap is None:
        # 未绑定: auto 表示"没配", 由工厂转成 AIServiceUnavailable(文案统一在工厂里)
        kind = "auto" if name != "rerank" else "none"
        return AISlot(slot=name, kind=kind, enabled=enabled, provider_id=None, provider_name="",
                      base_url="", api_key="", model=model, dim=dim or 0,
                      dim_explicit=dim is not None)
    kind = provider_kind_to_llm(snap.kind) if name == "chat" else (
        "ollama" if snap.kind == "ollama" else "openai")
    if name == "rerank":
        kind = "local" if snap.kind in ("local", "onnx") else ("none" if not snap.kind else "http")
    return AISlot(
        slot=name, kind=kind, enabled=enabled and snap.enabled,
        provider_id=snap.id, provider_name=snap.name,
        base_url=snap.base_url, api_key=snap.api_key, model=model,
        dim=dim or 0, dim_explicit=dim is not None, models=snap.models,
    )


def _build(values: dict[str, str], snapshots: dict[int, ProviderSnapshot]) -> EffectiveAIConfig:
    global_enabled = _as_bool(values.get("ai_defense_enabled"), True)
    chat = _slot(values, snapshots, name="chat", id_key="ai_chat_provider_id",
                 model_key="ai_chat_model", enabled=global_enabled)
    embedding = _slot(values, snapshots, name="embedding", id_key="ai_embedding_provider_id",
                      model_key="ai_embedding_model", enabled=global_enabled,
                      dim_key="ai_embedding_dim")
    rerank = _slot(values, snapshots, name="rerank", id_key="ai_rerank_provider_id",
                   model_key="ai_rerank_model", enabled=global_enabled)

    mode = test_mode()
    # 测试替身: mock = 会话与向量都走本地替身(离线跑通全链路); hash = 只把向量换成哈希,
    # 会话仍按真实绑定解析(用于只想避开 embed 服务的场景)。
    if mode in ("mock", "hash"):
        if mode == "mock":
            chat = AISlot(slot="chat", kind="mock", enabled=global_enabled,
                          provider_id=chat.provider_id, provider_name=chat.provider_name,
                          base_url=chat.base_url, api_key=chat.api_key, model=chat.model,
                          models=chat.models)
        embedding = AISlot(slot="embedding", kind="hash", enabled=global_enabled,
                           provider_id=embedding.provider_id,
                           provider_name=embedding.provider_name,
                           base_url=embedding.base_url, api_key=embedding.api_key,
                           model=embedding.model, dim=embedding.dim,
                           dim_explicit=embedding.dim_explicit, models=embedding.models)

    return EffectiveAIConfig(chat=chat, embedding_slot=embedding, rerank_slot=rerank)


def _db_values(db=None) -> dict[str, str]:
    """读取数据库里的绑定键; db=None 时自己开短连接; 异常一律退回"全部未设置"."""
    own = db is None
    session = db
    try:
        if own:
            from app.db.mysql import SessionLocal

            session = SessionLocal()
        from app.shared.sysconfig import get_all

        return dict(get_all(session))
    except Exception as exc:  # noqa: BLE001 — 库不可用不允许把 AI 调用打成 500
        logger.warning("读取 AI 绑定配置失败, 按未配置处理: %s: %s", type(exc).__name__, exc)
        return {}
    finally:
        if own and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


def _signature(values: dict[str, str]) -> tuple:
    return (
        test_mode(),
        _pick(values, "ai_defense_enabled"),
        _pick(values, "ai_chat_provider_id"), _pick(values, "ai_chat_model"),
        _pick(values, "ai_embedding_provider_id"), _pick(values, "ai_embedding_model"),
        _pick(values, "ai_embedding_dim"),
        _pick(values, "ai_rerank_provider_id"), _pick(values, "ai_rerank_model"),
    )


_lock = threading.Lock()
_cached: EffectiveAIConfig | None = None
_cached_at = 0.0
_cached_sig: tuple = ()


def resolve_ai_config(db=None, *, use_cache: bool = True) -> EffectiveAIConfig:
    """解析当前生效的 AI 配置; db 可复用请求会话(没有会话时自己开短连接)."""
    global _cached, _cached_at, _cached_sig

    values = _db_values(db)
    signature = _signature(values)
    if use_cache:
        with _lock:
            if (_cached is not None and _cached_sig == signature
                    and (time.monotonic() - _cached_at) < CACHE_TTL_SECONDS):
                return _cached
    ids = {_as_int(values.get(key)) or 0 for key in
           ("ai_chat_provider_id", "ai_embedding_provider_id", "ai_rerank_provider_id")}
    snapshots = load_provider_snapshots(db, ids)
    config = _build(values, snapshots)
    with _lock:
        _cached = config
        _cached_at = time.monotonic()
        _cached_sig = signature
    return config


def invalidate_ai_config() -> None:
    """配置变更后立刻失效: 清缓存 + 重建工厂单例(否则进程仍用旧 key/model 的客户端)."""
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
    try:
        from app.ai.rerank.factory import reset_reranker_cache

        reset_reranker_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("重置精排工厂失败: %s: %s", type(exc).__name__, exc)
