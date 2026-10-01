"""终期答辩材料模块: 仅处理材料审核及旧版材料评分，非正式答辩结果。"""
from app.models.business import FinalDefense, FinalDefenseVersion
from app.models.enums import BusinessType, ProcessStage
from app.modules.stages.engine import StageConfig
from app.modules.stages.router_factory import make_admin_router, make_student_router, make_teacher_router

FINAL_CONFIG = StageConfig(
    stage=ProcessStage.FINAL,
    business_type=BusinessType.FINAL_DEFENSE,
    model=FinalDefense,
    version_model=FinalDefenseVersion,
    version_fk="final_defense_id",
    label="终期答辩材料",
    fields=["summary", "innovation", "technical_solution", "test_result"],
    required_fields=["summary", "technical_solution", "test_result"],
)

student_router = make_student_router(FINAL_CONFIG, "final-defense")
teacher_router = make_teacher_router(FINAL_CONFIG, "final-defenses")
admin_router = make_admin_router(FINAL_CONFIG, "final-defenses")
