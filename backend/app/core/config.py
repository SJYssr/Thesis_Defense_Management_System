"""全局配置: 从 .env 读取, 所有组件统一经 Settings 获取参数."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_env: str = "dev"
    app_name: str = "智能毕业设计管理系统"
    secret_key: str = "graduation-system-secret-key-change-me-in-production"

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

    # LLM (OpenAI 兼容接口: 智谱/DeepSeek/通义/OpenAI 均可)
    llm_provider: str = "auto"  # auto | openai | mock
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"

    # Embedding
    embedding_provider: str = "auto"  # auto | openai | local
    embedding_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    embedding_api_key: str = ""
    embedding_model: str = "embedding-3"
    embedding_dim: int = 1024

    # AI 限流
    ai_rate_limit_per_minute: int = 10

    @property
    def sqlalchemy_database_uri(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def llm_use_openai(self) -> bool:
        """auto 模式下有 key 才走真实 API, 否则降级 Mock, 保证离线可完整演示."""
        if self.llm_provider == "mock":
            return False
        if self.llm_provider == "openai":
            return True
        return bool(self.llm_api_key.strip())

    @property
    def embedding_use_openai(self) -> bool:
        if self.embedding_provider == "local":
            return False
        if self.embedding_provider == "openai":
            return True
        return bool(self.embedding_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
