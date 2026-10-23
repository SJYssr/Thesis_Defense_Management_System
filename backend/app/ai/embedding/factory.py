"""Embedding 服务抽象: 云端 OpenAI 兼容 API / 本地 ollama / 显式离线哈希向量.

不静默降级: 没有真实向量模型时抛 AIServiceUnavailable(code=50301), 而不是悄悄换成
LocalHashEmbedding(纯词面匹配)却对外宣称检索正常 —— 哈希向量只在 EMBEDDING_PROVIDER=hash
显式启用时使用(测试与离线演示)。

来源(provider)与凭据来自"生效配置": 数据库系统设置(ai_embedding_*) > 环境变量(settings),
见 app/ai/runtime_config.py —— 管理员填 Base URL / API Key / 模型名 / 维度后保存即生效。

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

from app.ai.llm.factory import AIServiceUnavailable, probe_ollama
from app.ai.net import openai_http_client
from app.ai.runtime_config import resolve_ai_config

logger = logging.getLogger("app.ai.embedding")


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
    """本地 ollama 的 OpenAI 兼容 embeddings 端点({OLLAMA_BASE_URL}/v1).

    维度默认取 EMBEDDING_DIM, 首次真实调用后按模型输出校正(bge-m3 = 1024)。
    """

    def __init__(self, model: str | None = None, dim: int | None = None,
                 base_url: str | None = None, timeout: float = 60.0,
                 strict_dim: bool = False):
        from app.core.config import settings

        resolved = (model or settings.ollama_embedding_model or "").strip()
        super().__init__(
            base_url or settings.ollama_v1_url,
            "ollama",  # ollama 不校验 key, 但 OpenAI SDK 要求非空
            resolved,
            int(dim or settings.embedding_dim),
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

    @staticmethod
    def _provider() -> str:
        from app.core.config import settings

        return (settings.embedding_provider or "auto").strip().lower()

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
                "未检测到可用的向量模型服务: 请在系统设置中填写 API Key, 或设置环境变量 "
                "EMBEDDING_API_KEY(EMBEDDING_PROVIDER=openai 但未配置 API Key)"
            )
        return OpenAICompatibleEmbedding(
            cfg.embedding_base_url, key, cfg.embedding_model, cfg.embedding_dim,
            strict_dim=cfg.embedding_dim_explicit,
        )

    @classmethod
    def _build_ollama(cls, cfg) -> BaseEmbeddingService:
        return OllamaEmbedding(
            cfg.ollama_embedding_model, dim=cfg.embedding_dim,
            base_url=cfg.ollama_embedding_base_url,
            strict_dim=cfg.embedding_dim_explicit,
        )

    @classmethod
    def _build_hash(cls, cfg) -> BaseEmbeddingService:
        logger.warning(
            "EMBEDDING_PROVIDER=hash: 使用本地哈希向量(纯词面匹配, 无语义), 仅供测试与离线演示"
        )
        return LocalHashEmbedding(cfg.embedding_dim)

    @classmethod
    def get(cls, db=None) -> BaseEmbeddingService:
        """取当前 embedding 实现(配置: 系统设置 > 环境变量); 无可用来源抛 AIServiceUnavailable."""
        cfg = resolve_ai_config(db)
        if not cfg.embedding_enabled:
            raise AIServiceUnavailable(
                "AI 功能已在系统设置中关闭(ai_embedding_enabled=false): "
                "如需启用请在 系统设置 → AI 服务 中打开"
            )

        provider = cfg.embedding_provider
        if provider == "hash":
            return cls._cached(("hash", cfg.embedding_dim), lambda: cls._build_hash(cfg))
        if provider == "openai":
            return cls._cached(
                ("openai", cfg.embedding_base_url, cfg.embedding_model,
                 cfg.embedding_dim, cfg.embedding_api_key),
                lambda: cls._build_openai(cfg),
            )
        if provider == "ollama":
            return cls._cached(
                ("ollama", cfg.ollama_embedding_base_url, cfg.ollama_embedding_model,
                 cfg.embedding_dim),
                lambda: cls._build_ollama(cfg),
            )
        if provider != "auto":
            raise AIServiceUnavailable(
                f"未检测到可用的向量模型服务(未知 EMBEDDING_PROVIDER={provider}): "
                "可选 auto | ollama | openai | hash"
            )

        if (cfg.embedding_api_key or "").strip():
            return cls._cached(
                ("openai", cfg.embedding_base_url, cfg.embedding_model,
                 cfg.embedding_dim, cfg.embedding_api_key),
                lambda: cls._build_openai(cfg),
            )
        probe = probe_ollama()
        if probe.get("reachable"):
            return cls._cached(
                ("ollama", cfg.ollama_embedding_base_url, cfg.ollama_embedding_model,
                 cfg.embedding_dim),
                lambda: cls._build_ollama(cfg),
            )
        raise AIServiceUnavailable(
            "未检测到可用的向量模型服务: 请启动 ollama(ollama serve) 或配置 EMBEDDING_API_KEY"
            f"/在系统设置中填写 API Key({probe.get('detail') or 'ollama 不可达'})"
        )

    @classmethod
    def reset(cls) -> None:
        """清空实例缓存(配置热更新/测试用)."""
        with cls._lock:
            cls._instance = None
            cls._instance_key = None
