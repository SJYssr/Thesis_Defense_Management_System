"""正式答辩排期/评审/成绩/归档模型(docs/project-design.md 第4节).

三个独立状态域, 严禁互相推断:
- 材料审核: opening/midterm/final/material 表自身 status(既有状态机)
- 场次安排: defense_session + defense_assignment
- 正式结论: defense_result(仅由评委评分汇总后人工发布产生)

旧 final_defense.score 只代表旧版材料评分, 不写入 defense_result。
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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


# ============ P1 阶段任务 ============

class StageTask(Base, TimestampMixin):
    """按学年发布的阶段任务: 名称/说明/所需材料/开放与截止/适用范围."""

    __tablename__ = "stage_task"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(16), comment="SELECTION/OPENING/MIDTERM/FINAL/MATERIAL")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, comment="任务说明")
    requirements_json: Mapped[str | None] = mapped_column(Text, comment='所需材料清单 ["开题报告","文献综述"]')
    opens_at: Mapped[datetime | None] = mapped_column(DateTime)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    late_policy: Mapped[str] = mapped_column(String(16), default="ALLOW", comment="ALLOW/DENY 补交政策")
    college_id: Mapped[int | None] = mapped_column(BigInteger, comment="适用学院, 空为全部")
    major_id: Mapped[int | None] = mapped_column(BigInteger, comment="适用专业, 空为全部")
    class_id: Mapped[int | None] = mapped_column(BigInteger, comment="适用班级, 空为全部")
    status: Mapped[str] = mapped_column(String(16), default="PUBLISHED", comment="DRAFT/PUBLISHED/CLOSED")
    created_by: Mapped[int | None] = mapped_column(BigInteger, comment="sys_user.id")
    current_version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_task_year_stage_status", "academic_year", "stage", "status"),)


class StageTaskVersion(Base):
    """任务修改版本化: 每次编辑保存修改前快照, 可追溯变更."""

    __tablename__ = "stage_task_version"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stage_task.id"), index=True)
    version_no: Mapped[int]
    snapshot_json: Mapped[str] = mapped_column(Text, comment="修改前任务内容快照")
    changed_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


# ============ P1 答辩排期 ============

class DefenseSession(Base, TimestampMixin):
    """答辩场次: 阶段/学年/时间/地点(或线上链接)/容量/状态, revision 乐观锁."""

    __tablename__ = "defense_session"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(16), comment="OPENING/MIDTERM/FINAL")
    title: Mapped[str | None] = mapped_column(String(255), comment="场次名称")
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    location: Mapped[str | None] = mapped_column(String(255), comment="线下地点; 线上场填 ONLINE")
    online_link: Mapped[str | None] = mapped_column(String(512), comment="线上会议链接")
    capacity: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True,
                                        comment="DRAFT/READY/PUBLISHED/IN_PROGRESS/FINISHED/CANCELLED")
    revision: Mapped[int] = mapped_column(Integer, default=0, comment="并发修改乐观锁")
    created_by: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="ck_session_time"),
        Index("ix_session_year_stage_status", "academic_year", "stage", "status"),
        Index("ix_session_starts", "starts_at"),
    )


class DefenseAssignment(Base, TimestampMixin):
    """学生答辩席位: 一场次一学生一个有效席位; 重答辩另建新席位."""

    __tablename__ = "defense_assignment"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("defense_session.id"), index=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), index=True)
    seat_status: Mapped[str] = mapped_column(String(16), default="SCHEDULED",
                                             comment="SCHEDULED/ATTENDED/ABSENT/POSTPONED/RETAKE")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, comment="学生确认已读时间")
    remark: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("session_id", "cycle_id", name="uq_assignment_session_cycle"),
        Index("ix_assignment_cycle", "cycle_id"),
    )


class DefenseExaminer(Base, TimestampMixin):
    """评委分配: 一场次教师不重复; 角色含 CHAIR(主席)/EXAMINER(评委)/REVIEWER(评阅人)."""

    __tablename__ = "defense_examiner"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("defense_session.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teacher_profile.id"), index=True)
    examiner_role: Mapped[str] = mapped_column(String(16), default="EXAMINER", comment="CHAIR/EXAMINER/REVIEWER")

    __table_args__ = (UniqueConstraint("session_id", "teacher_id", name="uq_examiner_session_teacher"),)


class DefenseScheduleChange(Base):
    """已发布场次改期审计: 前后时间/地点、操作者、原因, 全量留痕."""

    __tablename__ = "defense_schedule_change"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("defense_session.id"), index=True)
    revision_before: Mapped[int | None] = mapped_column(Integer)
    before_starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    before_ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    before_location: Mapped[str | None] = mapped_column(String(255))
    after_starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    after_ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    after_location: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(String(500))
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


# ============ P2 评分配置与评委评分 ============

class ReviewRubric(Base, TimestampMixin):
    """评分项与权重配置: 按学年/学院/阶段, 启用后冻结版本, 权重合计 100%."""

    __tablename__ = "review_rubric"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    college_id: Mapped[int | None] = mapped_column(BigInteger, comment="空为全校通用")
    stage: Mapped[str] = mapped_column(String(16), comment="OPENING/MIDTERM/FINAL")
    version: Mapped[int] = mapped_column(Integer, default=1)
    min_examiners: Mapped[int] = mapped_column(Integer, default=3, comment="最低有效评委人数")
    weights_json: Mapped[str] = mapped_column(Text, comment='[{"name":"报告质量","weight":40},...]')
    pass_line: Mapped[int] = mapped_column(Integer, default=60, comment="通过分数线")
    enabled: Mapped[int] = mapped_column(SmallInteger, default=0, comment="1=启用(启用后冻结)")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime)


class ExaminerScore(Base, TimestampMixin):
    """评委评分: 每评委每席位仅一个有效提交版本; 复核重开产生新版本, 旧版保留 SUPERSEDED."""

    __tablename__ = "examiner_score"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("defense_assignment.id"), index=True)
    examiner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teacher_profile.id"), index=True)
    rubric_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("review_rubric.id"))
    rubric_version: Mapped[int | None] = mapped_column(Integer)
    scores_json: Mapped[str | None] = mapped_column(Text, comment='{"报告质量":88,...} 各评分项得分')
    comment: Mapped[str | None] = mapped_column(Text, comment="结构化评语")
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True,
                                        comment="DRAFT/SUBMITTED/REOPENED/SUPERSEDED")
    version: Mapped[int] = mapped_column(Integer, default=1)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("assignment_id", "examiner_id", "version", name="uq_score_assignment_examiner_version"),
    )


class DefenseResult(Base, TimestampMixin):
    """正式答辩结论: 服务端按有效评分汇总计算, 人工复核发布; 发布/撤回全留痕."""

    __tablename__ = "defense_result"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("defense_assignment.id"), unique=True, index=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), index=True)
    stage: Mapped[str] = mapped_column(String(16))
    score: Mapped[int | None] = mapped_column(Integer, comment="加权总分(四舍五入)")
    decision: Mapped[str | None] = mapped_column(String(24), comment="PASS/FAIL/POSTPONED")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True,
                                        comment="PENDING/READY/UNDER_REVIEW/PUBLISHED/WITHDRAWN")
    rubric_snapshot: Mapped[str | None] = mapped_column(Text, comment="发布时评分项/权重快照, 保证可复算")
    score_detail_json: Mapped[str | None] = mapped_column(Text, comment="各评委各评分项原始分, 支持复算")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_by: Mapped[int | None] = mapped_column(BigInteger)
    withdraw_reason: Mapped[str | None] = mapped_column(String(500))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)
    withdrawn_by: Mapped[int | None] = mapped_column(BigInteger)


# ============ P3 毕业成果归档 ============

class ArchivePackage(Base, TimestampMixin):
    """归档包: 一流程一个有效归档; 默认私有, 公开仅经学生授权."""

    __tablename__ = "archive_package"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graduation_cycle.id"), unique=True, index=True)
    # P9-A: 归档包归属列。此前无 student_id, 状态机对 STUDENT 的归属校验拿到 None 直接放行(D 越权:
    # 学生 A 可 submit 学生 B 的 PENDING 归档包)。迁移 0004 回填后 DB 侧置 NOT NULL;
    # ORM 侧保持可空以容纳历史构造点(seed.py 与管理端一次性提交路径仍在迁移前签名上构造)。
    student_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="归属学生 student_profile.id")
    teacher_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="归属导师 teacher_profile.id")
    result_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("defense_result.id"), comment="正式结论")
    title: Mapped[str | None] = mapped_column(String(255), comment="成果题名")
    abstract: Mapped[str | None] = mapped_column(Text, comment="摘要(公开范围含 METADATA 时可见)")
    keywords: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="ARCHIVED", index=True, comment="PENDING/ARCHIVED/REVOKED")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class ArchiveFile(Base, TimestampMixin):
    """归档文件快照: 固定最终版本与哈希, 下载统一走授权接口."""

    __tablename__ = "archive_file"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("archive_package.id"), index=True)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_file.id"))
    file_role: Mapped[str] = mapped_column(String(32), comment="THESIS/DESIGN/SOURCE_CODE/MANUAL/VIDEO/OTHER")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="归档时文件版本号")
    sha256: Mapped[str | None] = mapped_column(String(64), comment="归档时校验哈希")


class ArchiveConsent(Base):
    """公开授权: 学生本人单独记录, 可撤回; 撤回后检索与下载立即失效."""

    __tablename__ = "archive_consent"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("archive_package.id"), index=True)
    student_user_id: Mapped[int] = mapped_column(BigInteger, comment="授权学生 sys_user.id")
    visibility: Mapped[str] = mapped_column(String(16), comment="METADATA(题目摘要)/FULL_TEXT(全文)")
    consented_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArchiveDownloadLog(Base):
    """下载审计: 每次归档文件下载(含被拒绝的)都记录, 供追溯."""

    __tablename__ = "archive_download_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(BigInteger, index=True)
    file_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger, comment="请求人 sys_user.id")
    allowed: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
