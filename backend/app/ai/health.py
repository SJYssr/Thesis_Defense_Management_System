"""AI 链路统一探测器: 让"模型没起来"在 /health 与管理端模型信息里一眼可见.

status 口径:
- ok              真实可用(来源可达且模型存在 / 云端真实推理成功)
- degraded        已配置但探测失败(连不上 / 鉴权失败 / 模型未安装 / 维度与设置不符)
- not_configured  系统设置与环境变量都没有 Base URL / API Key, 且 ollama 不可达 —— 纯"没配"
- down            显式选定来源当前不可达(如 provider=ollama 而 ollama 没起)
- disabled        显式离线实现(mock / hash)或该能力被管理员关闭(enabled=false)

硬约束:
1. 每个子探测都有超时(HTTP 超时 / 线程 join 超时), /health 不被模型冷启动拖住;
2. 子探测绝不抛异常 —— 异常统一转成该块 status + detail, 健康检查自己不能 500。

llm / embedding 块额外带 provider(生效值) 与 configured(是否配过云端来源), 供前端区分
"没配"与"配了但连不上"。
"""
import logging
import threading
import time
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("app.ai.health")

# 索引一致性: 当前向量指纹写在这里, 与索引重建流程共享
EMBEDDING_FINGERPRINT_KEY = "ai:embedding:fingerprint"
# 云侧真实推理探测上限(llm_timeout=120s 属于业务调用, 不能进健康检查)
CLOUD_PROBE_TIMEOUT_SECONDS = 10.0
# 本地探测(如向量库模式)的线程等待上限
LOCAL_PROBE_TIMEOUT_SECONDS = 2.0


def _llm_block(status, *, provider, configured, model="", latency_ms=None, detail=""):
    return {"status": status, "provider": provider, "configured": bool(configured),
            "model": model, "latency_ms": latency_ms, "detail": detail}


def _embedding_block(status, *, provider, configured, model="", latency_ms=None, detail="",
                     dim=0, fingerprint=""):
    return {"status": status, "provider": provider, "configured": bool(configured),
            "model": model, "latency_ms": latency_ms, "detail": detail,
            "dim": dim, "fingerprint": fingerprint}


def _llm_empty(status, detail, provider="unavailable", configured=False):
    return _llm_block(status, provider=provider, configured=configured, detail=detail)


def _embedding_empty(status, detail, provider="unavailable", configured=False):
    return _embedding_block(status, provider=provider, configured=configured, detail=detail)


