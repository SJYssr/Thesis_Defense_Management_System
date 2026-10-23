"""Dashboard 模块: 角色首页概览 + 管理员统计图表数据(roadmap 五十二~五十四)."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIUsageLog
from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationProcess,
    GraduationTopic,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.enums import ProcessStage
from app.models.system import Announcement, Notification, ReviewRecord
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.models.defense import DefenseExaminer, DefenseSession
from app.shared.cycle import academic_year_for

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

STAGE_CN = {"SELECTION": "选题", "OPENING": "开题材料", "MIDTERM": "中期材料", "FINAL": "终期材料", "MATERIAL": "毕业材料", "COMPLETED": "材料审核完成"}
RISK_CN = {"NORMAL": "正常", "WARNING": "警告", "HIGH": "高风险"}


def _current_year_cycles(db: Session):
    """当前学年的 cycle id 子查询 —— 后台"完成/毕业"类口径统一以此为范围(D63/D64)."""
    return select(GraduationCycle.id).where(GraduationCycle.academic_year == academic_year_for(db))


def _completed_count(db: Session) -> int:
    """D63/D64: "已完成/已毕业"唯一口径.

    只认 GraduationProcess.completed_at(progress.compute() 的结果投影, 见 app/shared/progress.py),
    并限定当前学年 cycle; 不再用 current_stage == "COMPLETED" 这个另立的字面值口径。
    """
    return db.execute(select(func.count(GraduationProcess.id)).where(
        GraduationProcess.completed_at.is_not(None),
        GraduationProcess.cycle_id.in_(_current_year_cycles(db)),
    )).scalar_one()


@router.get("/teacher-activity")
def teacher_activity(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Teacher's own review queue, student stages and assigned defense sessions."""
    if user.role != "TEACHER":
        raise PermissionDeniedException("仅教师可查看本人工作台")

    selections = db.execute(select(TopicSelection).where(
        TopicSelection.teacher_id == user.profile_id, TopicSelection.status == "APPROVED",
        TopicSelection.active_flag == 1)).scalars().all()
    student_ids = list({s.student_id for s in selections})
    topic_ids = {s.student_id: s.topic_id for s in selections}
    stages = {stage: 0 for stage in ("SELECTION", "OPENING", "MIDTERM", "FINAL", "MATERIAL", "COMPLETED")}
    cycle_ids = [s.cycle_id for s in selections if s.cycle_id is not None]
    processes = db.execute(select(GraduationProcess).where(
        GraduationProcess.cycle_id.in_(cycle_ids))).scalars().all() if cycle_ids else []
    processes_by_cycle = {p.cycle_id: p for p in processes}
    current_processes = {s.student_id: processes_by_cycle[s.cycle_id]
                         for s in selections if s.cycle_id in processes_by_cycle}
    for student_id in student_ids:
        stage = current_processes[student_id].current_stage if student_id in current_processes else "SELECTION"
        stages[stage if stage in stages else "SELECTION"] += 1

    student_names = dict(db.execute(select(StudentProfile.id, SysUser.real_name)
                                    .join(SysUser, SysUser.id == StudentProfile.user_id)
                                    .where(StudentProfile.id.in_(student_ids))).all()) if student_ids else {}
    topic_names = dict(db.execute(select(GraduationTopic.id, GraduationTopic.title)
                                  .where(GraduationTopic.id.in_(set(topic_ids.values())))).all()) if topic_ids else {}

    task_types = [("选题审核", TopicSelection, "PENDING", "/teacher/selections"),
                  ("开题报告审核", OpeningReport, "SUBMITTED", "/teacher/opening"),
                  ("中期材料审核", MidtermReport, "SUBMITTED", "/teacher/midterm"),
                  ("终期材料审核", FinalDefense, "SUBMITTED", "/teacher/final"),
                  ("毕业材料审核", GraduationMaterial, "SUBMITTED", "/teacher/materials")]
    pending = []
    pending_count = 0
    for label, model, status, path in task_types:
        pending_count += db.execute(select(func.count(model.id)).where(
            model.teacher_id == user.profile_id, model.status == status)).scalar_one()
        rows = db.execute(select(model).where(model.teacher_id == user.profile_id, model.status == status)
                          .order_by(model.id.desc()).limit(10)).scalars().all()
        for row in rows:
            student = db.get(StudentProfile, row.student_id)
            student_user = db.get(SysUser, student.user_id) if student else None
            topic = db.get(GraduationTopic, row.topic_id) if row.topic_id else None
            when = row.created_at if model is TopicSelection else row.submitted_at
            pending.append({"type": label, "student": student_user.real_name if student_user else "学生",
                            "topic": topic.title if topic else "—", "time": when.isoformat() if when else None,
                            "status": "待审核", "path": path})
    pending.sort(key=lambda row: row["time"] or "", reverse=True)

    doing = []
    for student_id in student_ids:
        process = current_processes.get(student_id)
        if process and process.current_stage == "COMPLETED":
            continue
        topic = topic_names.get(topic_ids[student_id], "—")
        doing.append({"type": "毕业设计指导", "student": student_names.get(student_id, "学生"),
                      "topic": topic, "time": process.updated_at.isoformat() if process and process.updated_at else None,
                      "status": "进行中", "path": "/teacher/students"})
    doing.sort(key=lambda row: row["time"] or "", reverse=True)

    done_count = db.execute(select(func.count(ReviewRecord.id)).where(
        ReviewRecord.reviewer_id == user.id,
        ReviewRecord.action.in_(["APPROVE", "REJECT", "RETURN"]))).scalar_one()
    reviewed = db.execute(select(ReviewRecord).where(ReviewRecord.reviewer_id == user.id,
                         ReviewRecord.action.in_(["APPROVE", "REJECT", "RETURN"]))
                          .order_by(ReviewRecord.id.desc()).limit(10)).scalars().all()
    done = []
    review_labels = {"TOPIC_SELECTION": ("选题审核", "/teacher/selections"),
                     "OPENING_REPORT": ("开题报告审核", "/teacher/opening"),
                     "MIDTERM_REPORT": ("中期材料审核", "/teacher/midterm"),
                     "FINAL_DEFENSE": ("终期材料审核", "/teacher/final"),
                     "GRADUATION_MATERIAL": ("毕业材料审核", "/teacher/materials")}
    for row in reviewed:
        label, path = review_labels.get(row.business_type, ("材料审核", "/teacher/students"))
        student = db.get(StudentProfile, row.student_id)
        student_user = db.get(SysUser, student.user_id) if student else None
        topic_id = topic_ids.get(row.student_id)
        topic = db.get(GraduationTopic, topic_id) if topic_id else None
        done.append({"type": label, "student": student_user.real_name if student_user else "学生",
                     "topic": topic.title if topic else "—",
                     "time": row.created_at.isoformat() if row.created_at else None,
                     "status": "已完成", "path": path})

    now = datetime.now()
    sessions = db.execute(select(DefenseSession).join(DefenseExaminer, DefenseExaminer.session_id == DefenseSession.id)
                          .where(DefenseExaminer.teacher_id == user.profile_id,
                                 DefenseSession.starts_at >= now,
                                 DefenseSession.status.in_(["PUBLISHED", "IN_PROGRESS"]))
                          .order_by(DefenseSession.starts_at).limit(5)).scalars().all()
    schedules = [{"id": s.id, "title": s.title or f"{s.stage}答辩", "starts_at": s.starts_at.isoformat(),
                  "ends_at": s.ends_at.isoformat(), "location": s.location or ("线上" if s.online_link else "待定")}
                 for s in sessions]
    published_topics = db.execute(select(func.count(GraduationTopic.id)).where(
        GraduationTopic.teacher_id == user.profile_id, GraduationTopic.status == "PUBLISHED",
        GraduationTopic.deleted_at.is_(None))).scalar_one()
    return ResponseUtil.success({"published_topics": published_topics, "student_stages": stages,
                                 "tasks": {"pending": pending[:10], "doing": doing[:10], "done": done},
                                 "task_counts": {"pending": pending_count, "doing": len(doing), "done": done_count},
                                 "schedules": schedules})


