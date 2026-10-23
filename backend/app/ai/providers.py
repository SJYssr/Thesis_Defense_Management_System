"""AI 供应商: 持久化 / 凭证加密 / 模型清单 / 连通性探测 / 能力绑定 / 旧配置折算(P11).

一个供应商 = 一份 Base URL + 一份 API Key, 它同时可以提供 chat 模型和 embedding 模型
(Dify / Open WebUI / LiteLLM 的共同口径)。管理端因此只需要三步:
  1) 添加供应商(可从预设一键填 Base URL);
  2) 点"获取模型"从 /v1/models(ollama 走 /api/tags)拉清单;
  3) 三个下拉把 会话 / 向量 / 精排 绑定到"某供应商的某模型"。
Base URL 与 Key 各只填一次, 不再按能力重复; 也没有任何 AI 环境变量参与解析。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.crypto import decrypt_api_key, encrypt_api_key, key_hint
from app.ai.net import direct_opener, is_loopback_url
from app.core.exceptions import BusinessException, ConflictException, ResourceNotFoundException
from app.models.ai import AIProvider
from app.models.system import SystemConfig

logger = logging.getLogger("app.ai.providers")

# 供应商类型 = 预设; custom 表示"任意 OpenAI 兼容服务", 只需要 Base URL
KINDS = ("openai", "deepseek", "zhipu", "dashscope", "moonshot", "ollama", "custom")

# 代码内置预设(不是环境变量): 选中即填好 Base URL 与常见模型名, 用户只需补 Key
PRESETS: tuple[dict, ...] = (
    {
        "kind": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
        "needs_key": True, "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "OpenAI 兼容; 向量模型需另配供应商",
    },
    {
        "kind": "zhipu", "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "needs_key": True, "models": ["glm-4-flash", "glm-4-plus", "embedding-3"],
        "note": "同时提供 chat 与 embedding-3",
    },
    {
        "kind": "dashscope", "label": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "needs_key": True, "models": ["qwen-plus", "text-embedding-v3"],
        "note": "OpenAI 兼容模式端点",
    },
    {
        "kind": "moonshot", "label": "Moonshot", "base_url": "https://api.moonshot.cn/v1",
        "needs_key": True, "models": ["moonshot-v1-8k"],
        "note": "OpenAI 兼容; 无向量模型",
    },
    {
        "kind": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1",
        "needs_key": True, "models": ["gpt-4o-mini", "text-embedding-3-small"],
        "note": "官方; 需可访问外网",
    },
    {
        "kind": "ollama", "label": "本地 ollama", "base_url": "http://127.0.0.1:11434",
        "needs_key": False, "models": [],
        "note": "无需 Key 与网络, 但需要本机 ollama 已启动并 pull 过模型",
    },
    {
        "kind": "custom", "label": "自定义(OpenAI 兼容)", "base_url": "",
        "needs_key": False, "models": [],
        "note": "任何实现 /v1/chat/completions 与 /v1/embeddings 的服务",
    },
)

# 旧版本(P10)的 system_config 键: 迁移时折算成供应商行后删除。
# 只列"与新设计不同名"的键 —— ai_embedding_model / ai_rerank_model 在新旧两版里同名
# 且语义一致(模型名), 若也列进来, 迁移写完绑定键后下一次启动会再识别成旧值, 反复重建供应商。
LEGACY_KEYS: tuple[str, ...] = (
    "ai_llm_enabled", "ai_llm_provider", "ai_llm_base_url", "ai_llm_api_key", "ai_llm_model",
    "ai_embedding_enabled", "ai_embedding_provider", "ai_embedding_base_url",
    "ai_embedding_api_key",
    "ai_rerank_enabled", "ai_rerank_provider", "ai_rerank_base_url",
    "ai_rerank_api_key", "ai_rerank_model_dir",
)
# 新旧同名的模型名键: 不删除(新设计继续用), 但旧值要参与折算
CARRY_OVER_KEYS: tuple[str, ...] = ("ai_embedding_model", "ai_embedding_dim", "ai_rerank_model")

PROBE_TIMEOUT_SECONDS = 8.0
DETAIL_LIMIT = 480


def _truthy(raw: object, default: bool = True) -> bool:
    text = str(raw if raw is not None else "").strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def _as_int(raw: object) -> int | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def normalize_base_url(raw: str, kind: str) -> str:
    """归一 Base URL: ollama 存根地址(去掉 /v1), 其他保持原样但必须以 http(s) 开头."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise BusinessException("Base URL 不能为空")
    if not url.startswith(("http://", "https://")):
        raise BusinessException("Base URL 必须以 http:// 或 https:// 开头")
    if kind == "ollama" and url.endswith("/v1"):
        return url[:-3].rstrip("/")
    return url


