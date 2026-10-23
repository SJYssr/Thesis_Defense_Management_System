"""统一列表查询与分页(docs/refactor-plan.md §9, 缺陷 D10/D25).

规则:

- 所有过滤(状态/关键字/归属)必须在 SQL 层完成, 先 count 后取页;
  total 是"过滤之后"的总数, 与返回的 items 口径一致;
- 禁止"先分页再在内存里过滤"(旧 router_factory.admin_list 的写法)。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def _keyword_condition(keyword: str, keyword_fields: Sequence[Any]):
    if not keyword_fields:
        raise BusinessException("keyword 过滤缺少可搜索字段定义")
    pattern = f"%{keyword}%"
    return or_(*[field.like(pattern) for field in keyword_fields])


def count_of(db: Session, stmt) -> int:
    """count(*) 走子查询, 自带 stmt 上已有的 where 条件."""
    subquery = stmt.order_by(None).subquery()
    return int(db.execute(select(func.count()).select_from(subquery)).scalar() or 0)


def paginate(db: Session, stmt, *, page: int = 1, size: int = DEFAULT_PAGE_SIZE,
             keyword: str | None = None, keyword_fields: Sequence[Any] = (),
             filters: Sequence[Any] = ()) -> tuple[list[Any], int]:
    """统一分页入口: 返回 (rows, total), rows 为 stmt 选中实体的列表.

    filters: 追加的 where 条件(来源于调用方)
    keyword / keyword_fields: 关键字 ILIKE 模糊匹配, 多字段 OR
    """
    conditions = [condition for condition in filters if condition is not None]
    text = (keyword or "").strip()
    if text:
        conditions.append(_keyword_condition(text, keyword_fields))
    if conditions:
        stmt = stmt.where(*conditions)

    total = count_of(db, stmt)
    current_page = max(1, int(page or 1))
    page_size = max(1, min(int(size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    rows = db.execute(
        stmt.offset((current_page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(rows), total


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_arg(value: str | None, name: str) -> tuple[datetime, bool] | None:
    """解析 ISO 日期/日期时间参数; 空值视为未提供(None), 非法值抛 40002.

    返回 (解析结果, 是否为纯日期)。调用方据此决定"含当日整日"还是"精确到时刻"。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise BusinessException(
            f"参数校验失败: {name} 不是合法的日期, 应为 YYYY-MM-DD 或 ISO 8601 日期时间", 40002) from None
    return parsed, bool(_DATE_ONLY_RE.match(text))


def time_range_conditions(column, date_from: str | None, date_to: str | None) -> list:
    """时间区间过滤条件。

    - 纯日期: date_from 从当日 00:00 起(>=), date_to 含当日整日(< 次日 00:00);
    - 带时刻: 分别按 >= / <= 精确比较;
    - 仅给一端即单边过滤, 都不给则不追加任何条件。
    """
    conditions = []
    low = parse_iso_arg(date_from, "date_from")
    if low is not None:
        conditions.append(column >= low[0])
    high = parse_iso_arg(date_to, "date_to")
    if high is not None:
        value, date_only = high
        conditions.append(column < value + timedelta(days=1) if date_only else column <= value)
    return conditions
