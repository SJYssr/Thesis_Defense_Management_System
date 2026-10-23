"""Agent 执行轨迹: 内存有界环形缓冲(P10-C).

不落库的原因: 轨迹是运维/排障用的可观测数据, 不是业务实体; 落库需要新表 + 迁移,
超出本批改动边界(models/** 与 alembic 均为只读)。因此用进程内环形缓冲, 长度取
settings.agent_trace_keep, 重启即清空 —— 代价是跨进程/多 worker 不可见, 已在
router 的 GET /ai/agent/trace 注释里写明。
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Iterable

from app.core.config import settings


class AgentTrace:
    """最近 N 次 agent 问答的轨迹快照(线程安全, 有界)."""

    def __init__(self, keep: int | None = None) -> None:
        self._lock = threading.Lock()
        # 显式传入的 keep 必须一直生效; 只有 None 才跟随 settings(支持配置热改/测试 patch)
        self._explicit_keep = None if keep is None else self._resolve_keep(keep)
        self._items: deque[dict] = deque(maxlen=self._resolve_keep(keep))

    @staticmethod
    def _resolve_keep(keep: int | None) -> int:
        value = keep if keep is not None else getattr(settings, "agent_trace_keep", 50)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 50
        return max(1, value)

    def _current(self) -> deque[dict]:
        keep = self._explicit_keep if self._explicit_keep is not None else self._resolve_keep(None)
        if self._items.maxlen != keep:  # 配置热改(或测试 patch)后重建, 保留最新的部分
            self._items = deque(list(self._items)[-keep:], maxlen=keep)
        return self._items

    def record(self, *, query: str, student_id: int | None, steps: int, elapsed_ms: int,
               tool_calls: Iterable[str] = (), status: str = "SUCCESS",
               degraded: bool = False, forced: str = "", sources: int = 0) -> dict:
        entry = {
            "query": (query or "")[:200],
            "student_id": student_id,
            "steps": int(steps),
            "elapsed_ms": int(elapsed_ms),
            "tool_calls": list(tool_calls),
            "status": status,
            "degraded": bool(degraded),
            "forced": forced or "",
            "sources": int(sources),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._current().append(entry)
        return entry

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._current())

    def recent(self, n: int = 10) -> list[dict]:
        try:
            limit = max(0, int(n))
        except (TypeError, ValueError):
            limit = 10
        with self._lock:
            items = list(self._current())
        return items[-limit:] if limit else []

    def clear(self) -> None:
        with self._lock:
            self._current().clear()


agent_trace = AgentTrace()
