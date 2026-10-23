"""系统配置模块: 管理端读写 + 登录前可用的公开配置(roadmap 八十五章).

密钥字段(ai_llm_api_key / ai_embedding_api_key / ai_rerank_api_key)一律不出明文:
- 读取经 mask_secrets 掩码, 任何响应里都不会出现真 Key;
- 写入时若提交的是掩码(*** 开头)或空串, 视为"不修改" —— 管理员在页面上只改别的字段,
  不会把已填好的 Key 擦掉; 需要真正清空时提交哨兵值 __CLEAR__;
- 写入成功后失效生效配置缓存并重置两个工厂单例, 新 Base URL / Key / 模型免重启生效。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.shared.sysconfig import (
    FIELDS,
    SECRET_FIELDS,
    SystemConfigError,
    get_all,
    mask_secrets,
    update_configs,
)

router = APIRouter(tags=["SystemConfig"])

# 清空密钥的显式哨兵: 空串与掩码都表示"不修改", 想删掉 Key 必须用这个值
CLEAR_SECRET_SENTINEL = "__CLEAR__"
SECRET_KEYS = (
    ("llm", "ai_llm_api_key"),
    ("embedding", "ai_embedding_api_key"),
    ("rerank", "ai_rerank_api_key"),
)


def _secret_changed(raw: str) -> bool:
    """提交值是否代表"真的改密钥": 空串与掩码(*** 开头)都只是页面回填."""
    value = (raw or "").strip()
    if not value or value.startswith("***"):
        return False
    return True


def _has_keys(unmasked: dict) -> dict:
    """按未掩码值判断每个能力是否配过 Key(前端用来显示"已配置/未配置")."""
    return {name: bool((unmasked.get(key) or "").strip()) for name, key in SECRET_KEYS}


@router.get("/config/public")
def public_config(db: Session = Depends(get_db)):
    """登录页/布局可读取的公开配置, 不含任何敏感项."""
    cfg = get_all(db)
    return ResponseUtil.success({
        "system_name": cfg["system_name"],
        "school_name": cfg["school_name"],
        "ai_defense_enabled": cfg["ai_defense_enabled"] == "true",
    })


class ConfigUpdateRequest(BaseModel):
    items: dict[str, str] = Field(min_length=1, max_length=20)


@router.get("/admin/system-configs")
def list_configs(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """全部配置项: 值已掩码(密钥不出明文), 结构仍是 [{key, value, description, type}]."""
    cfg = get_all(db)
    masked = mask_secrets(cfg)
    return ResponseUtil.success([{
        "key": key, "value": masked[key], "description": desc, "type": vtype,
    } for key, (_, desc, vtype) in FIELDS.items()])


@router.put("/admin/system-configs")
def update_configs_api(body: ConfigUpdateRequest, user: CurrentUser = Depends(require_admin),
                       db: Session = Depends(get_db)):
    payload: dict[str, str] = {}
    for key, raw in body.items.items():
        if key in SECRET_FIELDS:
            value = (raw or "").strip()
            if value == CLEAR_SECRET_SENTINEL:
                payload[key] = ""
            elif _secret_changed(value):
                payload[key] = value
            continue
        payload[key] = raw
    if not payload:
        raise BusinessException("没有需要变更的配置项: 密钥留空或保持掩码即表示不修改", 40001)
    try:
        values = update_configs(db, payload, user.id)
    except SystemConfigError as exc:
        raise BusinessException(str(exc), 40001)
    # 生效配置有 5s 内存缓存, 并且工厂拿着单例: 两者都要立刻失效, 否则新 Key 要等重启
    from app.ai.runtime_config import invalidate_ai_config

    invalidate_ai_config()
    return ResponseUtil.success(mask_secrets(values), message="配置已更新并即时生效")


@router.get("/admin/ai-model-info")
def ai_model_info(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """AI 模型信息: 真实探测结果 + 配置齐备度 + 索引一致性(needs_reindex)."""
    from app.ai.health import embedding_needs_reindex, probe_all
    from app.core.config import settings

    cfg = get_all(db)
    override = (cfg.get("ai_llm_model") or "").strip()
    # 走统一探测器: 模型不可用时这里展示原因, 而不是抛 500 或谎报正常
    ai = probe_all(db)
    llm, embedding, rerank = ai["llm"], ai["embedding"], ai["rerank"]
    return ResponseUtil.success({
        "llm_provider": llm["provider"],
        "llm_status": llm["status"],
        "llm_configured": llm.get("configured", False),
        "llm_detail": llm["detail"],
        "llm_openai_mode": settings.llm_use_openai,
        "llm_model_env": settings.llm_model,
        "ai_llm_model": override,
        "llm_model_effective": llm.get("model") or override or settings.llm_model,
        "embedding_provider": embedding["provider"],
        "embedding_status": embedding["status"],
        "embedding_configured": embedding.get("configured", False),
        "embedding_detail": embedding["detail"],
        "embedding_dim": embedding["dim"],
        "embedding_fingerprint": embedding["fingerprint"],
        "embedding_model": embedding["model"] or None,
        "has_keys": _has_keys(cfg),
        "needs_reindex": embedding_needs_reindex(db),
        "rerank_status": rerank["status"],
        "rerank_available": rerank["available"],
        "ollama_reachable": ai["ollama"]["reachable"],
        "vector_store_mode": ai["vector_store"]["mode"],
    })
