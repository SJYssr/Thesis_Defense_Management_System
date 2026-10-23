"""Cross-encoder 精排: ONNX Runtime 推理 bge-reranker-v2-m3(不引入 torch/transformers).

为什么要独立一层精排:
- 混合召回(向量 KNN + BM25)追求召回, 排序靠 RRF 只用到"名次", 丢掉了
  query-doc 的交叉语义; cross-encoder 把 (query, doc) 拼成一个序列过一遍模型,
  排序质量显著好于双塔向量相似度;
- 精排只对召回池(settings.rerank_recall)打分, 成本可控, 最终只留
  settings.rerank_keep 条进上下文。

工程约束:
1. 延迟加载 —— 首次 rerank() 才 import onnxruntime 并建 InferenceSession,
   web 启动、单测、离线脚本都不为精排付启动成本;
2. 优雅降级 —— 缺 model.onnx / tokenizer.json / onnxruntime / tokenizers,
   或推理抛异常, 一律 available=False 且 rerank() 原序直通, 问答链路不中断;
3. 纯 CPU: providers 固定 CPUExecutionProvider, 不依赖 GPU 运行时。
"""
from __future__ import annotations

import importlib.util
import logging
import math
import threading
from pathlib import Path

logger = logging.getLogger("app.ai.rerank")


