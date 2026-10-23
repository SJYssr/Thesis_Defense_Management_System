"""Embedding 实现选择: OpenAI 兼容云端 / 本地 ollama / 显式哈希替身(P11).

不静默降级: 没有绑定真实向量模型时抛 AIServiceUnavailable(code=50301), 而不是悄悄换成
LocalHashEmbedding(纯词面匹配)却对外宣称检索正常 —— 哈希向量只在 AI_TEST_MODE=hash
显式启用时使用(单测与离线演示)。

配置来源只有数据库: 供应商行(ai_provider)提供 Base URL 与 Key, 绑定键(ai_embedding_*)
提供模型名与维度, 环境变量不参与(见 app/ai/runtime_config.py)。

fingerprint(name:dim) 是索引一致性口径: 换模型或换维度后旧向量不可复用, 必须重建索引。
"""
import hashlib
import logging
import math
import re
import threading
from abc import ABC, abstractmethod
from collections import Counter

import numpy as np

from app.ai.llm.factory import AIServiceUnavailable
from app.ai.net import openai_http_client
from app.ai.providers import openai_base_url
from app.ai.runtime_config import resolve_ai_config

logger = logging.getLogger("app.ai.embedding")

# 未指定维度时哈希替身使用的桶数(与历史索引保持一致, 避免测试指纹漂移)
HASH_DIM = 1024