@router.get("/analytics")
def admin_analytics(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """管理员统计页数据.

    口径(D63/D73): 进度类指标(通过率/月度提交/答辩结论/已完成)统一为当前学年;
    实体总量(学生/教师/题目)与待办队列保持全量, 由响应里的 scope_map 显式标注,
    不允许"同一响应内两种口径却不标注"。
    """
    from app.core.exceptions import PermissionDeniedException
    from app.models.defense import DefenseResult
    from app.models.enums import ResultStatus

    if user.role != "ADMIN":
        raise PermissionDeniedException("仅管理员可查看全局统计")

    academic_year = academic_year_for(db)  # 注意: 下方月份循环里 year 会被重绑定为公历年
    year_cycles = _current_year_cycles(db)  # 进度类统计的唯一范围
    active_students = StudentProfile.deleted_at.is_(None)
    student_total = db.execute(select(func.count(StudentProfile.id)).where(active_students)).scalar_one()
    teacher_total = db.execute(select(func.count(TeacherProfile.id))).scalar_one()
    topic_total = db.execute(select(func.count(GraduationTopic.id)).where(GraduationTopic.deleted_at.is_(None))).scalar_one()
    selected_total = db.execute(select(func.count(func.distinct(TopicSelection.student_id))).where(
        TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1,
        TopicSelection.cycle_id.in_(year_cycles))).scalar_one()
    completed_total = _completed_count(db)  # D63: 与 progress.compute() 同源 + 当前学年口径
    pending_total = sum(db.execute(select(func.count(model.id)).where(model.status == "SUBMITTED")).scalar_one()
                        for model in (OpeningReport, MidtermReport, FinalDefense, GraduationMaterial))
    pending_total += db.execute(select(func.count(TopicSelection.id)).where(
        TopicSelection.status == "PENDING", TopicSelection.active_flag == 1)).scalar_one()

    colleges = db.execute(
        select(College.name, func.count(StudentProfile.id))
        .join(StudentProfile, (StudentProfile.college_id == College.id) & active_students, isouter=True)
        .where(College.id.is_not(None))
        .group_by(College.id, College.name).order_by(College.id)
    ).all()
    majors = db.execute(
        select(Major.id, Major.name, func.count(StudentProfile.id))
        .join(StudentProfile, (StudentProfile.major_id == Major.id) & active_students, isouter=True)
        .group_by(Major.id, Major.name).order_by(Major.id)
    ).all()
    major_items = []
    for major_id, name, total in majors:
        chosen = db.execute(select(func.count(func.distinct(TopicSelection.student_id)))
                            .join(StudentProfile, StudentProfile.id == TopicSelection.student_id)
                            .where(StudentProfile.major_id == major_id, active_students,
                                   TopicSelection.status == "APPROVED",
                                   TopicSelection.active_flag == 1)).scalar_one()
        major_items.append({"name": name, "total": total, "chosen": chosen,
                            "rate": round(chosen / total * 100, 1) if total else 0,
                            "unchosen": max(total - chosen, 0)})

    stage_models = [
        ("选题", TopicSelection), ("开题", OpeningReport), ("中期", MidtermReport),
        ("终期材料", FinalDefense), ("毕业材料", GraduationMaterial),
    ]
    pass_rates = []
    for name, model in stage_models:
        base = [model.cycle_id.in_(year_cycles)]
        if model is TopicSelection:
            base.append(model.active_flag == 1)
        approved = db.execute(select(func.count(model.id)).where(*base, model.status == "APPROVED")).scalar_one()
        reviewed = db.execute(select(func.count(model.id)).where(*base, model.status.in_(
            ["APPROVED", "REJECTED", "RESUBMIT_REQUIRED", "SUBMITTED"]))).scalar_one()
        pass_rates.append({"name": name, "value": round(approved / reviewed * 100, 1) if reviewed else 0})

    topic_types = db.execute(select(GraduationTopic.topic_type, func.count(GraduationTopic.id))
                             .where(GraduationTopic.deleted_at.is_(None))
                             .group_by(GraduationTopic.topic_type)).all()

    now = datetime.now()
    months = []
    monthly = {name: [] for name, _ in stage_models[1:]}
    for offset in range(7, -1, -1):
        index = now.year * 12 + now.month - 1 - offset
        year, month_zero = divmod(index, 12)
        start = datetime(year, month_zero + 1, 1)
        end = datetime(year + (month_zero == 11), month_zero + 2 if month_zero < 11 else 1, 1)
        months.append(f"{year}-{month_zero + 1:02d}")
        for name, model in stage_models[1:]:
            monthly[name].append(db.execute(select(func.count(model.id)).where(
                model.submitted_at >= start, model.submitted_at < end,
                model.cycle_id.in_(year_cycles))).scalar_one())

    result_rows = db.execute(select(DefenseResult.score, DefenseResult.decision).where(
        DefenseResult.stage == "FINAL", DefenseResult.status == ResultStatus.PUBLISHED.value,
        DefenseResult.cycle_id.in_(year_cycles))).all()
    grade_counts = {name: 0 for name in ("优秀", "良好", "中等", "及格", "不及格")}
    for score, decision in result_rows:
        if decision != "PASS" or score is None or score < 60:
            grade_counts["不及格"] += 1
        elif score >= 90:
            grade_counts["优秀"] += 1
        elif score >= 80:
            grade_counts["良好"] += 1
        elif score >= 70:
            grade_counts["中等"] += 1
        else:
            grade_counts["及格"] += 1

    return ResponseUtil.success({
        "cards": {"student_count": student_total, "teacher_count": teacher_total,
                  "topic_count": topic_total, "selected_count": selected_total,
                  "completed_count": completed_total, "pending_count": pending_total},
        "colleges": [{"name": name, "value": count} for name, count in colleges],
        "majors": major_items,
        "pass_rates": pass_rates,
        "topic_types": [{"name": name or "未分类", "value": count} for name, count in topic_types],
        "months": months,
        "month_series": [{"name": name, "data": values} for name, values in monthly.items()],
        "defense_results": [{"name": name, "value": count} for name, count in grade_counts.items()],
        "defense_total": len(result_rows),
        "scope": "current_year",
        "academic_year": academic_year,
        "scope_map": {
            "cards.completed_count": "current_year", "cards.selected_count": "current_year",
            "cards.pending_count": "all_years", "cards.student_count": "all_years",
            "cards.teacher_count": "all_years", "cards.topic_count": "all_years",
            "pass_rates": "current_year", "month_series": "current_year",
            "defense_results": "current_year",
            "colleges": "all_years", "majors": "all_years", "topic_types": "all_years",
        },
    })


@router.get("/admin-activity")
def admin_activity(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Recent review work and student submissions for the administrator home."""
    from app.core.exceptions import PermissionDeniedException

    if user.role != "ADMIN":
        raise PermissionDeniedException("仅管理员可查看全局待办")

    stage_models = [
        ("选题审核", TopicSelection, "selection"),
        ("开题审核", OpeningReport, "opening"),
        ("中期审核", MidtermReport, "midterm"),
        ("终期审核", FinalDefense, "final"),
        ("毕业材料", GraduationMaterial, "material"),
    ]
    counts = []
    todos = []
    for label, model, kind in stage_models:
        conditions = [model.status == ("PENDING" if model is TopicSelection else "SUBMITTED")]
        if model is TopicSelection:
            conditions.append(model.active_flag == 1)
        count = db.execute(select(func.count(model.id)).where(*conditions)).scalar_one()
        counts.append({"label": label, "count": count})
        date_field = model.created_at if model is TopicSelection else model.submitted_at
        pending = db.execute(select(model.student_id, date_field).where(*conditions)
                             .order_by(date_field.desc()).limit(5)).all()
        for student_id, date in pending:
            name = db.execute(select(SysUser.real_name)
                              .join(StudentProfile, StudentProfile.user_id == SysUser.id)
                              .where(StudentProfile.id == student_id)).scalar_one_or_none()
            todos.append({"text": f"{name or '学生'}的{label}待处理", "cat": label,
                          "date": date.isoformat()[:10] if date else "", "kind": kind,
                          "sort_date": date.isoformat() if date else ""})
    todos.sort(key=lambda item: item["sort_date"], reverse=True)
    for todo in todos:
        todo.pop("sort_date")

    business_cn = {
        "TOPIC_SELECTION": "选题申请", "OPENING_REPORT": "开题报告",
        "MIDTERM_REPORT": "中期材料", "FINAL_DEFENSE": "终期材料",
        "GRADUATION_MATERIAL": "毕业材料",
    }
    recent = db.execute(select(ReviewRecord).where(ReviewRecord.action.in_(["SUBMIT", "RESUBMIT"]))
                        .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc()).limit(5)).scalars().all()
    submissions = []
    for record in recent:
        name = db.execute(select(SysUser.real_name)
                          .join(StudentProfile, StudentProfile.user_id == SysUser.id)
                          .where(StudentProfile.id == record.student_id)).scalar_one_or_none()
        submissions.append({"name": name or "学生", "content": business_cn.get(record.business_type, record.business_type),
                            "time": record.created_at.isoformat()[:16].replace("T", " ") if record.created_at else "",
                            "status": "待审核" if record.after_status in ("PENDING", "SUBMITTED") else record.after_status})
    return ResponseUtil.success({"counts": counts, "todos": todos[:8], "submissions": submissions})


@router.get("/home")
def home(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    announcements = db.execute(
        select(Announcement).where(Announcement.status == "PUBLISHED").order_by(Announcement.publish_time.desc()).limit(5)
    ).scalars().all()
    ann_items = [{"id": a.id, "title": a.title, "priority": a.priority,
                  "publish_time": a.publish_time.isoformat() if a.publish_time else None} for a in announcements]
    unread = db.execute(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.is_read == 0)).scalar()

    if user.role == "ADMIN":
        year = academic_year_for(db)
        year_cycles = _current_year_cycles(db)  # D73: 进度类卡片统一当前学年
        data = {
            "role": "ADMIN",
            "cards": {
                "student_count": db.execute(select(func.count(StudentProfile.id)).where(StudentProfile.deleted_at.is_(None))).scalar(),
                "teacher_count": db.execute(select(func.count(TeacherProfile.id))).scalar(),
                "topic_count": db.execute(select(func.count(GraduationTopic.id)).where(GraduationTopic.deleted_at.is_(None))).scalar(),
                "selected_count": db.execute(select(func.count(TopicSelection.id)).where(
                    TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1,
                    TopicSelection.cycle_id.in_(year_cycles))).scalar(),
                "completed_count": _completed_count(db),  # D63: 唯一口径, 见 _completed_count
                "risk_count": db.execute(select(func.count(GraduationProcess.id)).where(
                    GraduationProcess.risk_level.in_(["WARNING", "HIGH"]),
                    GraduationProcess.cycle_id.in_(year_cycles))).scalar(),
            },
            # 同一屏不允许两种口径混排而不标注: 进度类当前学年, 实体总量全量
            "scope": "current_year", "academic_year": year,
            "scope_map": {
                "cards.completed_count": "current_year", "cards.risk_count": "current_year",
                "cards.selected_count": "current_year",
                "cards.student_count": "all_years", "cards.teacher_count": "all_years",
                "cards.topic_count": "all_years",
            },
            "announcements": ann_items, "unread": unread,
        }
        return ResponseUtil.success(data)

    if user.role == "TEACHER":
        year = academic_year_for(db)
        year_cycles = _current_year_cycles(db)
        pending_selections = db.execute(select(func.count(TopicSelection.id)).where(TopicSelection.teacher_id == user.profile_id, TopicSelection.status == "PENDING")).scalar()
        pending_opening = db.execute(select(func.count(OpeningReport.id)).where(OpeningReport.teacher_id == user.profile_id, OpeningReport.status == "SUBMITTED")).scalar()
        pending_midterm = db.execute(select(func.count(MidtermReport.id)).where(MidtermReport.teacher_id == user.profile_id, MidtermReport.status == "SUBMITTED")).scalar()
        pending_final = db.execute(select(func.count(FinalDefense.id)).where(FinalDefense.teacher_id == user.profile_id, FinalDefense.status == "SUBMITTED")).scalar()
        pending_material = db.execute(select(func.count(GraduationMaterial.id)).where(GraduationMaterial.teacher_id == user.profile_id, GraduationMaterial.status == "SUBMITTED")).scalar()
        student_ids = [r for r in db.execute(select(TopicSelection.student_id).where(TopicSelection.teacher_id == user.profile_id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1, TopicSelection.cycle_id.in_(year_cycles))).scalars().all()]
        risk = db.execute(select(func.count(GraduationProcess.id)).where(
            GraduationProcess.student_id.in_(student_ids),
            GraduationProcess.risk_level.in_(["WARNING", "HIGH"]),
            GraduationProcess.cycle_id.in_(year_cycles))).scalar() if student_ids else 0
        data = {
            "role": "TEACHER",
            "cards": {
                "topic_count": db.execute(select(func.count(GraduationTopic.id)).where(GraduationTopic.teacher_id == user.profile_id, GraduationTopic.deleted_at.is_(None))).scalar(),
                "student_count": len(student_ids),
                "pending_selections": pending_selections, "pending_opening": pending_opening,
                "pending_midterm": pending_midterm, "pending_final": pending_final,
                "pending_material": pending_material, "risk_count": risk,
            },
            "scope": "current_year", "academic_year": year,
            "scope_map": {
                "cards.risk_count": "current_year", "cards.student_count": "current_year",
                "cards.topic_count": "all_years", "cards.pending_selections": "all_years",
                "cards.pending_opening": "all_years", "cards.pending_midterm": "all_years",
                "cards.pending_final": "all_years", "cards.pending_material": "all_years",
            },
            "announcements": ann_items, "unread": unread,
        }
        return ResponseUtil.success(data)

    # STUDENT
    from app.shared.progress import ProgressService
    from app.shared.workflow import GraduationWorkflowService

    process = GraduationWorkflowService.get_process(db, user.profile_id)
    todos = []
    if process is None:
        todos.append("请前往题目中心选择毕业设计题目")
    else:
        stage_map = {
            "OPENING": "请填写并提交开题报告",
            "MIDTERM": "请填写并提交中期报告",
            "FINAL": "请填写并提交终期答辩材料",
            "MATERIAL": "请上传毕业论文等毕业材料",
        }
        if process.current_stage in stage_map:
            todos.append(stage_map[process.current_stage])
        if process.risk_level != "NORMAL":
            todos.append(f"风险提示: {process.risk_reason or '进度存在风险, 请尽快推进'}")
    data = {
        "role": "STUDENT",
        "cards": {
            "progress_percent": process.progress_percent if process else 0,
            "current_stage": process.current_stage if process else "SELECTION",
            "current_stage_cn": STAGE_CN.get(process.current_stage, "选题") if process else "选题",
            "risk_level": process.risk_level if process else "NORMAL",
        },
        "deadline_info": ProgressService.stage_deadline_info(db, process.current_stage if process else "SELECTION"),
        "todos": todos, "announcements": ann_items, "unread": unread,
    }
    return ResponseUtil.success(data)


@router.get("/stats")
def admin_stats(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "ADMIN":
        from app.core.exceptions import PermissionDeniedException

        raise PermissionDeniedException("仅管理员可查看全局统计")

    # D73: 进度类指标统一当前学年; 实体总量(学生总数/教师工作量/AI 用量)保持全量,
    # 由响应 scope_map 显式标注, 前端可据此加"当前学年/全量"标签。
    year = academic_year_for(db)
    year_cycles = _current_year_cycles(db)

    total_students = db.execute(select(func.count(StudentProfile.id)).where(
        StudentProfile.deleted_at.is_(None))).scalar_one()

    # 进度分布(饼图) —— 当前学年
    stage_dist_rows = db.execute(
        select(GraduationProcess.current_stage, func.count(GraduationProcess.id))
        .where(GraduationProcess.cycle_id.in_(year_cycles))
        .group_by(GraduationProcess.current_stage)
    ).all()
    no_selection = db.execute(
        select(func.count(StudentProfile.id)).where(
            StudentProfile.deleted_at.is_(None),
            ~StudentProfile.id.in_(select(TopicSelection.student_id).where(TopicSelection.active_flag == 1)),
        )
    ).scalar()
    stage_dist = [{"name": "未选题", "value": no_selection}] + [
        {"name": STAGE_CN.get(s, s), "value": c} for s, c in stage_dist_rows
    ]

    # D66: 各阶段通过情况 —— 统一 {name, value, total} 三元组, 比率由前端 value/total 计算,
    # 不再把裸计数塞进 value 冒充比率; 计数全部带当前学年 cycle 条件(与完成口径同源同域)。
    selected = db.execute(select(func.count(func.distinct(TopicSelection.student_id))).where(
        TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1,
        TopicSelection.cycle_id.in_(year_cycles))).scalar_one()

    def _stage_counts(model) -> tuple[int, int]:
        """(已通过数, 已评审数) —— 原 _rate 的死代码在此真正启用, 且带 cycle 过滤."""
        conditions = [model.cycle_id.in_(year_cycles)]
        if model is TopicSelection:
            conditions.append(model.active_flag == 1)
        approved = db.execute(select(func.count(model.id)).where(
            *conditions, model.status == "APPROVED")).scalar_one()
        reviewed = db.execute(select(func.count(model.id)).where(
            *conditions, model.status.in_(["APPROVED", "REJECTED", "RESUBMIT_REQUIRED", "SUBMITTED"]))).scalar_one()
        return approved, reviewed

    year_process_total = db.execute(select(func.count(GraduationProcess.id)).where(
        GraduationProcess.cycle_id.in_(year_cycles))).scalar_one()
    pass_counts = []
    for _name, _model in (("选题", TopicSelection), ("开题", OpeningReport),
                          ("中期", MidtermReport), ("终期", FinalDefense)):
        _approved, _reviewed = _stage_counts(_model)
        pass_counts.append({"name": _name, "value": _approved, "total": _reviewed})
    # D64: "已毕业"柱与后台"已完成"同源(completed_at 投影 + 当前学年), 不再数材料审核通过
    pass_counts.append({"name": "已毕业", "value": _completed_count(db), "total": year_process_total})

    # 最近7天提交趋势(折线图)
    trend = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).date()
        next_day = day + timedelta(days=1)
        count = 0
        for model in (OpeningReport, MidtermReport, FinalDefense, GraduationMaterial):
            count += db.execute(
                select(func.count(model.id)).where(
                    model.submitted_at >= day, model.submitted_at < next_day,
                    model.cycle_id.in_(year_cycles))
            ).scalar()
        trend.append({"date": day.isoformat(), "value": count})

    # 教师指导人数(横向柱状图)
    supervision_rows = db.execute(
        select(SysUser.real_name, func.count(TopicSelection.id))
        .join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
        .join(TopicSelection, TopicSelection.teacher_id == TeacherProfile.id)
        .where(TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)
        .group_by(SysUser.real_name).order_by(func.count(TopicSelection.id).desc()).limit(10)
    ).all()
    supervision = [{"name": name, "value": c} for name, c in supervision_rows]

    # 风险分布 —— 当前学年(与 completed/pass_counts 同域)
    risk_rows = db.execute(
        select(GraduationProcess.risk_level, func.count(GraduationProcess.id))
        .where(GraduationProcess.cycle_id.in_(year_cycles))
        .group_by(GraduationProcess.risk_level)
    ).all()
    risk_dist = [{"name": RISK_CN.get(r, r), "value": c} for r, c in risk_rows]

    # AI 使用统计
    ai_total = db.execute(select(func.count(AIUsageLog.id))).scalar()
    ai_today = db.execute(select(func.count(AIUsageLog.id)).where(AIUsageLog.created_at >= datetime.now().replace(hour=0, minute=0, second=0))).scalar()
    scene_rows = db.execute(
        select(AIUsageLog.scene, func.count(AIUsageLog.id))
        .where(AIUsageLog.scene.is_not(None)).group_by(AIUsageLog.scene)
        .order_by(func.count(AIUsageLog.id).desc())
    ).all()
    scene_cn = {
        "GENERATE_QUESTIONS": "生成问题", "DEFENSE_QA": "回答评价",
        "PROGRESS_ANALYSIS": "进度分析", "PPT_OUTLINE": "PPT大纲", "QA": "知识问答",
    }
    ai_scenes = [{"name": scene_cn.get(s, s), "value": c} for s, c in scene_rows]

    return ResponseUtil.success({
        "stage_distribution": stage_dist,
        "pass_counts": pass_counts,
        "student_total": total_students,
        "selected_total": selected,
        "submit_trend": trend,
        "supervision": supervision,
        "risk_distribution": risk_dist,
        "ai_usage": {"total": ai_total, "today": ai_today, "scenes": ai_scenes},
        "scope": "current_year",
        "academic_year": year,
        "scope_map": {
            "stage_distribution": "current_year", "pass_counts": "current_year",
            "submit_trend": "current_year", "risk_distribution": "current_year",
            "selected_total": "current_year",
            "student_total": "all_years", "no_selection": "all_years",
            "supervision": "all_years", "ai_usage": "all_years",
        },
    })


@router.get("/defense-stats")
def defense_stats(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """答辩/评审/归档运营统计(roadmap P4.3): 场次容量、未分配评委、待评审、延期、归档完成率."""
    from app.models.defense import (
        ArchivePackage,
        DefenseAssignment,
        DefenseExaminer,
        DefenseResult,
        DefenseSession,
        ExaminerScore,
    )
    from app.models.enums import ArchiveStatus, ResultStatus, ScoreStatus, SeatStatus, SessionStatus as SS

    if user.role != "ADMIN":
        from app.core.exceptions import PermissionDeniedException

        raise PermissionDeniedException("仅管理员可查看全局统计")

    active_sessions = select(DefenseSession.id).where(
        DefenseSession.status.in_([SS.PUBLISHED.value, SS.IN_PROGRESS.value]))
    total_capacity = db.execute(
        select(func.coalesce(func.sum(DefenseSession.capacity), 0))
        .where(DefenseSession.status.in_([SS.DRAFT.value, SS.READY.value, SS.PUBLISHED.value, SS.IN_PROGRESS.value]))
    ).scalar()
    assigned_total = db.execute(
        select(func.count(DefenseAssignment.id))
        .join(DefenseSession, DefenseSession.id == DefenseAssignment.session_id)
        .where(DefenseSession.status.in_([SS.DRAFT.value, SS.READY.value, SS.PUBLISHED.value, SS.IN_PROGRESS.value]))
    ).scalar()

    sessions_no_examiner = db.execute(
        select(func.count(DefenseSession.id)).where(
            DefenseSession.id.in_(active_sessions),
            ~DefenseSession.id.in_(select(DefenseExaminer.session_id)),
        )
    ).scalar()

    # 待评审: 有效场次中尚无有效 SUBMITTED 评分的席位
    attended_assignments = db.execute(
        select(DefenseAssignment.id).join(DefenseSession, DefenseSession.id == DefenseAssignment.session_id)
        .where(DefenseSession.status.in_([SS.PUBLISHED.value, SS.IN_PROGRESS.value]))
    ).scalars().all()
    scored_assignments = db.execute(
        select(ExaminerScore.assignment_id).where(ExaminerScore.status == ScoreStatus.SUBMITTED.value)
    ).scalars().all()
    pending_review = len([a for a in attended_assignments if a not in set(scored_assignments)])

    postponed = db.execute(
        select(func.count(DefenseAssignment.id)).where(DefenseAssignment.seat_status == SeatStatus.POSTPONED.value)
    ).scalar()

    # 归档完成率: 归档数 / 归档达标数(终期结论 PUBLISHED PASS 且材料 APPROVED 的流程)
    # D65: 分母按 cycle 去重 —— 一个 cycle 有多份材料/多条结论时只算一个应归档单元, 归档率不会虚高
    eligible_cycles = db.execute(
        select(DefenseResult.cycle_id)
        .join(GraduationMaterial, GraduationMaterial.cycle_id == DefenseResult.cycle_id)
        .where(DefenseResult.stage == "FINAL", DefenseResult.status == ResultStatus.PUBLISHED.value,
               DefenseResult.decision == "PASS", GraduationMaterial.status == "APPROVED")
        .distinct()
    ).scalars().all()
    archived = db.execute(
        select(func.count(func.distinct(ArchivePackage.cycle_id))).where(
            ArchivePackage.cycle_id.in_(eligible_cycles) if eligible_cycles else ArchivePackage.id.is_(None),
            ArchivePackage.status == ArchiveStatus.ARCHIVED.value)
    ).scalar()
    results_published = db.execute(
        select(func.count(DefenseResult.id)).where(DefenseResult.status == ResultStatus.PUBLISHED.value)).scalar()

    return ResponseUtil.success({
        "capacity_total": int(total_capacity),
        "assigned_total": assigned_total,
        "sessions_no_examiner": sessions_no_examiner,
        "pending_review": pending_review,
        "postponed": postponed,
        "archive_eligible": len(eligible_cycles),
        "archive_archived": archived,
        "archive_rate": round(archived / len(eligible_cycles) * 100, 1) if eligible_cycles else 0,
        "results_published": results_published,
    })
