"""系统配置模块: 管理端读写 + 登录前可用的公开配置(roadmap 八十五章)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.permissions import CurrentUser, require_admin
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.shared.sysconfig import FIELDS, SystemConfigError, get_all, update_configs

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
    cfg = get_all(db)
    return ResponseUtil.success([{
        "key": key, "value": cfg[key], "description": desc, "type": vtype,
    } for key, (_, desc, vtype) in FIELDS.items()])


@router.put("/admin/system-configs")
def update_configs_api(body: ConfigUpdateRequest, user: CurrentUser = Depends(require_admin),
                       db: Session = Depends(get_db)):
    try:
        values = update_configs(db, body.items, user.id)
    except SystemConfigError as exc:
        raise BusinessException(str(exc), 40001)
    return ResponseUtil.success(values, message="配置已更新并即时生效")
