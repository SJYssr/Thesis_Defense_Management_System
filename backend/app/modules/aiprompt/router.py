"""Prompt 模板管理(roadmap 七十三章): 管理端查看/覆盖/停用/重置 AI 场景 System Prompt.

- 场景清单以代码注册表(app/ai/prompts.PROMPT_SPECS)为准, 不允许凭空新增场景
- 修改产生新版本并写入 ai_prompt_template_version 历史
- 停用后该场景回退内置默认; 防注入安全声明由系统强制追加, 不在可编辑内容中
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import PROMPT_SPECS, invalidate_prompt_cache
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIPromptTemplate, AIPromptTemplateVersion
from app.models.system import OperationLog

router = APIRouter(prefix="/admin/ai-prompts", tags=["AI-Prompt"])

MIN_PROMPT_LEN = 20
MAX_PROMPT_LEN = 8000


def _get_row(db: Session, code: str) -> AIPromptTemplate | None:
    return db.execute(select(AIPromptTemplate).where(AIPromptTemplate.code == code)).scalar_one_or_none()


def _require_spec(code: str):
    spec = PROMPT_SPECS.get(code)
    if spec is None:
        raise ResourceNotFoundException(f"未知 Prompt 场景: {code}")
    return spec


def _record_history(db: Session, code: str, version: int, content: str | None,
                    action: str, operator_id: int) -> None:
    db.add(AIPromptTemplateVersion(code=code, version=version, system_prompt=content,
                                   action=action, operator_id=operator_id,
                                   created_at=datetime.now()))


def _log_op(db: Session, user: CurrentUser, action: str, code: str, version: int) -> None:
    db.add(OperationLog(user_id=user.id, username=user.username, module="AI_PROMPT", action=action,
                        detail=f"{code} v{version}"))


@router.get("")
def list_prompts(_: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """全部场景: 注册表内置默认 + 数据库覆盖状态合并返回."""
    rows = {r.code: r for r in db.execute(select(AIPromptTemplate)).scalars()}
    items = []
    for code, spec in PROMPT_SPECS.items():
        row = rows.get(code)
        customized = row is not None
        enabled = customized and row.status == 1
        items.append({
            "code": code,
            "name": (row.name if customized and row.name else spec.name),
            "description": spec.description,
            "untrusted": spec.untrusted,
            "customized": customized,
            "enabled": enabled,
            "version": row.version if customized else 0,
            "updated_at": row.updated_at.isoformat() if customized and row.updated_at else None,
            "system_prompt": (row.system_prompt if enabled and row.system_prompt else spec.default_system),
            "source": "DB" if enabled else "BUILT_IN",
        })
    return ResponseUtil.success(items)


class PromptUpdateRequest(BaseModel):
    system_prompt: str = Field(min_length=MIN_PROMPT_LEN, max_length=MAX_PROMPT_LEN)
    name: str | None = Field(default=None, max_length=128)


@router.put("/{code}")
def update_prompt(code: str, body: PromptUpdateRequest, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """覆盖场景 System Prompt: 新建或更新, 版本号+1 并留痕.

    D69: 编辑内容不动 status —— 启停只由 POST /{code}/status 显式控制,
    否则"已停用的场景"会被一次内容编辑静默复活。
    """
    spec = _require_spec(code)
    content = body.system_prompt.strip()
    if len(content) < MIN_PROMPT_LEN:
        raise BusinessException(f"Prompt 内容过短(至少{MIN_PROMPT_LEN}字符)", 40001)

    row = _get_row(db, code)
    if row is None:
        row = AIPromptTemplate(code=code, name=body.name or spec.name,
                               system_prompt=content, version=1, status=1, updated_by=user.id)
        db.add(row)
        version = 1
    else:
        if body.name:
            row.name = body.name
        row.system_prompt = content
        row.version = (row.version or 0) + 1
        # 不回写 status: 停用状态下编辑内容仍保持停用(D69)
        row.updated_by = user.id
        version = row.version
    _record_history(db, code, version, content, "UPDATE", user.id)
    _log_op(db, user, "UPDATE_PROMPT", code, version)
    db.commit()
    invalidate_prompt_cache(code)
    return ResponseUtil.success({"code": code, "version": version}, message="Prompt 已更新并即时生效")


class PromptStatusRequest(BaseModel):
    enabled: bool


@router.post("/{code}/status")
def toggle_status(code: str, body: PromptStatusRequest, user: CurrentUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """停用后该场景回退内置默认(自定义内容保留, 可再次启用)."""
    _require_spec(code)
    row = _get_row(db, code)
    if row is None:
        raise ResourceNotFoundException("该场景尚未自定义, 无需停用")
    row.status = 1 if body.enabled else 0
    row.updated_by = user.id
    _record_history(db, code, row.version, row.system_prompt, "STATUS", user.id)
    _log_op(db, user, "ENABLE_PROMPT" if body.enabled else "DISABLE_PROMPT", code, row.version)
    db.commit()
    invalidate_prompt_cache(code)
    return ResponseUtil.success({"code": code, "enabled": body.enabled},
                                message="已启用自定义内容" if body.enabled else "已停用自定义内容, 回退内置默认")


@router.post("/{code}/reset")
def reset_prompt(code: str, user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """内容重置为内置默认(版本号+1 留痕, 行保留供审计).

    D69: 重置同样不动 status —— 停用的场景重置后仍是停用, 回退内置默认;
    需要启用时显式调用 POST /{code}/status {"enabled": true}。
    """
    spec = _require_spec(code)
    row = _get_row(db, code)
    if row is None:
        raise ResourceNotFoundException("该场景尚未自定义")
    row.system_prompt = spec.default_system
    row.version = (row.version or 0) + 1
    # 保持原 status(停用即停用), 启停只走 /status 接口(D69)
    row.updated_by = user.id
    _record_history(db, code, row.version, row.system_prompt, "RESET", user.id)
    _log_op(db, user, "RESET_PROMPT", code, row.version)
    db.commit()
    invalidate_prompt_cache(code)
    return ResponseUtil.success({"code": code, "version": row.version}, message="已恢复内置默认内容")


@router.get("/{code}/versions")
def prompt_versions(code: str, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """版本历史倒序: 每次内容修改/停用启用的内容快照."""
    _require_spec(code)
    rows = db.execute(
        select(AIPromptTemplateVersion).where(AIPromptTemplateVersion.code == code)
        .order_by(AIPromptTemplateVersion.id.desc()).limit(50)
    ).scalars().all()
    return ResponseUtil.success([{
        "version": r.version,
        "action": r.action,
        "system_prompt": r.system_prompt,
        "operator_id": r.operator_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows])
