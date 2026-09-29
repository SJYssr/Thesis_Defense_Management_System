"""开题报告模块: 由统一阶段状态机引擎驱动."""
from app.models.business import OpeningReport, OpeningReportVersion
from app.models.enums import BusinessType, ProcessStage
from app.modules.stages.engine import StageConfig
from app.modules.stages.router_factory import make_admin_router, make_student_router, make_teacher_router

OPENING_CONFIG = StageConfig(
    stage=ProcessStage.OPENING,
    business_type=BusinessType.OPENING_REPORT,
    model=OpeningReport,
    version_model=OpeningReportVersion,
    version_fk="opening_report_id",
    label="开题报告",
    fields=["title", "research_background", "research_significance", "research_content",
            "research_method", "technical_route", "schedule_plan", "references"],
    required_fields=["title", "research_background", "research_content", "research_method", "schedule_plan"],
)

student_router = make_student_router(OPENING_CONFIG, "opening-report")
teacher_router = make_teacher_router(OPENING_CONFIG, "opening-reports")
admin_router = make_admin_router(OPENING_CONFIG, "opening-reports")
