"""SQLAlchemy 声明基类与公共 Mixin."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


# SQLite 测试环境不支持 BIGINT 自增主键, 统一走该类型(MySQL 仍为 BIGINT)
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
