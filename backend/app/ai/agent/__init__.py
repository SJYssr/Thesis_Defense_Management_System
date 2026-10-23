"""AI Agent 问答子系统(P10-C): 只读工具 + 多步决策循环 + 轨迹.

模块边界:
- tools.py  : 只读工具注册表(归属过滤、异常归一)
- loop.py   : 决策循环(JSON 协议, 硬约束: 步数/检索轮次/超时/上下文预算)
- trace.py  : 内存有界轨迹

对外只暴露 AgentRunner / AgentContext / AgentToolbox / agent_trace;
rag_service.answer_agent_stream 负责把事件流包装成 SSE 契约。
"""
from app.ai.agent.tools import (
    RETRIEVAL_TOOLS,
    AgentContext,
    AgentTool,
    AgentToolbox,
    AgentToolError,
    TOOL_REGISTRY,
    tools_prompt,
)
from app.ai.agent.trace import AgentTrace, agent_trace
from app.ai.agent.loop import AgentRunner

__all__ = [
    "RETRIEVAL_TOOLS",
    "AgentContext",
    "AgentRunner",
    "AgentTool",
    "AgentToolbox",
    "AgentToolError",
    "AgentTrace",
    "TOOL_REGISTRY",
    "agent_trace",
    "tools_prompt",
]