def openai_base_url(base_url: str) -> str:
    """把 Base URL 折算成 OpenAI 兼容基址(含 /v1); ollama 的兼容层同样挂在 /v1."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else base + "/v1"


def _models_url(base_url: str) -> str:
    return openai_base_url(base_url) + "/models"


def _tags_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/") + "/api/tags"


def _http_json(url: str, api_key: str = "", timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """极简 GET JSON: 回环地址绕系统代理(见 app/ai/net.py), 云端走默认网络设置."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    if is_loopback_url(url):
        response = direct_opener().open(request, timeout=timeout)
    else:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — 地址来自配置
    with response:
        raw = response.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _extract_models(payload: dict) -> list[str]:
    """兼容两种形状: OpenAI {data:[{id}]} 与 ollama {models:[{name}]}."""
    items = payload.get("data")
    if not isinstance(items, list):
        items = payload.get("models")
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return sorted(set(names))


@dataclass(frozen=True)
class ProviderSnapshot:
    """脱离 ORM 会话后的供应商快照(运行时解析只认它, 不持有 Session)."""

    id: int
    name: str
    kind: str
    base_url: str
    api_key: str
    enabled: bool
    models: tuple[str, ...]

    @property
    def openai_base(self) -> str:
        return openai_base_url(self.base_url)


def _decode_models(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if str(item).strip())


def snapshot_of(row: AIProvider) -> ProviderSnapshot:
    return ProviderSnapshot(
        id=int(row.id),
        name=row.name,
        kind=(row.kind or "custom").strip().lower(),
        base_url=(row.base_url or "").strip().rstrip("/"),
        api_key=decrypt_api_key(row.api_key_enc),
        enabled=bool(row.enabled),
        models=_decode_models(row.models_json),
    )


def load_provider_snapshots(db: Session | None, ids: set[int]) -> dict[int, ProviderSnapshot]:
    """批量取快照; db=None 时自开短连接. 任何异常都退化为空(不让 AI 调用打成 500)."""
    wanted = sorted({int(i) for i in ids if i})
    if not wanted:
        return {}
    own = db is None
    session = db
    try:
        if own:
            from app.db.mysql import SessionLocal

            session = SessionLocal()
        rows = session.execute(select(AIProvider).where(AIProvider.id.in_(wanted))).scalars().all()
        return {int(row.id): snapshot_of(row) for row in rows}
    except Exception as exc:  # noqa: BLE001 — 库不可用时按"未配置"处理并记日志
        logger.warning("读取 AI 供应商失败: %s: %s", type(exc).__name__, exc)
        return {}
    finally:
        if own and session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


def view_of(row: AIProvider) -> dict:
    """管理端视图: 永远不含完整 Key, 只给 has_key 与后四位提示."""
    api_key = decrypt_api_key(row.api_key_enc)
    return {
        "id": int(row.id),
        "name": row.name,
        "kind": (row.kind or "custom").strip().lower(),
        "base_url": row.base_url,
        "openai_base_url": openai_base_url(row.base_url),
        "enabled": bool(row.enabled),
        "has_key": bool(api_key),
        "key_hint": key_hint(api_key),
        "cipher_broken": bool(row.api_key_enc) and not api_key,
        "models": list(_decode_models(row.models_json)),
        "models_refreshed_at": row.models_refreshed_at.isoformat() if row.models_refreshed_at else None,
        "last_status": row.last_status,
        "last_detail": row.last_detail,
        "last_probe_at": row.last_probe_at.isoformat() if row.last_probe_at else None,
    }


