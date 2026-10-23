"""Agent 决策循环: 规划 → 只读工具 → 观察 → 充分性自检 → 生成(P10-C).

为什么不用 OpenAI tool-calling:
本项目要跨 provider(openai / deepseek / ollama / MockLLM)工作, 各家 tool-calling 的
字段与流式行为差异明显(ollama 兼容层尤其), 一旦某家不支持, 问答会整体退化为单轮。
因此这里用"JSON 决策协议"(AGENT_PLAN)驱动: 每轮让 LLM 返回一个 JSON 动作, 由本模块
解析、校验、执行。协议解析失败有明确的重试与收束路径, 行为在所有 provider 上一致。

硬约束(违反即强制收束, 不允许"再多跑一步"):
- settings.agent_max_steps          总决策步数(计数口径 = 已发出的 kind="tool" 步)
- settings.agent_max_retrieval_rounds 检索类工具(search_knowledge/search_regulations)轮次
- settings.agent_timeout_seconds    总耗时(time.monotonic 计时)
- MAX_CONTEXT_CHARS                 拼进 prompt 的上下文字符预算
"""
from __future__ import annotations

import logging
import time
from typing import Any, Generator

from app.ai.agent.tools import AgentContext, AgentToolbox, RETRIEVAL_TOOLS, tools_prompt
from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.prompts import AGENT_TOOLS_PLACEHOLDER, DOC_TYPE_CN, resolve_system_prompt, untrusted_note
from app.core.config import settings

logger = logging.getLogger(__name__)

# 拼进 prompt 的上下文预算: 超出先丢最老的非检索观察, 再压缩来源正文
MAX_CONTEXT_CHARS = 12000
SOURCE_CONTENT_KEEP = 600      # 单条来源进 prompt 的正文字符数
OBSERVATION_KEEP_CHARS = 900   # 单条工具观察进 prompt 的字符数
EVENT_OBSERVATION_CHARS = 700  # 单条观察进 SSE step 事件的字符数
DELTA_CHUNK_SIZE = 64          # 需先核验再下发时, 答案按此粒度切块
VALID_ACTIONS = ("tool", "answer", "stop")


class AgentProtocolError(RuntimeError):
    """LLM 未按 JSON 协议输出(重试一次后仍失败). 由调用方收束或降级."""


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "…(截断)"


