"""Agent 只读工具注册表(P10-C).

设计要点:
1. **身份只能由系统注入**: 每个工具从 AgentContext.student_id 取归属, 调用方(LLM)
   传 student_id/studentId/sid 一律拒绝(AgentToolError), 而不是"静默忽略后返回空" ——
   静默忽略会让越权尝试看起来成功, 排查时无从判断是被过滤还是没数据。
2. **只读**: 全部工具只做 select / 计算, 不写库、不调外部副作用接口。
3. **失败不炸循环**: 工具异常由 AgentToolbox.execute 转成 {"error": ...} 观察结果,
   交给决策循环继续推理(见 loop.py)。
4. 工具清单要能被 LLM 以 JSON 选择, 因此提供 tools_prompt() 渲染成一段紧凑文本。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.rag.retriever import HybridRetriever
from app.models.enums import AIDocumentType

# 检索类工具: 计入 agent_max_retrieval_rounds 预算
RETRIEVAL_TOOLS = frozenset({"search_knowledge", "search_regulations"})

# 全局知识库文档类型(答辩规范 / 参考资料), 与 AIDocumentType 枚举保持一致
GLOBAL_DOCUMENT_TYPES = (AIDocumentType.DEFENSE_RULE.value, AIDocumentType.REFERENCE.value)

# 归属身份参数名: 任何工具都不接受调用方指定
IDENTITY_ARG_NAMES = frozenset({
    "student_id", "studentId", "student", "sid", "owner_id", "profile_id", "user_id",
})

_MAX_TOP_K = 10
_CONTENT_LIMIT = 600


class AgentToolError(RuntimeError):
    """工具参数非法/越权/执行失败. 由注册表捕获成 observation, 不向上抛。"""


@dataclass(frozen=True)
class AgentContext:
    """一次 agent 会话的身份上下文(不可由 LLM 改写)."""

    student_id: int
    user_id: int | None = None
    scene: str = "QA"
    history: list[dict] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.student_id, int) or self.student_id <= 0:
            raise AgentToolError("AgentContext.student_id 必须是正整数(身份由系统注入)")


def _reject_identity_args(args: dict) -> None:
    for key in args:
        if str(key) in IDENTITY_ARG_NAMES:
            raise AgentToolError(
                f"参数 {key} 不允许由调用方指定: 学生身份只能由系统注入, 拒绝跨学生访问")


def _require_query(value: Any, tool: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentToolError(f"{tool} 缺少必填参数 query")
    return text[:500]


def _clean_document_types(values: Any) -> list[str] | None:
    """枚举白名单: 未知类型直接拒绝(不静默丢弃, 否则检索面被悄悄放大/缩小)."""
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise AgentToolError("document_types 必须是字符串数组")
    allowed = {t.value for t in AIDocumentType}
    out: list[str] = []
    for value in values:
        text = str(value)
        if text not in allowed:
            raise AgentToolError(f"未知文档类型 {text!r}; 只能取 {sorted(allowed)}")
        if text not in out:
            out.append(text)
    return out or None


def _clean_top_k(value: Any, default: int = 6) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AgentToolError(f"top_k 必须是整数, 收到 {value!r}") from None
    if number < 1 or number > _MAX_TOP_K:
        raise AgentToolError(f"top_k 必须在 1..{_MAX_TOP_K} 之间, 收到 {number}")
    return number


def _result_refs(chunks: list) -> list[dict]:
    items = []
    for index, chunk in enumerate(chunks, 1):
        items.append({
            "ref": index,
            "chunk_id": chunk.chunk_id,
            "document_type": chunk.document_type,
            "section": chunk.section_title,
            "score": chunk.score,
            "content": (chunk.content or "")[:_CONTENT_LIMIT],
        })
    return items


# ---------- 工具实现 ----------

def _search_knowledge(db: Session, ctx: AgentContext, query: Any = None,
                      document_types: Any = None, top_k: Any = None) -> list[dict]:
    """本人私有库 + 全局库混合检索."""
    text = _require_query(query, "search_knowledge")
    types = _clean_document_types(document_types)
    chunks = HybridRetriever(db).retrieve(text, ctx.student_id, types,
                                          top_k=_clean_top_k(top_k))
    return _result_refs(chunks)


def _search_regulations(db: Session, ctx: AgentContext, query: Any = None,
                        top_k: Any = None) -> list[dict]:
    """只检索全局知识库(答辩规范 / 参考资料)."""
    text = _require_query(query, "search_regulations")
    chunks = HybridRetriever(db).retrieve(text, ctx.student_id,
                                          list(GLOBAL_DOCUMENT_TYPES),
                                          top_k=_clean_top_k(top_k, default=5))
    return _result_refs(chunks)


def _get_my_progress(db: Session, ctx: AgentContext) -> dict:
    from app.shared.progress import STAGE_LABELS, ProgressService

    snapshot = ProgressService.compute(db, ctx.student_id)
    return {
        "percent": snapshot.percent,
        "stage": snapshot.stage,
        "stage_cn": STAGE_LABELS.get(snapshot.stage, snapshot.stage),
        "completed": snapshot.completed,
        "blockers": list(snapshot.blockers),
    }


def _get_my_topic(db: Session, ctx: AgentContext) -> dict:
    from app.models.business import GraduationTopic, TopicSelection

    selection = db.execute(
        select(TopicSelection)
        .where(TopicSelection.student_id == ctx.student_id, TopicSelection.active_flag == 1)
        .order_by(TopicSelection.id.desc())
        .limit(1)
    ).scalars().first()
    if selection is None:
        return {"topic": None, "note": "该学生当前没有有效选题记录"}
    topic = db.get(GraduationTopic, selection.topic_id)
    return {
        "title": topic.title if topic else None,
        "selection_status": selection.status,
        "cycle_id": selection.cycle_id,
        "topic_type": topic.topic_type if topic else None,
        "difficulty": topic.difficulty if topic else None,
        "source": (f"出题教师 #{topic.teacher_id}" if topic else "题目已删除"),
        "publish_time": (topic.publish_time.isoformat(timespec="minutes")
                         if topic and topic.publish_time else None),
        "reason": (selection.student_reason or "")[:200],
    }


def _get_my_stage_status(db: Session, ctx: AgentContext) -> list[dict]:
    """四个阶段材料的当前状态(按当前学年流程实例取)."""
    from app.models.business import (
        FinalDefense,
        GraduationMaterial,
        MidtermReport,
        OpeningReport,
        TopicSelection,
    )
    from app.shared.cycle import get_active_cycle

    cycle = get_active_cycle(db, ctx.student_id)
    cycle_id = cycle.id if cycle is not None else None

    def _latest(model) -> str | None:
        stmt = select(model).where(model.student_id == ctx.student_id)
        if cycle_id is not None:
            stmt = stmt.where(model.cycle_id == cycle_id)
        row = db.execute(stmt.order_by(model.id.desc()).limit(1)).scalars().first()
        return row.status if row else None

    selection = db.execute(
        select(TopicSelection)
        .where(TopicSelection.student_id == ctx.student_id, TopicSelection.active_flag == 1)
        .order_by(TopicSelection.id.desc()).limit(1)
    ).scalars().first()

    stages = [("SELECTION", selection.status if selection else None)]
    stages.append(("OPENING", _latest(OpeningReport)))
    stages.append(("MIDTERM", _latest(MidtermReport)))
    stages.append(("FINAL", _latest(FinalDefense)))
    stages.append(("MATERIAL", _latest(GraduationMaterial)))
    return [{"stage": stage, "status": status or "NOT_SUBMITTED"} for stage, status in stages]


def _get_rubric(db: Session, ctx: AgentContext) -> dict:
    """评分维度与权重快照(只读). 无启用配置时返回空数组 + 说明, 不编造维度."""
    from app.models.defense import ReviewRubric

    rubric = db.execute(
        select(ReviewRubric).where(ReviewRubric.enabled == 1).order_by(ReviewRubric.id.desc()).limit(1)
    ).scalars().first()
    if rubric is None:
        return {"items": [], "note": "系统当前没有启用中的评分细则(review_rubric), 无法给出权重"}
    try:
        items = json.loads(rubric.weights_json or "[]")
    except (TypeError, ValueError):
        items = []
    return {
        "academic_year": rubric.academic_year,
        "stage": rubric.stage,
        "version": rubric.version,
        "pass_line": rubric.pass_line,
        "min_examiners": rubric.min_examiners,
        "items": items,
    }


# ---------- 注册表 ----------

@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]

    def run(self, db: Session, ctx: AgentContext, **kwargs) -> Any:
        _reject_identity_args(kwargs)
        return self.func(db, ctx, **kwargs)

    def render(self) -> str:
        schema = json.dumps(self.parameters, ensure_ascii=False, separators=(",", ":"))
        return f"- {self.name}: {self.description}\n  parameters(JSON Schema): {schema}"


TOOL_REGISTRY: dict[str, AgentTool] = {}


def _register(tool: AgentTool) -> AgentTool:
    TOOL_REGISTRY[tool.name] = tool
    return tool


_register(AgentTool(
    name="search_knowledge",
    description="在本人私有知识库(论文/开题/中期/终期/题目)与全局知识库中混合检索(向量+关键词 RRF), 返回带编号的片段。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题, 中文, 越具体越好"},
            "document_types": {"type": "array", "items": {"type": "string",
                                                          "enum": [t.value for t in AIDocumentType]},
                               "description": "可选, 限定文档类型"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": _MAX_TOP_K, "description": "返回条数, 默认 6"},
        },
        "required": ["query"],
    },
    func=_search_knowledge,
))

_register(AgentTool(
    name="search_regulations",
    description="只检索全局知识库(答辩规范 DEFENSE_RULE / 参考资料 REFERENCE), 用于回答流程与写作规范类问题。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题, 例如 答辩迟到多久算缺席"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": _MAX_TOP_K, "description": "返回条数, 默认 5"},
        },
        "required": ["query"],
    },
    func=_search_regulations,
))

_register(AgentTool(
    name="get_my_progress",
    description="读取本人毕业设计进度快照(百分比/当前阶段/是否完成/未达成项), 数值由规则引擎计算, 不可推测。",
    parameters={"type": "object", "properties": {}, "required": []},
    func=_get_my_progress,
))

_register(AgentTool(
    name="get_my_topic",
    description="读取本人当前有效选题(题目名称/选题状态/来源/难度/选题理由)。",
    parameters={"type": "object", "properties": {}, "required": []},
    func=_get_my_topic,
))

_register(AgentTool(
    name="get_my_stage_status",
    description="读取本人四个阶段材料(选题/开题/中期/终期/毕业材料)的当前状态。",
    parameters={"type": "object", "properties": {}, "required": []},
    func=_get_my_stage_status,
))

_register(AgentTool(
    name="get_rubric",
    description="读取当前启用中的评分细则(评分维度与权重/通过线/最低评委数); 无配置时 items 为空。",
    parameters={"type": "object", "properties": {}, "required": []},
    func=_get_rubric,
))


class AgentToolbox:
    """工具集合: 白名单查询 + 异常归一 + prompt 渲染."""

    def __init__(self, tools: dict[str, AgentTool] | None = None):
        self._tools = dict(tools if tools is not None else TOOL_REGISTRY)

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> AgentTool:
        tool = self._tools.get(str(name or ""))
        if tool is None:
            raise AgentToolError(
                f"未注册的工具 {name!r}; 只能从以下工具中选择: {', '.join(self.names())}")
        return tool

    def describe(self) -> list[dict]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    def tools_prompt(self) -> str:
        return "\n".join(tool.render() for tool in self._tools.values())

    def execute(self, name: str, db: Session, ctx: AgentContext, args: dict | None = None) -> Any:
        """执行工具; 任何异常都变成 {"error": ...} 观察结果, 绝不抛给决策循环."""
        try:
            tool = self.get(name)
            payload = args if args is None else args
            if not isinstance(payload, dict):
                raise AgentToolError(f"工具 {name} 的 args 必须是 JSON 对象")
            return tool.run(db, ctx, **payload)
        except AgentToolError as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"工具 {name} 参数不匹配: {exc}"[:300]}
        except Exception as exc:  # noqa: BLE001 — 工具失败不得中断 agent 循环
            return {"error": f"工具 {name} 执行失败: {type(exc).__name__}: {exc}"[:300]}


def tools_prompt(toolbox: AgentToolbox | None = None) -> str:
    """渲染给 LLM 的工具清单(紧凑文本, 明确"只能从这些工具里选")."""
    box = toolbox or AgentToolbox()
    return (
        "可用工具清单(只能从下列工具中选择, 不得发明工具、不得调用未列出的名字):\n"
        f"{box.tools_prompt()}"
    )
