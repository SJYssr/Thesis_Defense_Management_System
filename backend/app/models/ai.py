"""AI/RAG 模型: 知识文档/Chunk元数据/对话/答辩会话/用量."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


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
    __tablename__ = "ai_prompt_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, comment="如 DEFENSE_EVALUATE_ANSWER")
    name: Mapped[str | None] = mapped_column(String(128))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
