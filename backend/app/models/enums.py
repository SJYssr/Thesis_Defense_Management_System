"""业务状态机统一定义: 角色/阶段/审核状态/风险等级等全部集中于此, 严禁散落各处硬编码."""
from enum import Enum


class Role(str, Enum):
    ADMIN = "ADMIN"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"


class UserStatus(int, Enum):
    DISABLED = 0
    ENABLED = 1


class TopicStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class SelectionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class StageStatus(str, Enum):
    """各阶段材料内部状态: 开题/中期/终期/毕业材料通用."""
    NOT_SUBMITTED = "NOT_SUBMITTED"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESUBMIT_REQUIRED = "RESUBMIT_REQUIRED"


class ProcessStage(str, Enum):
    """毕业流程总阶段(粗粒度), 决定学生当前所处环节."""
    SELECTION = "SELECTION"
    OPENING = "OPENING"
    MIDTERM = "MIDTERM"
    FINAL = "FINAL"
    MATERIAL = "MATERIAL"
    COMPLETED = "COMPLETED"


# 阶段推进权重(roadmap: 选题10% 开题20% 中期25% 终期25% 材料20%)
STAGE_WEIGHTS: dict[ProcessStage, int] = {
    ProcessStage.SELECTION: 10,
    ProcessStage.OPENING: 20,
    ProcessStage.MIDTERM: 25,
    ProcessStage.FINAL: 25,
    ProcessStage.MATERIAL: 20,
}


class RiskLevel(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HIGH = "HIGH"


class ReviewAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RETURN = "RETURN"
    SUBMIT = "SUBMIT"
    RESUBMIT = "SUBMIT"


class BusinessType(str, Enum):
    """审核记录/通知关联的业务类型."""
    TOPIC_SELECTION = "TOPIC_SELECTION"
    OPENING_REPORT = "OPENING_REPORT"
    MIDTERM_REPORT = "MIDTERM_REPORT"
    FINAL_DEFENSE = "FINAL_DEFENSE"
    GRADUATION_MATERIAL = "GRADUATION_MATERIAL"
    ARCHIVE_PACKAGE = "ARCHIVE_PACKAGE"


class MaterialType(str, Enum):
    THESIS = "THESIS"
    DESIGN = "DESIGN"
    SOURCE_CODE = "SOURCE_CODE"
    MANUAL = "MANUAL"
    VIDEO = "VIDEO"
    OTHER = "OTHER"


class AnnouncementPriority(str, Enum):
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"


# ============ 正式答辩/评审/归档(docs/project-design.md 第3节) ============

class DefenseStage(str, Enum):
    """排期/评审/结论共用的阶段维度; 开题与中期如需现场答辩复用同一场次模型."""
    OPENING = "OPENING"
    MIDTERM = "MIDTERM"
    FINAL = "FINAL"


class SessionStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    IN_PROGRESS = "IN_PROGRESS"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class SeatStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ATTENDED = "ATTENDED"
    ABSENT = "ABSENT"
    POSTPONED = "POSTPONED"
    RETAKE = "RETAKE"


class ExaminerRole(str, Enum):
    CHAIR = "CHAIR"
    EXAMINER = "EXAMINER"
    REVIEWER = "REVIEWER"


class ScoreStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REOPENED = "REOPENED"
    SUPERSEDED = "SUPERSEDED"


class ResultStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    UNDER_REVIEW = "UNDER_REVIEW"
    PUBLISHED = "PUBLISHED"
    WITHDRAWN = "WITHDRAWN"


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class ArchiveStatus(str, Enum):
    PENDING = "PENDING"
    ARCHIVED = "ARCHIVED"
    REVOKED = "REVOKED"


class ConsentVisibility(str, Enum):
    METADATA = "METADATA"
    FULL_TEXT = "FULL_TEXT"


class NotificationType(str, Enum):
    ANNOUNCEMENT = "ANNOUNCEMENT"
    REVIEW_RESULT = "REVIEW_RESULT"
    REMINDER = "REMINDER"
    SYSTEM = "SYSTEM"


# ============ AI 部分 ============

class AIDocumentType(str, Enum):
    THESIS = "THESIS"
    OPENING_REPORT = "OPENING_REPORT"
    MIDTERM_REPORT = "MIDTERM_REPORT"
    FINAL_REPORT = "FINAL_REPORT"
    TOPIC = "TOPIC"
    TEACHER_COMMENT = "TEACHER_COMMENT"
    DEFENSE_RULE = "DEFENSE_RULE"
    REFERENCE = "REFERENCE"


class IndexStatus(str, Enum):
    WAITING = "WAITING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ConversationType(str, Enum):
    DEFENSE = "DEFENSE"
    QA = "QA"
    PROGRESS_ANALYSIS = "PROGRESS_ANALYSIS"


class DefenseSessionStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class QuestionType(str, Enum):
    BASIC = "BASIC"                # 基础理解
    TECHNOLOGY = "TECHNOLOGY"      # 技术选型
    DESIGN = "DESIGN"              # 系统设计
    IMPLEMENTATION = "IMPLEMENTATION"  # 实现细节
    DATABASE = "DATABASE"          # 数据库
    TEST = "TEST"                  # 测试
    INNOVATION = "INNOVATION"      # 创新
    LIMITATION = "LIMITATION"      # 不足
    EXTENSION = "EXTENSION"        # 扩展


# 问题生成比例(roadmap 第四十一章)
QUESTION_GENERATION_PLAN: dict[QuestionType, int] = {
    QuestionType.BASIC: 3,
    QuestionType.TECHNOLOGY: 5,
    QuestionType.DESIGN: 4,
    QuestionType.IMPLEMENTATION: 4,
    QuestionType.INNOVATION: 2,
    QuestionType.LIMITATION: 2,
    QuestionType.EXTENSION: 2,
}
