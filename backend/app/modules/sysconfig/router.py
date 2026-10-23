"""系统配置模块: 管理端读写 + 登录前可用的公开配置(roadmap 八十五章).

P11 起本表只存非凭证配置(系统名称 / 上传上限 / AI 能力绑定与开关)。
AI 的 Base URL 与 API Key 属于 ai_provider 表(crypto 加密落库, GET 只回掩码),
不再经由本模块按能力各填一遍 —— 见 app/ai/providers.py 与 /admin/ai/providers。

写入成功后立即失效生效配置缓存并重置工厂单例, 绑定变更免重启生效。
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
    SystemConfigError,
    get_all,
    mask_secrets,
    update_configs,
)

router = APIRouter(tags=["SystemConfig"])


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
    payload: dict[str, str] = {key: raw for key, raw in body.items.items()}
    if not payload:
        raise BusinessException("没有需要变更的配置项", 40001)
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
    from app.ai.providers import bindings

    # 走统一探测器: 模型不可用时这里展示原因, 而不是抛 500 或谎报正常
    ai = probe_all(db)
    llm, embedding, rerank = ai["llm"], ai["embedding"], ai["rerank"]
    bound = bindings(db)
    return ResponseUtil.success({
        "enabled": bound["enabled"],
        "bindings": bound["slots"],
        "llm_provider": llm["provider"],
        "llm_status": llm["status"],
        "llm_configured": llm.get("configured", False),
        "llm_detail": llm["detail"],
        "llm_model": llm.get("model") or "",
        "embedding_provider": embedding["provider"],
        "embedding_status": embedding["status"],
        "embedding_configured": embedding.get("configured", False),
        "embedding_detail": embedding["detail"],
        "embedding_dim": embedding["dim"],
        "embedding_fingerprint": embedding["fingerprint"],
        "embedding_model": embedding["model"] or "",
        "needs_reindex": embedding_needs_reindex(db),
        "rerank_status": rerank["status"],
        "rerank_available": rerank["available"],
        "rerank_detail": rerank["detail"],
        "ollama_reachable": ai["ollama"]["reachable"],
        "vector_store_mode": ai["vector_store"]["mode"],
    })