def list_providers(db: Session) -> list[dict]:
    rows = db.execute(select(AIProvider).order_by(AIProvider.id)).scalars().all()
    return [view_of(row) for row in rows]


def get_provider(db: Session, provider_id: int) -> AIProvider:
    row = db.get(AIProvider, int(provider_id))
    if row is None:
        raise ResourceNotFoundException("供应商不存在")
    return row


def _assert_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    stmt = select(AIProvider).where(AIProvider.name == name)
    if exclude_id is not None:
        stmt = stmt.where(AIProvider.id != int(exclude_id))
    if db.execute(stmt).scalars().first() is not None:
        raise ConflictException(f"供应商名称已存在: {name}")


def create_provider(
    db: Session, *, name: str, kind: str, base_url: str,
    api_key: str | None = None, enabled: bool = True,
) -> dict:
    clean_name = (name or "").strip()
    if not clean_name:
        raise BusinessException("供应商名称不能为空")
    if len(clean_name) > 64:
        raise BusinessException("供应商名称过长(最多 64 字符)")
    clean_kind = (kind or "custom").strip().lower()
    if clean_kind not in KINDS:
        raise BusinessException(f"未知供应商类型: {kind}; 可选 {'/'.join(KINDS)}")
    row = AIProvider(
        name=clean_name,
        kind=clean_kind,
        base_url=normalize_base_url(base_url, clean_kind),
        api_key_enc=encrypt_api_key(api_key),
        enabled=1 if enabled else 0,
    )
    _assert_name_free(db, clean_name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return view_of(row)


def update_provider(db: Session, provider_id: int, **fields) -> dict:
    row = get_provider(db, provider_id)
    if "name" in fields and fields["name"] is not None:
        clean_name = str(fields["name"]).strip()
        if not clean_name:
            raise BusinessException("供应商名称不能为空")
        _assert_name_free(db, clean_name, exclude_id=row.id)
        row.name = clean_name
    if "kind" in fields and fields["kind"]:
        clean_kind = str(fields["kind"]).strip().lower()
        if clean_kind not in KINDS:
            raise BusinessException(f"未知供应商类型: {fields['kind']}")
        row.kind = clean_kind
    if "base_url" in fields and fields["base_url"] is not None:
        row.base_url = normalize_base_url(str(fields["base_url"]), row.kind)
    if "enabled" in fields and fields["enabled"] is not None:
        row.enabled = 1 if fields["enabled"] else 0
    # api_key 三态: 不传/None=不动; 空串=清空; 其他=覆盖(前端拿不到明文, 省略即不动)
    if fields.get("api_key") is not None:
        text = str(fields["api_key"]).strip()
        if text:
            row.api_key_enc = encrypt_api_key(text)
        # 空串由调用方用 clear_key=True 显式表达, 避免"没填"被误当成"清空"
    if fields.get("clear_key"):
        row.api_key_enc = None
    db.commit()
    db.refresh(row)
    _invalidate()
    return view_of(row)


def delete_provider(db: Session, provider_id: int) -> None:
    row = get_provider(db, provider_id)
    used = _bound_slots(db, int(row.id))
    if used:
        raise BusinessException(
            "该供应商仍被 " + "、".join(used) + " 绑定, 请先把对应能力改绑到别的供应商"
        )
    db.delete(row)
    db.commit()
    _invalidate()


_PROBE_STATUS = {"ok", "degraded", "down"}


def refresh_models(db: Session, provider_id: int) -> dict:
    """拉模型清单并落库; 失败不抛给前端, 以 status/detail 呈现(供应商可能只是暂时离线)."""
    row = get_provider(db, provider_id)
    snap = snapshot_of(row)
    started = time.monotonic()
    models: list[str] = []
    detail = ""
    status = "ok"
    try:
        if snap.kind == "ollama":
            models = _extract_models(_http_json(_tags_url(snap.base_url), snap.api_key))
        if not models:
            models = _extract_models(_http_json(_models_url(snap.base_url), snap.api_key))
        if not models:
            status = "degraded"
            detail = "接口可达但没有返回任何模型, 请确认该供应商已加载模型"
    except urllib.error.HTTPError as exc:
        status = "down"
        detail = f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        status = "down"
        detail = f"无法连接: {exc.reason}"
    except (ValueError, OSError) as exc:
        status = "down"
        detail = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.monotonic() - started) * 1000)
    if status == "ok":
        detail = f"{len(models)} 个模型, {latency_ms}ms"
    row.models_json = json.dumps(models, ensure_ascii=False)
    row.models_refreshed_at = datetime.now()
    row.last_status = status if status in _PROBE_STATUS else "down"
    row.last_detail = detail[:DETAIL_LIMIT]
    row.last_probe_at = datetime.now()
    db.commit()
    db.refresh(row)
    return {"status": row.last_status, "detail": row.last_detail, "models": models,
            "latency_ms": latency_ms, "provider": view_of(row)}


