"""Embedding 服务抽象: OpenAI 兼容 API + 本地哈希向量(离线降级)."""
import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

import numpy as np


class BaseEmbeddingService(ABC):
    name = "base"
    dim = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化."""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OpenAICompatibleEmbedding(BaseEmbeddingService):
    def __init__(self, base_url: str, api_key: str, model: str, dim: int):
        from openai import OpenAI

        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=60)
        self.model = model
        self.name = f"openai:{model}"
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for i in range(0, len(texts), 16):  # 分批防超限
            batch = [t[:8000] or " " for t in texts[i:i + 16]]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            result.extend([d.embedding for d in resp.data])
        return result


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

    @classmethod
    def get(cls) -> BaseEmbeddingService:
        if cls._instance is None:
            from app.core.config import settings

            if settings.embedding_use_openai:
                try:
                    cls._instance = OpenAICompatibleEmbedding(
                        settings.embedding_base_url, settings.embedding_api_key,
                        settings.embedding_model, settings.embedding_dim,
                    )
                except Exception:
                    cls._instance = LocalHashEmbedding(settings.embedding_dim)
            else:
                cls._instance = LocalHashEmbedding(settings.embedding_dim)
        return cls._instance
