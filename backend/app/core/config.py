"""全局配置: 从 .env 读取, 所有组件统一经 Settings 获取参数."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 示例/弱密钥特征: 出现任意一个即视为不可用于签名的公开串
WEAK_SECRET_MARKERS = (
    "change-me-in-production",
    "change_me_in_production",
    "changeme",
    "secret-key-change-me",
    "your-secret-key",
)

SECRET_KEY_MIN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_env: str = "dev"
    app_name: str = "智能毕业设计管理系统"
    # D12: 无默认值, 缺失即启动失败; 长度与示例串由 validator 拦截
    secret_key: str

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "graduation_system"
    mysql_user: str = "graduation"
    mysql_password: str = "graduation123"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6380
    redis_password: str = ""
    redis_db: int = 0

    # 文件上传
    upload_dir: str = str(BASE_DIR / "uploads")
    max_upload_size_mb: int = 50

    # JWT
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # LLM (OpenAI 兼容接口: 云端智谱/DeepSeek/通义/OpenAI, 或本地 ollama 的 /v1)
    # auto = 有 API Key 走云端, 否则探测本地 ollama; 两者都不可用必须显式失败,
    # 不允许再静默回退 Mock(那会让"AI 问答"在页面上看起来正常却全是模板文本)。
    llm_provider: str = "auto"  # auto | openai | ollama | mock(仅测试)
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_timeout: float = 120.0

    # 本地模型服务(ollama): 提供 chat 与 embedding, 均为 OpenAI 兼容端点
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_llm_model: str = "qwen2.5:7b-instruct"
    ollama_embedding_model: str = "bge-m3"
    ollama_probe_timeout: float = 2.0
    ollama_probe_ttl: float = 30.0

    # Embedding
    embedding_provider: str = "auto"  # auto | openai | ollama | hash(仅测试)
    embedding_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    embedding_api_key: str = ""
    embedding_model: str = "embedding-3"
    embedding_dim: int = 1024

    # Rerank(cross-encoder 精排): 无云端依赖, 用本地 ONNX 模型
    rerank_provider: str = "auto"  # auto | onnx | none
    rerank_model_dir: str = str(BASE_DIR / "models" / "bge-reranker-v2-m3")
    rerank_recall: int = 20      # 送入精排的候选数(混合召回池)
    rerank_keep: int = 6         # 精排后最终保留条数
    rerank_max_length: int = 512
    rerank_batch_size: int = 8

    # BM25(真倒排打分, 替代原"拉 3000 行到 Python 算二元组重叠")
    bm25_enabled: bool = True
    bm25_top_k: int = 20
    bm25_cache_size: int = 8

    # Agent RAG(多步规划/自检/引用核验), 上限必须硬约束避免失控轮次
    agent_enabled: bool = True
    agent_max_steps: int = 6  # 单次检索+一次自检补检就吃掉 2 步, 4 步太紧会频繁落到 forced
    agent_max_retrieval_rounds: int = 3
    agent_timeout_seconds: float = 120.0
    agent_citation_check: bool = True
    agent_trace_keep: int = 50

    # AI 限流
    ai_rate_limit_per_minute: int = 10

    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        """D12: 拒绝公开示例串与过短密钥, 让弱配置在启动期直接失败."""
        secret = (value or "").strip()
        if len(secret) < SECRET_KEY_MIN_LENGTH:
            raise ValueError(f"SECRET_KEY 长度必须 >= {SECRET_KEY_MIN_LENGTH} 个字符")
        lowered = secret.lower()
        for marker in WEAK_SECRET_MARKERS:
            if marker in lowered:
                raise ValueError(f"SECRET_KEY 不能包含示例串 {marker!r}, 请改为随机生成的值")
        return secret

    @field_validator("upload_dir")
    @classmethod
    def _normalize_upload_dir(cls, value: str) -> str:
        """D18: 相对路径统一锚定到 BASE_DIR 并 resolve, 与进程 CWD 解耦."""
        path = Path(value or "uploads").expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path.resolve())

    @model_validator(mode="after")
    def _validate_ai_providers(self) -> "Settings":
        """测试替身(mock/hash)只允许在非生产环境显式启用.

        用户要求"真实可用的模型": 生产环境出现 mock/hash 等价于回答是模板拼接,
        宁可启动失败也不要静默降级(与 LLMFactory 的 50301 同一口径)。
        """
        env = (self.app_env or "dev").strip().lower()
        if env in ("prod", "production"):
            for field, value in (
                ("llm_provider", self.llm_provider),
                ("embedding_provider", self.embedding_provider),
            ):
                if str(value).strip().lower() in ("mock", "hash"):
                    raise ValueError(
                        f"{field}={value} 是测试替身, 生产环境(APP_ENV={env})禁用; "
                        "请在系统设置中配置真实 API Key 或保留 auto"
                    )
        return self

    @property
    def database_url(self) -> str:
        """Alembic env.py 读取的数据库连接串."""
        return self.sqlalchemy_database_uri

    @property
    def sqlalchemy_database_uri(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def upload_dir_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def llm_use_openai(self) -> bool:
        """显式 openai, 或 auto 且有 API Key —— 真实云端调用路径."""
        if self.llm_provider == "mock":
            return False
        if self.llm_provider == "openai":
            return True
        if self.llm_provider == "ollama":
            return False
        return bool(self.llm_api_key.strip())

    @property
    def embedding_use_openai(self) -> bool:
        if self.embedding_provider in ("hash", "ollama"):
            return False
        if self.embedding_provider == "openai":
            return True
        return bool(self.embedding_api_key.strip())

    @property
    def ollama_v1_url(self) -> str:
        """ollama 的 OpenAI 兼容前缀(chat/completions 与 embeddings 都在其下)."""
        return self.ollama_base_url.rstrip("/") + "/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