def probe_provider(db: Session, provider_id: int) -> dict:
    """连通性探测 = 拉一次模型清单(同一请求即可证明 Key/网络/地址三者都对)."""
    return refresh_models(db, provider_id)


SLOT_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("chat", "ai_chat_provider_id", "ai_chat_model", "会话模型"),
    ("embedding", "ai_embedding_provider_id", "ai_embedding_model", "向量模型"),
    ("rerank", "ai_rerank_provider_id", "ai_rerank_model", "精排模型"),
)


def _bound_slots(db: Session, provider_id: int) -> list[str]:
    from app.shared.sysconfig import get_all

    values = dict(get_all(db))
    used: list[str] = []
    for slot, id_key, model_key, label in SLOT_KEYS:
        if _as_int(values.get(id_key)) == int(provider_id):
            used.append(f"{label}({slot})")
    return used


def bindings(db: Session) -> dict:
    """当前三个能力位的绑定; provider 不存在时显式标记 broken(供应商被删/停用)."""
    from app.shared.sysconfig import get_all

    values = dict(get_all(db))
    snapshots = load_provider_snapshots(
        db, {_as_int(values.get(id_key)) or 0 for _, id_key, _, _ in SLOT_KEYS}
    )
    result: dict = {"enabled": _truthy(values.get("ai_defense_enabled"), True), "slots": {}}
    for slot, id_key, model_key, label in SLOT_KEYS:
        provider_id = _as_int(values.get(id_key))
        model = str(values.get(model_key) or "").strip()
        snap = snapshots.get(provider_id) if provider_id else None
        result["slots"][slot] = {
            "label": label,
            "provider_id": provider_id,
            "provider_name": snap.name if snap else "",
            "model": model,
            "configured": bool(provider_id and snap and snap.enabled and model),
            "broken": bool(provider_id and snap is None),
            "provider_enabled": bool(snap.enabled) if snap else False,
        }
    result["embedding_dim"] = _as_int(values.get("ai_embedding_dim"))
    return result


def save_bindings(db: Session, payload: dict, updater_id: int | None = None) -> dict:
    """保存能力绑定. 只接受 provider_id + model, 不允许在这里夹带 Base URL/Key."""
    from app.shared.sysconfig import update_configs

    updates: dict[str, str] = {}
    for slot, id_key, model_key, label in SLOT_KEYS:
        block = payload.get(slot)
        if block is None:
            continue
        raw_id = block.get("provider_id")
        provider_id = _as_int(raw_id)
        model = str(block.get("model") or "").strip()
        if provider_id is not None:
            row = get_provider(db, provider_id)  # 不存在直接抛 ResourceNotFound
            if not row.enabled:
                raise BusinessException(f"{label}选择的供应商已停用: {row.name}")
        if provider_id is None:
            updates[id_key] = ""
            updates[model_key] = ""
            continue
        if not model:
            raise BusinessException(f"{label}必须选择一个模型名(可先点“获取模型”再选)")
        updates[id_key] = str(provider_id)
        updates[model_key] = model
    if "enabled" in payload and payload["enabled"] is not None:
        updates["ai_defense_enabled"] = "true" if payload["enabled"] else "false"
    if not updates:
        raise BusinessException("没有需要保存的绑定")
    update_configs(db, updates, updater_id)
    _invalidate()
    return bindings(db)


