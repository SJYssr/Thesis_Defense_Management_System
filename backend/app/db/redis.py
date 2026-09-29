"""Redis 客户端与统一 Key 规划."""
import redis

from app.core.config import settings

redis_client: redis.Redis = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password or None,
    db=settings.redis_db,
    decode_responses=False,
)


class RedisKeys:
    # 认证
    REFRESH = "auth:refresh:{user_id}"  # value=refresh token jti 版本号, 改密后自增使旧 token 失效
    BLACKLIST = "auth:blacklist:{jti}"
    # 限流
    RATE_LOGIN = "rate:login:{ip}"
    RATE_AI = "rate:ai:{user_id}"
    # 缓存
    CACHE = "cache:{scope}:{id}"
    # RAG
    RAG_CHUNK = "rag:chunk:{chunk_id}"
    RAG_INDEX = "idx:graduation_chunks"


def redis_available() -> bool:
    try:
        return bool(redis_client.ping())
    except Exception:
        return False
