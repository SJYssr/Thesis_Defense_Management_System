"""中期报告模块: 由统一阶段状态机引擎驱动."""
from app.models.business import MidtermReport, MidtermReportVersion
from app.models.enums import BusinessType, ProcessStage
from app.modules.stages.engine import StageConfig
from app.modules.stages.router_factory import make_admin_router, make_student_router, make_teacher_router

MIDTERM_CONFIG = StageConfig(
    stage=ProcessStage.MIDTERM,
    business_type=BusinessType.MIDTERM_REPORT,
    model=MidtermReport,
    version_model=MidtermReportVersion,
    version_fk="midterm_report_id",
    label="中期报告",
    fields=["progress_description", "completed_content", "remaining_content", "existing_problems", "next_plan"],
    required_fields=["progress_description", "completed_content", "next_plan"],
)

student_router = make_student_router(MIDTERM_CONFIG, "midterm")
teacher_router = make_teacher_router(MIDTERM_CONFIG, "midterms")
admin_router = make_admin_router(MIDTERM_CONFIG, "midterms")