def _sigmoid(x: float) -> float:
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class BaseReranker:
    """精排接口: 分数越大越相关, 返回 (原文下标记, 分数) 且按分数降序."""

    name = "base"
    available = False
    reason = ""

    def rerank(self, query: str, texts: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
        raise NotImplementedError

    @staticmethod
    def _passthrough(texts: list[str], top_k: int | None) -> list[tuple[int, float]]:
        """保持原序的稳定占位分数: 1, 1/2, 1/3 ... 单调递减且可复现."""
        ranked = [(i, 1.0 / (i + 1)) for i in range(len(texts))]
        return ranked[:top_k] if top_k is not None else ranked


class NoopReranker(BaseReranker):
    """显式禁用(rerank_provider=none)或降级(缺模型/缺依赖)时的实现."""

    name = "noop"

    def __init__(self, reason: str = "disabled"):
        self.available = False
        self.reason = reason

    def rerank(self, query: str, texts: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
        return self._passthrough(texts or [], top_k)


class OnnxCrossEncoderReranker(BaseReranker):
    """onnxruntime + tokenizers 驱动的 cross-encoder 精排."""

    name = "onnx-cross-encoder"

    def __init__(self, model_dir: str | Path | None = None):
        from app.core.config import settings

        self.model_dir = Path(str(model_dir or settings.rerank_model_dir))
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._input_names: list[str] = []
        self._pad_id: int | None = None
        self._load_error: str | None = None

        self.model_path = self._find_onnx()
        tokenizer_path = self.model_dir / "tokenizer.json"
        self.tokenizer_path = tokenizer_path if tokenizer_path.is_file() else None

        missing = []
        if self.model_path is None:
            missing.append("model.onnx")
        if self.tokenizer_path is None:
            missing.append("tokenizer.json")
        self._runtime_ready = bool(importlib.util.find_spec("onnxruntime") and importlib.util.find_spec("tokenizers"))
        if not self._runtime_ready:
            missing.append("onnxruntime/tokenizers")
        self.available = not missing
        self.reason = "" if self.available else "missing:" + ",".join(missing)

    # ---------- 模型发现 ----------

    def _find_onnx(self) -> Path | None:
        preferred = self.model_dir / "model.onnx"
        if preferred.is_file():
            return preferred
        if not self.model_dir.is_dir():
            return None
        candidates = sorted(self.model_dir.glob("*.onnx"))
        return candidates[0] if candidates else None

    @property
    def _max_length(self) -> int:
        from app.core.config import settings

        return max(16, int(getattr(settings, "rerank_max_length", 512) or 512))

    @property
    def _batch_size(self) -> int:
        from app.core.config import settings

        return max(1, int(getattr(settings, "rerank_batch_size", 8) or 8))

    # ---------- 延迟加载 ----------

    def _ensure_loaded(self) -> bool:
        """首次推理时加载; 失败只降级不抛(问答链路不得因精排崩掉)."""
        if self._session is not None and self._tokenizer is not None:
            return True
        if not self.available:
            return False
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return True
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
                tokenizer.enable_truncation(max_length=self._max_length)
                self._tokenizer = tokenizer
                self._pad_id = self._resolve_pad_id(tokenizer)

                options = ort.SessionOptions()
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
                )
                self._input_names = [i.name for i in self._session.get_inputs()]
                logger.info("rerank 模型已加载: %s (inputs=%s, max_length=%d)",
                            self.model_path, self._input_names, self._max_length)
                return True
            except Exception as exc:  # noqa: BLE001 — 加载失败即永久降级, 不再重试刷屏
                self._load_error = str(exc)
                self.available = False
                self.reason = f"load-failed:{exc}"
                logger.warning("rerank 模型加载失败, 本进程降级为保序直通: %s", exc)
                return False

    @staticmethod
    def _resolve_pad_id(tokenizer) -> int:
        """按 tokenizer 自身配置取 pad id, 取不到再退回常见 pad 词/0."""
        try:
            padding = tokenizer.padding or {}
        except Exception:  # noqa: BLE001
            padding = {}
        if isinstance(padding, dict) and padding.get("pad_id") is not None:
            try:
                return int(padding["pad_id"])
            except (TypeError, ValueError):
                pass
        for token in ("<pad>", "[PAD]", "<s>"):
            try:
                tid = tokenizer.token_to_id(token)
            except Exception:  # noqa: BLE001
                tid = None
            if tid is not None:
                return int(tid)
        return 0

    # ---------- 推理 ----------

    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        import numpy as np

        if not self._ensure_loaded():
            return []
        assert self._session is not None and self._tokenizer is not None  # noqa: S101
        scores: list[float] = []
        batch_size = self._batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            encodings = [self._tokenizer.encode(query, text or " ") for text in batch]
            width = max(len(e.ids) for e in encodings)
            pad_id = self._pad_id if self._pad_id is not None else 0
            input_ids = np.full((len(encodings), width), pad_id, dtype=np.int64)
            attention_mask = np.zeros((len(encodings), width), dtype=np.int64)
            type_ids = np.zeros((len(encodings), width), dtype=np.int64)
            for row, enc in enumerate(encodings):
                ids = enc.ids[:width]
                input_ids[row, :len(ids)] = ids
                attention_mask[row, :len(ids)] = (enc.attention_mask or [1] * len(ids))[:len(ids)]
                type_ids[row, :len(ids)] = (enc.type_ids or [0] * len(ids))[:len(ids)]
            feed = {}
            for name in self._input_names:
                if name == "input_ids":
                    feed[name] = input_ids
                elif name == "attention_mask":
                    feed[name] = attention_mask
                elif name == "token_type_ids":
                    feed[name] = type_ids
            if "input_ids" not in feed:
                raise ValueError(f"ONNX 模型缺少 input_ids 输入: {self._input_names}")
            outputs = self._session.run(None, feed)
            logits = np.asarray(outputs[0]).reshape(len(batch), -1).astype("float64")
            if logits.shape[1] == 1:
                batch_scores = [_sigmoid(float(v)) for v in logits[:, 0]]
            else:  # 二分类头: softmax 取"相关"列
                shifted = logits - logits.max(axis=1, keepdims=True)
                exp = np.exp(shifted)
                probs = exp / exp.sum(axis=1, keepdims=True)
                batch_scores = [float(v) for v in probs[:, -1]]
            scores.extend(batch_scores)
        return scores

    def rerank(self, query: str, texts: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
        texts = list(texts or [])
        if not texts:
            return []
        if not query or not self.available:
            return self._passthrough(texts, top_k)
        try:
            scores = self._score_pairs(query, texts)
        except Exception as exc:  # noqa: BLE001 — 推理异常同样降级, 不打断问答
            self.available = False
            self.reason = f"inference-failed:{exc}"
            logger.warning("rerank 推理失败, 本进程降级为保序直通: %s", exc)
            return self._passthrough(texts, top_k)
        if len(scores) != len(texts):
            logger.warning("rerank 分数数量不匹配(%d != %d), 保序直通", len(scores), len(texts))
            return self._passthrough(texts, top_k)
        ranked = sorted(((i, float(s)) for i, s in enumerate(scores)), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k] if top_k is not None else ranked


# ---------- 工厂 ----------

_RERANKER_LOCK = threading.Lock()
_RERANKER: BaseReranker | None = None
_RERANKER_KEY: tuple | None = None


def _build_reranker() -> BaseReranker:
    from app.core.config import settings

    provider = str(getattr(settings, "rerank_provider", "auto") or "auto").strip().lower()
    model_dir = str(getattr(settings, "rerank_model_dir", "") or "")
    if provider == "none":
        return NoopReranker("disabled")
    if provider == "onnx":
        return OnnxCrossEncoderReranker(model_dir)
    candidate = OnnxCrossEncoderReranker(model_dir)  # auto: 有模型用 onnx, 否则 Noop
    if candidate.available:
        return candidate
    return NoopReranker(candidate.reason or "model-missing")


def get_reranker() -> BaseReranker:
    """按 settings.rerank_provider 取精排实现, 结果按 (provider, model_dir) 缓存."""
    global _RERANKER, _RERANKER_KEY
    from app.core.config import settings

    key = (str(getattr(settings, "rerank_provider", "auto")), str(getattr(settings, "rerank_model_dir", "")))
    with _RERANKER_LOCK:
        if _RERANKER is not None and _RERANKER_KEY == key:
            return _RERANKER
        try:
            reranker = _build_reranker()
        except Exception as exc:  # noqa: BLE001 — 初始化异常同样降级
            logger.warning("rerank 初始化异常, 降级为 Noop: %s", exc)
            reranker = NoopReranker(f"init-failed:{exc}")
        _RERANKER = reranker
        _RERANKER_KEY = key
        return reranker


def reset_reranker_cache() -> None:
    """清空工厂缓存(单测改 settings 后需要)."""
    global _RERANKER, _RERANKER_KEY
    with _RERANKER_LOCK:
        _RERANKER = None
        _RERANKER_KEY = None
