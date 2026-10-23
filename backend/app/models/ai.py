"""AI/RAG 模型: 知识文档/Chunk元数据/对话/答辩会话/用量."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class AIDocument(Base, TimestampMixin):
    __tablename__ = "ai_document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="学生私有库归属, 全局知识库为 NULL")
    topic_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    document_type: Mapped[str] = mapped_column(String(32), index=True, comment="THESIS/OPENING_REPORT/.../DEFENSE_RULE")
    document_name: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_file.id"))
    content_text: Mapped[str | None] = mapped_column(Text, comment="解析后的纯文本(用于关键词检索兜底)")
    scope: Mapped[str] = mapped_column(String(16), default="STUDENT", index=True, comment="GLOBAL/STUDENT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    index_status: Mapped[str] = mapped_column(String(16), default="WAITING", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AIDocumentChunk(Base):
    """Chunk 元数据存 MySQL; 向量优先存 Redis(RediSearch), 无 Search 模块时降级存本表 embedding 列."""

    __tablename__ = "ai_document_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_document.id"), index=True)
    student_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="STUDENT", index=True)
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(255), comment="章节标题")
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_key: Mapped[str | None] = mapped_column(String(128), comment="Redis 中的向量 key")
    embedding: Mapped[bytes | None] = mapped_column(Text, comment="降级模式: float32 bytes base64")
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class AIConversation(Base, TimestampMixin):
    __tablename__ = "ai_conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    student_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(32), default="QA")
    title: Mapped[str | None] = mapped_column(String(255))


class AIMessage(Base):
    __tablename__ = "ai_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_conversation.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), comment="SYSTEM/USER/ASSISTANT")
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    sources_json: Mapped[str | None] = mapped_column(Text, comment="引用的 chunk 信息")
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class AIDefenseQuestion(Base, TimestampMixin):
    __tablename__ = "ai_defense_question"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, index=True)
    session_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="预生成时为空")
    question: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32), comment="BASIC/TECHNOLOGY/DESIGN/...")
    difficulty: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    reference_chunks: Mapped[str | None] = mapped_column(Text, comment="生成依据 chunk id JSON")
    reference_answer: Mapped[str | None] = mapped_column(Text)
    used: Mapped[int] = mapped_column(SmallInteger, default=0, comment="是否已在答辩中使用")


class AIDefenseSession(Base, TimestampMixin):
    __tablename__ = "ai_defense_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="CREATED", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text, comment="答辩报告 JSON")


class AIDefenseTurn(Base, TimestampMixin):
    __tablename__ = "ai_defense_turn"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_defense_session.id"), index=True)
    question_id: Mapped[int | None] = mapped_column(BigInteger)
    turn_no: Mapped[int] = mapped_column(Integer, comment="第几问")
    is_follow_up: Mapped[int] = mapped_column(SmallInteger, default=0)
    question: Mapped[str] = mapped_column(Text)
    student_answer: Mapped[str | None] = mapped_column(Text)
    ai_feedback: Mapped[str | None] = mapped_column(Text)
    accuracy_score: Mapped[int | None] = mapped_column(Integer)
    logic_score: Mapped[int | None] = mapped_column(Integer)
    clarity_score: Mapped[int | None] = mapped_column(Integer)
    depth_score: Mapped[int | None] = mapped_column(Integer)
    total_score: Mapped[int | None] = mapped_column(Integer)
    follow_up_question: Mapped[str | None] = mapped_column(Text, comment="追问问题")
    reference_chunks: Mapped[str | None] = mapped_column(Text)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    scene: Mapped[str | None] = mapped_column(String(64), comment="DEFENSE_QA/GENERATE_QUESTIONS/...")
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # 双默认值兜底: 各 AI 服务插入时未传 created_at, 旧行为会产生 NULL 时间记录
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, server_default=func.now(), index=True)


class AIPromptTemplate(Base, TimestampMixin):
    """Prompt 模板当前态(roadmap 七十三章): 每场景一行, 内容修改产生新版本并写入历史表."""

    __tablename__ = "ai_prompt_template"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, comment="如 DEFENSE_EVALUATE_ANSWER")
    name: Mapped[str | None] = mapped_column(String(128))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, comment="1=启用自定义内容, 0=停用(回退内置默认)")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, comment="最近修改人 sys_user.id")


class AIPromptTemplateVersion(Base):
    """Prompt 版本历史: 每次 UPDATE/RESET 留存内容快照, 支持追溯."""

    __tablename__ = "ai_prompt_template_version"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16), comment="UPDATE/RESET/STATUS")
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now, index=True)


class AIProvider(Base, TimestampMixin):
    """AI 供应商(P11).

    设计原点: 一份 Base URL + 一份 API Key 就是一个供应商, 它同时可以充当会话模型来源
    和向量模型来源 —— Dify / Open WebUI / LiteLLM 都是这个口径。
    能力选择因此退化为"供应商 + 模型名"两个字段, 不必为 chat / embedding 各填一遍
    Base URL 与 API Key(旧设计 15 个输入框的根源)。
    """

    __tablename__ = "ai_provider"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, comment="供应商显示名, 如 智谱 / 本地 ollama")
    kind: Mapped[str] = mapped_column(
        String(32), default="openai",
        comment="预设类型: openai/ollama/deepseek/zhipu/dashscope/moonshot/custom",
    )
    base_url: Mapped[str] = mapped_column(
        String(255), comment="OpenAI 兼容基址(含 /v1); ollama 填 http://host:11434"
    )
    api_key_enc: Mapped[str | None] = mapped_column(
        String(512), comment="Fernet 密文; 空表示该供应商无需 Key"
    )
    enabled: Mapped[int] = mapped_column(SmallInteger, default=1, comment="1 启用 / 0 停用")
    models_json: Mapped[str | None] = mapped_column(Text, comment="缓存的模型清单 JSON 数组")
    models_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="模型清单最近刷新时间")
    last_status: Mapped[str | None] = mapped_column(String(16), comment="最近探测: ok/degraded/down")
    last_detail: Mapped[str | None] = mapped_column(String(512), comment="最近探测说明")
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近探测时间")
