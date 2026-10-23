"""AI 供应商凭证的落库加密(Fernet)。

为什么需要它: API Key 一旦明文入库, 任何一次库导出 / 慢查询日志 / 备份快照都会泄露。
密钥由 settings.secret_key 派生 —— 密钥本身不落库, 与应用签名密钥同源。
secret_key 更换后旧密文无法解开: decrypt 返回空串让上层提示"重新填写 Key",
而不是抛异常把整条 AI 链路打挂。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(plain: str | None) -> str | None:
    """明文 Key -> 密文; 空值返回 None(表示该供应商无需 Key)。"""
    text = (plain or "").strip()
    if not text:
        return None
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_api_key(cipher: str | None) -> str:
    """密文 -> 明文; 解不开(secret_key 变更)时返回空串, 由上层提示重填。"""
    if not cipher:
        return ""
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def key_hint(plain: str | None) -> str:
    """回显用: 只暴露后四位。前端永远拿不到完整 Key。"""
    text = (plain or "").strip()
    if not text:
        return ""
    # 短 Key 连后四位都不暴露: 4 位就能猜出大半的密钥等于没掩码
    if len(text) < 12:
        return "***"
    return "***" + text[-4:]