class BaseEmbeddingService(ABC):
    name = "base"
    dim = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化."""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def fingerprint(self) -> str:
        """向量空间标识(name:dim): 换模型/换维度后必须重建索引."""
        return f"{self.name}:{self.dim}"


class OpenAICompatibleEmbedding(BaseEmbeddingService):
    def __init__(self, base_url: str, api_key: str, model: str, dim: int,
                 timeout: float = 60.0, strict_dim: bool = False):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout,
                             http_client=openai_http_client(base_url))
        self.model = model
        self.name = f"openai:{model}"
        self.dim = dim
        # strict_dim: 维度由系统设置显式指定 -> 模型输出必须一致, 否则混维度会静默写坏向量库
        self.strict_dim = strict_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for i in range(0, len(texts), 16):  # 分批防超限
            batch = [t[:8000] or " " for t in texts[i:i + 16]]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            result.extend([d.embedding for d in resp.data])
        self._sync_dim(result)
        return result

    def _sync_dim(self, vectors: list[list[float]]) -> None:
        """未指定维度时以模型实际输出为准; 系统设置显式指定维度时不一致必须显式失败."""
        if not vectors:
            return
        actual = len(vectors[0])
        if not actual or actual == self.dim:
            return
        if self.strict_dim:
            raise AIServiceUnavailable(
                f"向量维度与系统设置不一致: ai_embedding_dim={self.dim}, 模型 {self.model} "
                f"实际输出 {actual} 维; 请把 系统设置 → AI 服务 的维度改成 {actual}"
                "(或留空由模型决定), 然后重建知识库索引"
            )
        logger.warning(
            "embedding 维度与配置不一致: EMBEDDING_DIM=%s, 模型 %s 实际输出 %s 维; 以模型为准"
            "(指纹随之变化, 旧索引需重建)", self.dim, self.model, actual,
        )
        self.dim = actual


class OllamaEmbedding(OpenAICompatibleEmbedding):
    """本地 ollama 的 OpenAI 兼容 embeddings 端点({base}/v1).

    base_url / 模型名来自绑定的供应商(数据库), 维度来自 ai_embedding_dim 或
    首次真实调用后由模型输出校正(bge-m3 = 1024)。
    """

    def __init__(self, model: str, base_url: str, dim: int = 0,
                 timeout: float = 60.0, strict_dim: bool = False):
        resolved = (model or "").strip()
        base = (base_url or "").strip()
        if not base:
            raise AIServiceUnavailable(
                "ollama 供应商缺少 Base URL: 请在 系统设置 → AI 服务 里补全后重试"
            )
        super().__init__(
            openai_base_url(base),
            "ollama",  # ollama 不校验 key, 但 OpenAI SDK 要求非空
            resolved,
            int(dim or 0),
            timeout=timeout,
            strict_dim=strict_dim,
        )
        self.name = f"ollama:{resolved}"


class LocalHashEmbedding(BaseEmbeddingService):
    """本地确定性哈希向量: 中文二元字符组 TF → 哈希桶 → L2 归一化.

    无外部依赖, 语义近似词面匹配, 足够支撑离线开发与演示.
    """

    def __init__(self, dim: int = 1024):
        self.name = "local-hash"
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        text = re.sub(r"\s+", "", text)
        if len(text) < 2:
            return [text] if text else []
        return [text[i:i + 2] for i in range(len(text) - 1)]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            counts = Counter(self._tokens(text))
            for token, count in counts.items():
                bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
                sign = 1.0 if bucket % 2 == 0 else -1.0
                vec[bucket] += sign * (1.0 + math.log(count))
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            vecs.append(vec.tolist())
        return vecs


class EmbeddingFactory:
    _instance: BaseEmbeddingService | None = None
    _instance_key: tuple | None = None
    _lock = threading.Lock()

    @classmethod
    def _cached(cls, key: tuple, build) -> BaseEmbeddingService:
        with cls._lock:
            if cls._instance is None or cls._instance_key != key:
                cls._instance = build()
                cls._instance_key = key
            return cls._instance

    @classmethod
    def _build_openai(cls, cfg) -> BaseEmbeddingService:
        key = (cfg.embedding_api_key or "").strip()
        if not key:
            raise AIServiceUnavailable(
                "向量模型供应商还没有保存 API Key: 请在 系统设置 → AI 服务 里补全后点“测试连接”"
            )
        return OpenAICompatibleEmbedding(
            cfg.embedding_base_url, key, cfg.embedding_model, cfg.embedding_dim,
            strict_dim=cfg.embedding_dim_explicit,
        )

    @classmethod
    def _build_ollama(cls, cfg) -> BaseEmbeddingService:
        return OllamaEmbedding(
            cfg.embedding_model, base_url=cfg.embedding_base_url, dim=cfg.embedding_dim,
            strict_dim=cfg.embedding_dim_explicit,
        )

    @classmethod
    def _build_hash(cls, cfg) -> BaseEmbeddingService:
        logger.warning(
            "AI_TEST_MODE 测试替身: 使用本地哈希向量(纯词面匹配, 无语义), 仅供测试与离线演示"
        )
        return LocalHashEmbedding(cfg.embedding_dim or HASH_DIM)

    @classmethod
    def get(cls, db=None) -> BaseEmbeddingService:
        """取当前 embedding 实现: 只看数据库绑定的供应商; 未绑定抛 AIServiceUnavailable.

        与 LLMFactory 同一条纪律: 没有 "auto 猜一个" 的分支, 用户绑谁就用谁。
        """
        cfg = resolve_ai_config(db)
        if not cfg.embedding_enabled:
            raise AIServiceUnavailable(
                "AI 功能已在系统设置中关闭(ai_defense_enabled=false): "
                "如需启用请在 系统设置 → AI 服务 中打开"
            )

        provider = cfg.embedding_provider
        if provider == "hash":
            return cls._cached(("hash", cfg.embedding_dim), lambda: cls._build_hash(cfg))
        if not cfg.embedding_configured:
            raise AIServiceUnavailable(
                "未绑定可用的向量模型供应商: 请在 系统设置 → AI 服务 里添加供应商, 再把「向量模型」绑定到它"
            )
        if provider == "ollama":
            return cls._cached(
                ("ollama", cfg.embedding_base_url, cfg.embedding_model, cfg.embedding_dim),
                lambda: cls._build_ollama(cfg),
            )
        return cls._cached(
            ("openai", cfg.embedding_base_url, cfg.embedding_model,
             cfg.embedding_dim, cfg.embedding_api_key),
            lambda: cls._build_openai(cfg),
        )

    @classmethod
    def reset(cls) -> None:
        """清空实例缓存(配置热更新/测试用)."""
        with cls._lock:
            cls._instance = None
            cls._instance_key = None
