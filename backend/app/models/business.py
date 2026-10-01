"""毕业设计核心业务模型: 题目/选题/四阶段材料/版本/流程/时间配置."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class GraduationTopic(Base, TimestampMixin):
    __tablename__ = "graduation_topic"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teacher_profile.id"), index=True, comment="出题教师")
    title: Mapped[str] = mapped_column(String(255), index=True, comment="题目名称")
    description: Mapped[str | None] = mapped_column(Text, comment="题目描述")
    requirements: Mapped[str | None] = mapped_column(Text, comment="要求")
    topic_type: Mapped[str | None] = mapped_column(String(32), comment="题目类型")
    difficulty: Mapped[str | None] = mapped_column(String(16), comment="难度")
    major_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("major.id"), comment="面向专业")
    max_students: Mapped[int] = mapped_column(Integer, default=1, comment="最大选题人数")
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True, comment="DRAFT/PUBLISHED/CLOSED")
    publish_time: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_topic_teacher_status", "teacher_id", "status"),)


class TopicSelection(Base, TimestampMixin):
    __tablename__ = "topic_selection"
    # active_flag=1 表示当前有效选题; 历史记录为 NULL.
    # MySQL UNIQUE 允许多个 NULL → 每个流程实例(学年)最多一条 active 选题, 数据库层防并发重复选题.
    # 唯一性按 cycle_id 而非 student_id: 学生跨学年重做时, 新学年拥有独立流程与选题.

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_topic.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teacher_profile.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True, comment="PENDING/APPROVED/REJECTED/CANCELLED")
    student_reason: Mapped[str | None] = mapped_column(Text, comment="选题理由")
    review_comment: Mapped[str | None] = mapped_column(String(500), comment="审核意见")
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, comment="审核人 sys_user.id")
    review_time: Mapped[datetime | None] = mapped_column(DateTime)
    active_flag: Mapped[int | None] = mapped_column(SmallInteger, default=1, index=True)

    __table_args__ = (
        UniqueConstraint("cycle_id", "active_flag", name="uq_cycle_active_selection"),
        Index("ix_selection_topic_status", "topic_id", "status"),
    )


class OpeningReport(Base, TimestampMixin):
    __tablename__ = "opening_report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(255))
    research_background: Mapped[str | None] = mapped_column(Text)
    research_significance: Mapped[str | None] = mapped_column(Text)
    research_content: Mapped[str | None] = mapped_column(Text)
    research_method: Mapped[str | None] = mapped_column(Text)
    technical_route: Mapped[str | None] = mapped_column(Text)
    schedule_plan: Mapped[str | None] = mapped_column(Text)
    references: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="NOT_SUBMITTED", index=True)
    review_comment: Mapped[str | None] = mapped_column(String(500))
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class OpeningReportVersion(Base):
    __tablename__ = "opening_report_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    opening_report_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opening_report.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[str] = mapped_column(Text, comment="提交时内容快照")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class MidtermReport(Base, TimestampMixin):
    __tablename__ = "midterm_report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, index=True)
    progress_description: Mapped[str | None] = mapped_column(Text)
    completed_content: Mapped[str | None] = mapped_column(Text)
    remaining_content: Mapped[str | None] = mapped_column(Text)
    existing_problems: Mapped[str | None] = mapped_column(Text)
    next_plan: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="NOT_SUBMITTED", index=True)
    review_comment: Mapped[str | None] = mapped_column(String(500))
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MidtermReportVersion(Base):
    __tablename__ = "midterm_report_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    midterm_report_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("midterm_report.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class FinalDefense(Base, TimestampMixin):
    """终期答辩材料审核(旧版). 现场答辩与正式结论见 defense.py, 两域严格分离."""

    __tablename__ = "final_defense"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, index=True)
    summary: Mapped[str | None] = mapped_column(Text, comment="工作总结")
    innovation: Mapped[str | None] = mapped_column(Text, comment="创新点")
    technical_solution: Mapped[str | None] = mapped_column(Text, comment="技术方案")
    test_result: Mapped[str | None] = mapped_column(Text, comment="测试结果")
    status: Mapped[str] = mapped_column(String(24), default="NOT_SUBMITTED", index=True)
    review_comment: Mapped[str | None] = mapped_column(String(500))
    score: Mapped[int | None] = mapped_column(Integer, comment="旧版材料评分, 不代表正式答辩成绩")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class FinalDefenseVersion(Base):
    __tablename__ = "final_defense_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    final_defense_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("final_defense.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class GraduationMaterial(Base, TimestampMixin):
    __tablename__ = "graduation_material"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), default="NOT_SUBMITTED", index=True)
    review_comment: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class GraduationMaterialFile(Base, TimestampMixin):
    __tablename__ = "graduation_material_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    material_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_material.id"), index=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_file.id"))
    material_type: Mapped[str] = mapped_column(String(32), comment="THESIS/DESIGN/SOURCE_CODE/MANUAL/VIDEO/OTHER")


class GraduationProcess(Base, TimestampMixin):
    """流程进度: 每个学年流程实例(cycle)一条, 记录阶段/进度/风险."""

    __tablename__ = "graduation_process"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    teacher_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    current_stage: Mapped[str] = mapped_column(String(24), default="SELECTION", index=True, comment="SELECTION/OPENING/MIDTERM/FINAL/MATERIAL/COMPLETED")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(16), default="NORMAL", index=True)
    risk_reason: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class GraduationSchedule(Base, TimestampMixin):
    """学年各阶段时间配置, 用于逾期判断与 AI 分析."""

    __tablename__ = "graduation_schedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(32), unique=True, comment="如 2025-2026")
    selection_start: Mapped[datetime | None] = mapped_column(DateTime)
    selection_end: Mapped[datetime | None] = mapped_column(DateTime)
    opening_start: Mapped[datetime | None] = mapped_column(DateTime)
    opening_end: Mapped[datetime | None] = mapped_column(DateTime)
    midterm_start: Mapped[datetime | None] = mapped_column(DateTime)
    midterm_end: Mapped[datetime | None] = mapped_column(DateTime)
    final_start: Mapped[datetime | None] = mapped_column(DateTime)
    final_end: Mapped[datetime | None] = mapped_column(DateTime)
    material_start: Mapped[datetime | None] = mapped_column(DateTime)
    material_end: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1, comment="当前生效的学年配置")


class GraduationCycle(Base, TimestampMixin):
    """学年流程实例: 一名学生一个学年一条(UNIQUE), 支撑跨学年重做与届别管理."""

    __tablename__ = "graduation_cycle"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student_profile.id"), index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("graduation_topic.id"))
    teacher_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("teacher_profile.id"))
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True, comment="ACTIVE/COMPLETED/WITHDRAWN")

    __table_args__ = (UniqueConstraint("academic_year", "student_id", name="uq_cycle_year_student"),)