def _guard(fn, fallback, label):
    """执行子探测: 任何异常都转成 fallback(status=down + detail), 绝不外逃."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — 健康检查必须始终给出结构
        logger.warning("AI 健康探测 %s 失败: %s: %s", label, type(exc).__name__, exc)
        out = dict(fallback)
        if "status" in out:
            out["status"] = "down"
        out["detail"] = "%s 探测异常: %s: %s" % (label, type(exc).__name__, exc)
        return out


def _run_bounded(fn, timeout, on_timeout):
    """在守护线程里跑可能阻塞的探测, 超时返回 on_timeout()(线程不回收, 但不阻塞响应)."""
    box = {}

    def _target():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 — 交给主线程处理
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return on_timeout()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _probe_ollama_block():
    from app.ai.llm.factory import probe_ollama

    info = probe_ollama()
    return {
        "reachable": bool(info.get("reachable")),
        "base_url": settings.ollama_base_url,
        "models": list(info.get("models") or []),
        "latency_ms": info.get("latency_ms"),
        "detail": str(info.get("detail") or ""),
    }


def _cloud_llm_probe(base_url, api_key, model):
    """对云端 OpenAI 兼容接口做一次真实最小补全(1 token), 用于 ok / degraded 判定."""
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key,
                    timeout=CLOUD_PROBE_TIMEOUT_SECONDS, max_retries=0)
    client.chat.completions.create(model=model,
                                   messages=[{"role": "user", "content": "ping"}],
                                   max_tokens=1, temperature=0)
    return True, "%s 真实补全成功(模型 %s)" % (base_url, model)


def _cloud_embedding_probe(base_url, api_key, model):
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key,
                    timeout=CLOUD_PROBE_TIMEOUT_SECONDS, max_retries=0)
    resp = client.embeddings.create(model=model, input=["ping"])
    dim = len(resp.data[0].embedding) if resp.data else 0
    return True, dim, "%s 真实向量化成功(模型 %s)" % (base_url, model)


def _probe_llm(db=None):
    from app.ai.llm.factory import AIServiceUnavailable, LLMFactory, ollama_model_present
    from app.ai.runtime_config import FIX_HINT, resolve_ai_config

    cfg = resolve_ai_config(db)
    provider = cfg.llm_provider
    configured = cfg.llm_configured

    if not cfg.llm_enabled:
        return _llm_block("disabled", provider=provider, configured=configured,
                          model=cfg.llm_model,
                          detail="ai_llm_enabled=false: LLM 已在系统设置中关闭")
    if provider == "mock":
        return _llm_block("disabled", provider="mock", configured=True, model="mock",
                          detail="LLM_PROVIDER=mock: 离线模板拼接, 输出不是真实模型回答")

    started = time.monotonic()
    try:
        service = LLMFactory.get(db)
    except AIServiceUnavailable as exc:  # 明确"没有可用来源": 原因原样展示
        detail = str(exc) or AIServiceUnavailable.message
        if configured:
            return _llm_block("degraded", provider=provider, configured=True,
                              model=cfg.llm_model, detail=detail)
        return _llm_block("not_configured", provider=provider, configured=False,
                          model=cfg.llm_model, detail="%s; 修复: %s" % (detail, FIX_HINT))
    # 其它异常不在这里吞: 交给 _guard 标注为探测异常(带异常类型, 便于定位)

    name = getattr(service, "name", "")
    latency_ms = int((time.monotonic() - started) * 1000)
    if name.startswith("ollama:"):
        model = getattr(service, "model", "") or cfg.ollama_llm_model
        probe = _probe_ollama_block()
        if not probe["reachable"]:
            return _llm_block("down", provider="ollama", configured=True, model=model,
                              detail=probe["detail"] or "ollama 不可达")
        if not ollama_model_present(probe["models"], model):
            return _llm_block("degraded", provider="ollama", configured=True, model=model,
                              latency_ms=probe.get("latency_ms"),
                              detail="ollama 可达但未安装模型 %s: 请执行 ollama pull %s"
                                     % (model, model))
        return _llm_block("ok", provider="ollama", configured=True, model=model,
                          latency_ms=probe.get("latency_ms") or latency_ms,
                          detail="%s 可达, 模型 %s 已安装(未做推理探测)"
                                 % (cfg.ollama_llm_base_url, model))

    if name.startswith("openai:"):
        model = getattr(service, "model", "") or cfg.llm_model
        try:
            _, detail = _cloud_llm_probe(cfg.llm_base_url, cfg.llm_api_key, model)
        except Exception as exc:  # noqa: BLE001
            return _llm_block("degraded", provider="openai", configured=True, model=model,
                              detail="云端补全探测失败: %s: %s" % (type(exc).__name__, exc))
        return _llm_block("ok", provider="openai", configured=True, model=model,
                          latency_ms=int((time.monotonic() - started) * 1000), detail=detail)

    return _llm_block("degraded", provider=name or "unknown", configured=configured,
                      model=getattr(service, "model", ""), latency_ms=latency_ms,
                      detail="来源未识别, 仅确认实例可构造")


def _probe_embedding(db=None):
    from app.ai.embedding.factory import EmbeddingFactory
    from app.ai.llm.factory import AIServiceUnavailable, ollama_model_present
    from app.ai.runtime_config import FIX_HINT, resolve_ai_config

    cfg = resolve_ai_config(db)
    provider = cfg.embedding_provider
    configured = cfg.embedding_configured

    if not cfg.embedding_enabled:
        return _embedding_block("disabled", provider=provider, configured=configured,
                                model=cfg.embedding_model, dim=cfg.embedding_dim,
                                detail="ai_embedding_enabled=false: 向量模型已在系统设置中关闭")

    started = time.monotonic()
    try:
        service = EmbeddingFactory.get(db)
    except AIServiceUnavailable as exc:
        detail = str(exc) or AIServiceUnavailable.message
        status = "degraded" if configured else "not_configured"
        if not configured:
            detail = "%s; 修复: %s" % (detail, FIX_HINT)
        return _embedding_block(status, provider=provider, configured=configured,
                                model=cfg.embedding_model, dim=cfg.embedding_dim, detail=detail)

    name = getattr(service, "name", "")
    model = getattr(service, "model", "")
    dim = int(getattr(service, "dim", 0) or 0)
    fingerprint = getattr(service, "fingerprint", "%s:%s" % (name, dim))
    latency_ms = int((time.monotonic() - started) * 1000)

    if name.startswith("local-hash"):
        return _embedding_block(
            "disabled", provider=name, configured=True, model="local-hash",
            latency_ms=latency_ms, dim=dim, fingerprint=fingerprint,
            detail="EMBEDDING_PROVIDER=hash: 本地哈希向量(纯词面匹配), 不是真实向量模型",
        )

    if name.startswith("ollama:"):
        probe = _probe_ollama_block()
        if not probe["reachable"]:
            return _embedding_block("down", provider="ollama", configured=True, model=model,
                                    dim=dim, fingerprint=fingerprint,
                                    detail=probe["detail"] or "ollama 不可达")
        if not ollama_model_present(probe["models"], model):
            return _embedding_block(
                "degraded", provider="ollama", configured=True, model=model,
                latency_ms=probe.get("latency_ms"), dim=dim, fingerprint=fingerprint,
                detail="ollama 可达但未安装模型 %s: 请执行 ollama pull %s" % (model, model),
            )
        return _embedding_block(
            "ok", provider="ollama", configured=True, model=model,
            latency_ms=probe.get("latency_ms") or latency_ms, dim=dim, fingerprint=fingerprint,
            detail="%s 可达, 模型 %s 已安装(维度取生效配置 %s)"
                   % (cfg.ollama_embedding_base_url, model, dim),
        )

    if name.startswith("openai:"):
        target = model or cfg.embedding_model
        try:
            _, actual_dim, detail = _cloud_embedding_probe(
                cfg.embedding_base_url, cfg.embedding_api_key, target)
        except Exception as exc:  # noqa: BLE001
            return _embedding_block("degraded", provider="openai", configured=True,
                                    model=model, dim=dim, fingerprint=fingerprint,
                                    detail="云端向量探测失败: %s: %s" % (type(exc).__name__, exc))
        if actual_dim and actual_dim != dim:
            if cfg.embedding_dim_explicit:
                return _embedding_block(
                    "degraded", provider="openai", configured=True, model=model,
                    latency_ms=int((time.monotonic() - started) * 1000), dim=actual_dim,
                    fingerprint="%s:%s" % (name, actual_dim),
                    detail="向量维度与系统设置不一致: ai_embedding_dim=%s, 模型实际输出 %s 维; "
                           "请修改系统设置里的维度并重建索引" % (dim, actual_dim),
                )
            logger.warning("云端 embedding 实际维度 %s 与配置 %s 不一致", actual_dim, dim)
            dim = actual_dim
            fingerprint = "%s:%s" % (name, dim)
        return _embedding_block(
            "ok", provider="openai", configured=True, model=target,
            latency_ms=int((time.monotonic() - started) * 1000), dim=dim,
            fingerprint=fingerprint, detail=detail)

    return _embedding_block("degraded", provider=name or "unknown", configured=configured,
                            model=model, latency_ms=latency_ms, dim=dim,
                            fingerprint=fingerprint,
                            detail="来源未识别, 仅确认实例可构造")


def _probe_rerank():
    provider = (settings.rerank_provider or "auto").strip().lower()
    model_dir = Path(settings.rerank_model_dir)
    base = {"status": "down", "provider": provider, "model_dir": str(model_dir),
            "available": False, "detail": ""}
    if provider == "none":
        return dict(base, status="disabled", detail="RERANK_PROVIDER=none: 跳过后精排")
    try:
        if not model_dir.is_dir():
            return dict(base, detail="模型目录不存在: %s" % model_dir)
        onnx_files = sorted(p.name for p in model_dir.glob("*.onnx"))
        has_tokenizer = (model_dir / "tokenizer.json").is_file()
        if not onnx_files:
            return dict(base, detail="目录内无 *.onnx: %s" % model_dir)
        if not has_tokenizer:
            return dict(base, detail="缺少 tokenizer.json: %s" % model_dir)
    except OSError as exc:
        return dict(base, detail="读取模型目录失败: %s" % exc)
    # 只报文件齐备, 不加载 onnx 会话(加载成本属于检索链路, 不属于健康检查)
    return dict(base, status="ok", available=True,
                detail="文件齐备: %s + tokenizer.json(未加载)" % ", ".join(onnx_files))


def _probe_vector_store():
    from app.ai.vector.store import vector_store

    def _mode():
        return str(vector_store.mode)

    try:
        mode = _run_bounded(_mode, LOCAL_PROBE_TIMEOUT_SECONDS, lambda: "unknown")
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量库模式探测失败: %s", exc)
        return {"mode": "unknown", "detail": "%s: %s" % (type(exc).__name__, exc)}
    if mode == "redis":
        detail = "RediSearch(KNN/HNSW)"
    elif mode == "unknown":
        detail = "未能确认向量库模式(探测超时)"
    else:
        detail = "MySQL 暴力余弦降级"
    return {"mode": mode, "detail": detail}


def probe_all(db=None):
    """统一探测 LLM / Embedding / Rerank / 向量库 / ollama; 结构见模块 docstring."""
    return {
        "llm": _guard(lambda: _probe_llm(db), _llm_empty("down", ""), "llm"),
        "embedding": _guard(lambda: _probe_embedding(db), _embedding_empty("down", ""),
                            "embedding"),
        "rerank": _guard(_probe_rerank,
                         {"status": "down", "provider": settings.rerank_provider,
                          "model_dir": settings.rerank_model_dir, "available": False,
                          "detail": ""}, "rerank"),
        "vector_store": _guard(_probe_vector_store, {"mode": "unknown", "detail": ""},
                               "vector_store"),
        "ollama": _guard(_probe_ollama_block,
                         {"reachable": False, "base_url": settings.ollama_base_url,
                          "models": [], "latency_ms": None, "detail": ""}, "ollama"),
    }


def current_embedding_fingerprint(db=None):
    """当前向量指纹; 取不到时返回空串(调用方按"未知"处理)."""
    from app.ai.embedding.factory import EmbeddingFactory

    try:
        return str(EmbeddingFactory.get(db).fingerprint)
    except Exception as exc:  # noqa: BLE001 — 管理端展示不能因模型不可用而 500
        logger.warning("无法获取当前 embedding 指纹: %s", exc)
        return ""


def embedding_needs_reindex(db=None):
    """Redis 里记录的指纹与当前模型不一致(或未知)时需要重建索引.

    键不存在 -> True(未知即需要重建); Redis 故障 -> False + warning
    (缓存故障不能把管理端页面打成错误, 也不能假装"需要重建")。
    """
    current = current_embedding_fingerprint(db)
    if not current:
        return True
    try:
        from app.db.redis import redis_client

        raw = redis_client.get(EMBEDDING_FINGERPRINT_KEY)
    except Exception as exc:  # noqa: BLE001 — Redis 异常不阻塞页面
        logger.warning("读取 embedding 指纹失败, 跳过索引一致性判断: %s", exc)
        return False
    if not raw:
        return True
    stored = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return stored.strip() != current


def mark_embedding_indexed(db=None):
    """索引重建完成后写入当前指纹(供索引流程调用); Redis 故障返回 False 不抛."""
    current = current_embedding_fingerprint(db)
    if not current:
        return False
    try:
        from app.db.redis import redis_client

        redis_client.set(EMBEDDING_FINGERPRINT_KEY, current)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 embedding 指纹失败: %s", exc)
        return False