def presets() -> list[dict]:
    return [dict(item) for item in PRESETS]


def _invalidate() -> None:
    try:
        from app.ai.runtime_config import invalidate_ai_config

        invalidate_ai_config()
    except Exception as exc:  # noqa: BLE001 — 失效动作失败不该反向打断保存
        logger.warning("重置 AI 运行时配置缓存失败: %s: %s", type(exc).__name__, exc)


def migrate_legacy_config(db: Session) -> bool:
    """把 P10 的 16 个配置键折算成供应商行 + 绑定, 幂等(折算后旧键即删除).

    密钥加密需要 settings.secret_key, 迁移脚本(alembic)不该依赖应用配置,
    所以搬移放在应用层启动时做一次。
    """
    rows = db.execute(select(SystemConfig).where(SystemConfig.key.in_(LEGACY_KEYS))).scalars().all()
    legacy = {row.key: str(row.value or "").strip() for row in rows}
    if not any(legacy.values()):
        for row in rows:
            db.delete(row)
        if rows:
            db.commit()
        return False
    for row in db.execute(select(SystemConfig).where(
            SystemConfig.key.in_(CARRY_OVER_KEYS))).scalars().all():
        legacy.setdefault(row.key, str(row.value or "").strip())

    def ensure_provider(label: str, kind: str, base_url: str, api_key: str) -> int | None:
        if not base_url and not api_key:
            return None
        clean_kind = kind if kind in KINDS else "openai"
        resolved = base_url or ("http://127.0.0.1:11434" if clean_kind == "ollama" else "")
        if not resolved:
            return None
        name = label
        suffix = 1
        while db.execute(select(AIProvider).where(AIProvider.name == name)).scalars().first() is not None:
            suffix += 1
            name = f"{label}({suffix})"
        row = AIProvider(
            name=name,
            kind=clean_kind,
            base_url=normalize_base_url(resolved, clean_kind),
            api_key_enc=encrypt_api_key(api_key),
            enabled=1,
        )
        db.add(row)
        db.flush()
        return int(row.id)

    llm_kind = (legacy.get("ai_llm_provider") or "").lower()
    chat_id = ensure_provider(
        "默认供应商", "ollama" if llm_kind == "ollama" else "openai",
        legacy.get("ai_llm_base_url", ""), legacy.get("ai_llm_api_key", ""),
    )
    emb_kind = (legacy.get("ai_embedding_provider") or "").lower()
    same_as_chat = (
        legacy.get("ai_embedding_base_url", "") == legacy.get("ai_llm_base_url", "")
        and legacy.get("ai_embedding_api_key", "") == legacy.get("ai_llm_api_key", "")
    )
    embedding_id = chat_id if (chat_id and same_as_chat) else ensure_provider(
        "默认供应商(向量)", "ollama" if emb_kind == "ollama" else "openai",
        legacy.get("ai_embedding_base_url", ""), legacy.get("ai_embedding_api_key", ""),
    )

    updates: dict[str, str] = {}
    if chat_id:
        updates["ai_chat_provider_id"] = str(chat_id)
        updates["ai_chat_model"] = legacy.get("ai_llm_model", "")
    if embedding_id:
        updates["ai_embedding_provider_id"] = str(embedding_id)
        updates["ai_embedding_model"] = legacy.get("ai_embedding_model", "")
        updates["ai_embedding_dim"] = legacy.get("ai_embedding_dim", "")
    for key in LEGACY_KEYS:
        row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalars().first()
        if row is not None:
            db.delete(row)
    db.commit()
    if updates:
        from app.shared.sysconfig import update_configs

        update_configs(db, updates, None)
    _invalidate()
    logger.info(
        "旧版 AI 配置已折算为供应商: chat=%s embedding=%s (原值 %s)",
        chat_id, embedding_id, sorted(k for k, v in legacy.items() if v),
    )
    return True
