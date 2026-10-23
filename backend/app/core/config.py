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

    # ---- AI 调用行为参数 ----
    # 配置来源(地址/密钥/模型名)一律在数据库的 ai_provider 表 + 绑定键里,
    # 这里只放"与供应商无关"的调用参数, 因此不存在环境变量与后台设置打架的可能。
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_timeout: float = 120.0

    # 测试替身: 唯一允许影响 AI 行为的环境变量, 只给单测/离线演示用。
    # mock = 离线模板拼装, hash = 本地哈希向量; APP_ENV=prod 时启动即失败。
    ai_test_mode: str = ""

    # ollama 连通性探测(用于展示"本地模型是否在线", 与"用哪个供应商"无关)
    ollama_probe_timeout: float = 2.0
    ollama_probe_ttl: float = 30.0

    # 精排(cross-encoder): 召回池大小与裁剪条数属于检索策略
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
        mode = (self.ai_test_mode or "").strip().lower()
        if mode and mode not in ("mock", "hash"):
            raise ValueError(f"AI_TEST_MODE={self.ai_test_mode} 非法; 可选 mock / hash, 留空表示真实模型")
        if mode and env in ("prod", "production"):
            raise ValueError(
                f"AI_TEST_MODE={mode} 是测试替身, 生产环境(APP_ENV={env})禁用; "
                "请在 系统设置 → AI 服务 中配置真实供应商"
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

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
