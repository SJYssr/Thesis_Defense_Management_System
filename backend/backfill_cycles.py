"""P0 数据迁移: 学年流程实例落地 + 业务表挂 cycle_id + 唯一约束切换(docs §4 迁移顺序).

对既有 MySQL 库执行:
1. 建新表(答辩排期/评审/成绩/归档/任务)
2. 为业务表补 cycle_id 列并回填(按学生 + 当前学年创建流程实例)
3. 移除"单学生唯一"旧约束, 切换为"单流程实例唯一"

脚本可重复执行(幂等)。执行前请备份数据库。
"""
import sys

from sqlalchemy import inspect, select, text

from app.db.base import Base
from app.db.mysql import SessionLocal, engine
from app.models import all as _all_models  # noqa: F401 注册全部模型
from app.models.business import (
    FinalDefense,
    GraduationMaterial,
    GraduationProcess,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.shared.cycle import academic_year_for, ensure_cycle

# (表名, 旧唯一索引, 新唯一索引[(列...)], 是否已有 cycle_id 列由 inspector 判断)
PLAN = [
    ("topic_selection", "uq_student_active_selection", "uq_cycle_active_selection", ("cycle_id", "active_flag")),
    ("opening_report", "ix_opening_report_student_id", "uq_opening_report_cycle", ("cycle_id",)),
    ("midterm_report", "ix_midterm_report_student_id", "uq_midterm_report_cycle", ("cycle_id",)),
    ("final_defense", "ix_final_defense_student_id", "uq_final_defense_cycle", ("cycle_id",)),
    ("graduation_material", "ix_graduation_material_student_id", "uq_material_cycle", ("cycle_id",)),
    ("graduation_process", "ix_graduation_process_student_id", "uq_process_cycle", ("cycle_id",)),
]

CYCLE_TABLES = {
    "topic_selection": TopicSelection,
    "opening_report": OpeningReport,
    "midterm_report": MidtermReport,
    "final_defense": FinalDefense,
    "graduation_material": GraduationMaterial,
    "graduation_process": GraduationProcess,
}


def _index_exists(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def _constraint_exists(insp, table: str, name: str) -> bool:
    try:
        return any(u.get("name") == name for u in insp.get_unique_constraints(table))
    except NotImplementedError:
        return False


def migrate() -> dict:
    report = {"tables_created": True, "columns_added": [], "backfilled": {}, "constraints_switched": []}
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)

    with engine.connect() as conn:
        # 1. 补 cycle_id 列
        for table in CYCLE_TABLES:
            columns = [c["name"] for c in insp.get_columns(table)]
            if "cycle_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN cycle_id BIGINT NULL"))
                conn.execute(text(f"ALTER TABLE {table} ADD INDEX ix_{table}_cycle (cycle_id)"))
                conn.commit()
                report["columns_added"].append(table)
        insp = inspect(engine)

        # 2. 唯一约束切换: 删旧(单学生), 建新(单流程实例)
        for table, old_name, new_name, new_cols in PLAN:
            has_old = _index_exists(insp, table, old_name) or _constraint_exists(insp, table, old_name)
            if has_old:
                conn.execute(text(f"ALTER TABLE {table} DROP INDEX {old_name}"))
                conn.commit()
                report["constraints_switched"].append(f"{table}: -{old_name}")
            if not _index_exists(insp, table, new_name):
                cols = ", ".join(new_cols)
                conn.execute(text(f"ALTER TABLE {table} ADD UNIQUE INDEX {new_name} ({cols})"))
                conn.commit()
                report["constraints_switched"].append(f"{table}: +{new_name}")

    # 3. 回填: 为每个学生创建当前学年流程实例, 并把其业务记录挂到该实例
    with SessionLocal() as db:
        year = academic_year_for(db)
        student_ids = db.execute(
            select(TopicSelection.student_id).distinct()
        ).scalars().all()
        process_student_ids = db.execute(select(GraduationProcess.student_id)).scalars().all()
        for sid in set(student_ids) | set(process_student_ids):
            sel = db.execute(
                select(TopicSelection).where(TopicSelection.student_id == sid, TopicSelection.active_flag == 1)
            ).scalar_one_or_none()
            cycle = ensure_cycle(db, sid,
                                 topic_id=sel.topic_id if sel else None,
                                 teacher_id=sel.teacher_id if sel else None,
                                 academic_year=year)
            for model in CYCLE_TABLES.values():
                rows = db.execute(
                    select(model).where(model.student_id == sid, model.cycle_id.is_(None))
                ).scalars().all()
                for r in rows:
                    r.cycle_id = cycle.id
                if rows:
                    report["backfilled"][model.__tablename__] = (
                        report["backfilled"].get(model.__tablename__, 0) + len(rows))
        db.commit()

    return report


if __name__ == "__main__":
    result = migrate()
    print("迁移完成:")
    print(f"  新增 cycle_id 列: {result['columns_added'] or '无'}")
    print(f"  回填记录: {result['backfilled'] or '无'}")
    print(f"  约束切换: {result['constraints_switched'] or '无'}")
    print("说明: 旧 final_defense.score 仅代表旧版材料评分, 未生成任何正式答辩结论。")
    sys.exit(0)