class AgentRunner:
    """一次 agent 问答的执行器. 逐事件 yield, 事件格式与 SSE 契约同名. """

    def __init__(self, db, ctx: AgentContext, *, toolbox: AgentToolbox | None = None,
                 llm=None, stream_deltas: bool = True) -> None:
        self.db = db
        self.ctx = ctx
        self.toolbox = toolbox if toolbox is not None else AgentToolbox()
        self._llm = llm
        self.stream_deltas = stream_deltas
        # 供上层(rag_service/trace)读取的执行结果
        self.sources: list[dict] = []
        self.tool_calls: list[str] = []
        self.forced: str = ""
        self.step_count: int = 0
        self.answer_text: str = ""
        self._observations: list[dict] = []
        self._query: str = ""
        self._history: list[dict] | None = None
        self._answer_hint: str = ""
        self._retrieval_rounds: int = 0
        self._sufficiency_done: bool = False
        self._started: float = 0.0
        self._index: int = 0

    # ---------- 基础设施 ----------

    @property
    def llm(self):
        if self._llm is None:
            self._llm = LLMFactory.get()
        return self._llm

    def _elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def _step(self, kind: str, thought: str = "", *, tool: str | None = None,
              args: dict | None = None, observation: str | None = None,
              **extra: Any) -> dict:
        data = {
            "index": self._index,
            "kind": kind,
            "thought": thought,
            "tool": tool,
            "args": args if args is not None else {},
            "observation": observation,
            "elapsed_ms": self._elapsed_ms(),
        }
        data.update(extra)
        return {"event": "step", "data": data}

    # ---------- 主循环 ----------

    def run(self, query: str, history: list[dict] | None = None) -> Generator[dict, None, None]:
        self._query = str(query or "").strip()
        self._history = history
        self._started = time.monotonic()
        self.sources = []
        self.tool_calls = []
        self.forced = ""
        self.step_count = 0
        self.answer_text = ""
        self._observations = []
        self._answer_hint = ""
        self._retrieval_rounds = 0
        self._sufficiency_done = False
        self._index = 0

        max_steps = max(1, int(getattr(settings, "agent_max_steps", 4) or 1))
        timeout = float(getattr(settings, "agent_timeout_seconds", 120.0) or 0.0)

        yield self._step("plan", f"收到问题, 可用只读工具: {', '.join(self.toolbox.names())}")

        while True:
            if self._index >= max_steps:
                self.forced = f"已达到最大步数 {max_steps}, 强制收束"
                break
            # 用秒级浮点判定: 整数毫秒会把极小预算(测试/压测)<1ms 的部分抹平
            if timeout > 0 and self._elapsed_s() > timeout:
                self.forced = f"执行超过 {timeout:g}s 时间预算, 强制收束"
                break
            try:
                decision = self._decide()
            except AgentProtocolError as exc:
                self.forced = str(exc)
                break

            action = decision["action"]
            if action == "tool":
                name = decision["tool"]
                args = decision["args"]
                thought = str(decision.get("thought") or "")
                self._index += 1
                self.step_count = self._index
                self.tool_calls.append(name)
                yield self._step("tool", thought, tool=name, args=args)
                observation = self._execute_tool(name, args)
                self._observations.append({"tool": name, "args": args, "observation": observation})
                yield self._step("observe", "", tool=name,
                                 observation=_clip(_dumps(observation), EVENT_OBSERVATION_CHARS))
                continue

            if action == "answer":
                self._index += 1
                self.step_count = self._index
                self._answer_hint = str(decision.get("answer_hint") or "")
                if self._should_self_check():
                    self._sufficiency_done = True
                    verdict = self._sufficiency_check()
                    if not verdict["enough"] and verdict["next_query"]:
                        name = "search_knowledge"
                        args = {"query": verdict["next_query"]}
                        self.tool_calls.append(name)
                        yield self._step(
                            "tool",
                            f"充分性自检判定材料不足({_clip(verdict['missing'], 80)}), 追加检索",
                            tool=name, args=args, self_check=True)
                        observation = self._execute_tool(name, args)
                        self._observations.append({"tool": name, "args": args,
                                                   "observation": observation})
                        yield self._step("observe", "", tool=name,
                                         observation=_clip(_dumps(observation), EVENT_OBSERVATION_CHARS))
                        continue
                yield self._step("answer", str(decision.get("thought") or ""))
                break

            # action == "stop": 模型自己判定收束
            self._answer_hint = str(decision.get("answer_hint") or decision.get("thought") or "")
            yield self._step("answer", str(decision.get("thought") or ""), stop=True)
            break

        if self.forced:
            yield self._step("forced", self.forced, forced=self.forced)

        yield from self._answer_stream()

    # ---------- 决策协议 ----------

    def _decide(self) -> dict:
        system = self._plan_system()
        user = self._plan_user()
        last_error = ""
        for _attempt in (1, 2):
            raw = self.llm.complete(system, user, 0.0)
            try:
                return self._validate(extract_json(raw))
            except Exception as exc:  # noqa: BLE001 — 任何不合协议都按同一路径重试
                last_error = f"{type(exc).__name__}: {exc}"
                user = (
                    f"{user}\n\n【上一次输出不合协议】{_clip(last_error, 200)}\n"
                    f"请严格只输出一个 JSON 对象, 不要任何额外文字。\n"
                    f"action 只能取 tool/answer/stop; tool 只能取以下之一: "
                    f"{', '.join(self.toolbox.names())}"
                )
        raise AgentProtocolError(f"LLM 连续两次未按协议输出({_clip(last_error, 160)}), 强制收束")

    def _validate(self, data: Any) -> dict:
        if not isinstance(data, dict):
            raise ValueError(f"决策必须是 JSON 对象, 收到 {type(data).__name__}")
        action = str(data.get("action") or "").strip()
        if action not in VALID_ACTIONS:
            raise ValueError(f"action 非法: {data.get('action')!r}, 只能取 {VALID_ACTIONS}")
        if action == "tool":
            name = str(data.get("tool") or "").strip()
            if not name:
                raise ValueError("action=tool 但缺少 tool 字段")
            self.toolbox.get(name)  # 未注册的工具 → AgentToolError
            args = data.get("args")
            args = {} if args is None else args
            if not isinstance(args, dict):
                raise ValueError("args 必须是 JSON 对象")
            data["tool"] = name
            data["args"] = args
        return data

    def _plan_system(self) -> str:
        system = resolve_system_prompt(self.db, "AGENT_PLAN")
        rendered = tools_prompt(self.toolbox)
        if AGENT_TOOLS_PLACEHOLDER in system:
            return system.replace(AGENT_TOOLS_PLACEHOLDER, rendered)
        # 管理端覆盖版本可能删掉占位符 —— 补在末尾, 否则模型面前没有任何工具清单
        return f"{system}\n\n{rendered}"

    def _plan_user(self) -> str:
        max_steps = int(getattr(settings, "agent_max_steps", 4) or 1)
        max_rounds = int(getattr(settings, "agent_max_retrieval_rounds", 3) or 0)
        parts = [untrusted_note(), f"【学生问题】{self._query}"]
        parts.append(
            f"【预算】已用步数 {self._index}/{max_steps}, "
            f"已用检索轮次 {self._retrieval_rounds}/{max_rounds}, "
            f"已耗时 {self._elapsed_ms()}ms"
        )
        parts.append(f"【已收集材料与工具观察】\n{self._context_text()}")
        return "\n\n".join(parts)

    # ---------- 工具执行 ----------

    def _execute_tool(self, name: str, args: dict) -> Any:
        if name in RETRIEVAL_TOOLS:
            limit = int(getattr(settings, "agent_max_retrieval_rounds", 3) or 0)
            if self._retrieval_rounds >= limit:
                return {"error": f"检索轮次已达上限 {limit}, 请基于现有材料直接作答"}
            self._retrieval_rounds += 1
        result = self.toolbox.execute(name, self.db, self.ctx, args)
        if name in RETRIEVAL_TOOLS and isinstance(result, list):
            self._merge_sources(result)
        return result

    def _merge_sources(self, chunks: list[dict]) -> None:
        # 同一 chunk 被多轮检索命中时只登记一次: 否则 [n] 编号会指向重复内容,
        # 引用核验的 1..N 上界也会被虚高
        seen = {item.get("chunk_id") for item in self.sources}
        for chunk in chunks:
            if chunk.get("chunk_id") in seen:
                continue
            seen.add(chunk.get("chunk_id"))
            self.sources.append({
                "index": len(self.sources) + 1,
                "chunk_id": chunk.get("chunk_id"),
                "document_type": chunk.get("document_type"),
                "section": chunk.get("section"),
                "score": chunk.get("score"),
                "content": chunk.get("content"),
            })

    # ---------- 充分性自检 ----------

    def _should_self_check(self) -> bool:
        if self._sufficiency_done or not self.sources:
            return False
        limit = int(getattr(settings, "agent_max_retrieval_rounds", 3) or 0)
        return self._retrieval_rounds < limit

    def _sufficiency_check(self) -> dict:
        try:
            system = resolve_system_prompt(self.db, "AGENT_SUFFICIENCY")
            user = (f"{untrusted_note()}\n【学生问题】{self._query}\n\n"
                    f"【已收集材料】\n{self._context_text()}")
            data = extract_json(self.llm.complete(system, user, 0.0))
        except Exception as exc:  # noqa: BLE001 — 自检失败按"充分"处理, 不阻塞作答
            logger.warning("agent 充分性自检失败, 视为材料充分: %s", exc)
            return {"enough": True, "missing": "", "next_query": ""}
        if not isinstance(data, dict):
            return {"enough": True, "missing": "", "next_query": ""}
        return {
            "enough": bool(data.get("enough", True)),
            "missing": str(data.get("missing") or ""),
            "next_query": str(data.get("next_query") or "").strip(),
        }

    # ---------- 上下文 ----------

    def _render_source(self, source: dict) -> str:
        label = DOC_TYPE_CN.get(source.get("document_type"), source.get("document_type"))
        section = source.get("section") or "正文"
        content = (source.get("content") or "")[:SOURCE_CONTENT_KEEP]
        return f"[{source['index']}] {label}·{section}\n{content}"

    def _context_text(self) -> str:
        source_blocks = [self._render_source(s) for s in self.sources]
        obs_blocks = [
            f"[工具 {item['tool']} 的返回]\n{_clip(_dumps(item['observation']), OBSERVATION_KEEP_CHARS)}"
            for item in self._observations
            if item["tool"] not in RETRIEVAL_TOOLS
        ]
        total = sum(len(b) for b in source_blocks) + sum(len(b) for b in obs_blocks)
        dropped = 0
        # 预算: 来源是事实依据必须保留; 非检索观察按"最新优先", 最老的先丢
        while obs_blocks and total > MAX_CONTEXT_CHARS:
            total -= len(obs_blocks.pop(0))
            dropped += 1
        if total > MAX_CONTEXT_CHARS and source_blocks:
            per = max(200, MAX_CONTEXT_CHARS // len(source_blocks))
            source_blocks = [b[:per] for b in source_blocks]
        blocks = source_blocks + obs_blocks
        if not blocks:
            return "(尚未收集到材料)"
        text = "\n\n".join(blocks)
        if dropped:
            text += f"\n\n(较早的 {dropped} 条工具观察因上下文预算被丢弃)"
        return text

    # ---------- 最终生成 ----------

    def _answer_user(self) -> str:
        hint = self._answer_hint.strip()
        parts = [
            f"{untrusted_note()}\n【检索上下文与工具结果】\n{self._context_text()}",
            f"【学生问题】{self._query}",
        ]
        if hint:
            parts.append(f"【规划器给的作答要点(仅作提示, 不是事实依据)】{_clip(hint, 400)}")
        parts.append(
            "请依据上面的材料与工具结果回答; 材料无法支持时明确说明无法确认。\n"
            f"引用材料时使用方括号编号, 编号必须落在 1..{max(1, len(self.sources))} 之间。"
        )
        return "\n\n".join(parts)

    def answer_user_prompt(self) -> str:
        """最终生成的 user prompt(引用核验重生成时复用同一份材料上下文)."""
        return self._answer_user()

    def _answer_stream(self) -> Generator[dict, None, None]:
        system = resolve_system_prompt(self.db, "RAG_ANSWER")
        user = self._answer_user()
        pieces: list[str] = []
        for piece in self.llm.stream(system, user, self._history):
            if not piece:
                continue
            pieces.append(piece)
            if self.stream_deltas:
                yield {"event": "delta", "data": piece}
        self.answer_text = "".join(pieces)
        # 内部事件(不直接下发): 上层据此做引用核验与 trace
        yield {"event": "answer_text", "data": {"text": self.answer_text, "hint": self._answer_hint}}


def _dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)
