"""全局配置: 从 .env 读取, 所有组件统一经 Settings 获取参数."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
