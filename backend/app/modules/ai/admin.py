"""AI 供应商与能力绑定的管理端接口(roadmap 八十五章 P11).

口径:
- 一个供应商 = 一份 Base URL + API Key, 同时可提供对话与向量能力;
- 管理端只维护"供应商"与"三个能力位绑定给谁 + 用哪个模型", 不在这里填维度/环境变量;
- 模型名一律从供应商的 /models(ollama 为 /api/tags) 拉取后下拉选择, 不做手填兜底;
- API Key 只写不读: 响应里永远是 has_key + ***后四位。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import providers as svc
from app.ai.health import embedding_needs_reindex, probe_all
from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db

router = APIRouter(prefix="/admin/ai", tags=["AI-Admin"])


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="custom", max_length=32)
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    clear_key: bool = False
    enabled: bool | None = None


class BindingSlot(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider_id: int | None = None
    model: str | None = None


class BindingsUpdate(BaseModel):
    enabled: bool | None = None
    chat: BindingSlot | None = None
    embedding: BindingSlot | None = None
    rerank: BindingSlot | None = None


@router.get("/presets")
def list_presets(_: CurrentUser = Depends(require_admin)):
    """预设只给"常见供应商的 Base URL 与推荐模型", 方便一次点选而不是背地址."""
    return ResponseUtil.success(svc.presets())


@router.get("/providers")
def list_providers(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    return ResponseUtil.success(svc.list_providers(db))


@router.post("/providers")
def create_provider(
    body: ProviderCreate,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ResponseUtil.success(svc.create_provider(
        db,
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        api_key=body.api_key,
        enabled=body.enabled,
    ))


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ResponseUtil.success(svc.update_provider(
        db,
        provider_id,
        name=body.name,
        kind=body.kind,
        api_key=body.api_key,
        clear_key=body.clear_key,
        enabled=body.enabled,
        base_url=body.base_url,
    ))


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    svc.delete_provider(db, provider_id)
    return ResponseUtil.success({"deleted": provider_id})


@router.post("/providers/{provider_id}/refresh")
def refresh_models(
    provider_id: int,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """拉一次模型清单: 同时验证 Base URL/Key/网络, 结果落库供下次打开页面直接展示."""
    return ResponseUtil.success(svc.refresh_models(db, provider_id))


@router.post("/providers/{provider_id}/test")
def test_provider(
    provider_id: int,
    _: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ResponseUtil.success(svc.probe_provider(db, provider_id))


@router.get("/bindings")
def get_bindings(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    return ResponseUtil.success(svc.bindings(db))


@router.put("/bindings")
def save_bindings(
    body: BindingsUpdate,
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_none=True)
    return ResponseUtil.success(svc.save_bindings(db, payload, user.id))


@router.get("/overview")
def overview(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """一屏看全: 供应商列表 + 绑定 + 三个能力位的真实探测结果."""
    ai = probe_all(db)
    bound = svc.bindings(db)
    return ResponseUtil.success({
        "enabled": bound["enabled"],
        "slots": bound["slots"],
        "embedding_dim": bound["embedding_dim"],
        "providers": svc.list_providers(db),
        "probe": {
            "llm": ai["llm"],
            "embedding": ai["embedding"],
            "rerank": ai["rerank"],
            "ollama": ai["ollama"],
            "vector_store": ai["vector_store"],
        },
        "needs_reindex": embedding_needs_reindex(db),
    })
