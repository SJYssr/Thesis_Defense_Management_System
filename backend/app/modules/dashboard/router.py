"""Dashboard 模块: 角色首页概览 + 管理员统计图表数据(roadmap 五十二~五十四)."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser, get_current_user
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.ai import AIUsageLog
from app.models.business import (
    FinalDefense,
    GraduationMaterial,
    GraduationProcess,
    GraduationTopic,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.enums import ProcessStage
from app.models.system import Announcement, Notification
from app.models.user import StudentProfile, SysUser, TeacherProfile

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

STAGE_CN = {"SELECTION": "选题", "OPENING": "开题", "MIDTERM": "中期", "FINAL": "终期", "MATERIAL": "材料", "COMPLETED": "已完成"}
RISK_CN = {"NORMAL": "正常", "WARNING": "警告", "HIGH": "高风险"}


@router.get("/home")
def home(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    announcements = db.execute(
        select(Announcement).where(Announcement.status == "PUBLISHED").order_by(Announcement.publish_time.desc()).limit(5)
    ).scalars().all()
    ann_items = [{"id": a.id, "title": a.title, "priority": a.priority,
                  "publish_time": a.publish_time.isoformat() if a.publish_time else None} for a in announcements]
    unread = db.execute(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.is_read == 0)).scalar()

    if user.role == "ADMIN":
        data = {
            "role": "ADMIN",
            "cards": {
                "student_count": db.execute(select(func.count(StudentProfile.id)).where(StudentProfile.deleted_at.is_(None))).scalar(),
                "teacher_count": db.execute(select(func.count(TeacherProfile.id))).scalar(),
                "topic_count": db.execute(select(func.count(GraduationTopic.id)).where(GraduationTopic.deleted_at.is_(None))).scalar(),
                "selected_count": db.execute(select(func.count(TopicSelection.id)).where(TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)).scalar(),
                "completed_count": db.execute(select(func.count(GraduationProcess.id)).where(GraduationProcess.current_stage == "COMPLETED")).scalar(),
                "risk_count": db.execute(select(func.count(GraduationProcess.id)).where(GraduationProcess.risk_level.in_(["WARNING", "HIGH"]))).scalar(),
            },
            "announcements": ann_items, "unread": unread,
        }
        return ResponseUtil.success(data)

    if user.role == "TEACHER":
        pending_selections = db.execute(select(func.count(TopicSelection.id)).where(TopicSelection.teacher_id == user.profile_id, TopicSelection.status == "PENDING")).scalar()
        pending_opening = db.execute(select(func.count(OpeningReport.id)).where(OpeningReport.teacher_id == user.profile_id, OpeningReport.status == "SUBMITTED")).scalar()
        pending_midterm = db.execute(select(func.count(MidtermReport.id)).where(MidtermReport.teacher_id == user.profile_id, MidtermReport.status == "SUBMITTED")).scalar()
        pending_final = db.execute(select(func.count(FinalDefense.id)).where(FinalDefense.teacher_id == user.profile_id, FinalDefense.status == "SUBMITTED")).scalar()
        pending_material = db.execute(select(func.count(GraduationMaterial.id)).where(GraduationMaterial.teacher_id == user.profile_id, GraduationMaterial.status == "SUBMITTED")).scalar()
        student_ids = [r for r in db.execute(select(TopicSelection.student_id).where(TopicSelection.teacher_id == user.profile_id, TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)).scalars().all()]
        risk = db.execute(select(func.count(GraduationProcess.id)).where(GraduationProcess.student_id.in_(student_ids), GraduationProcess.risk_level.in_(["WARNING", "HIGH"]))).scalar() if student_ids else 0
        data = {
            "role": "TEACHER",
            "cards": {
                "topic_count": db.execute(select(func.count(GraduationTopic.id)).where(GraduationTopic.teacher_id == user.profile_id, GraduationTopic.deleted_at.is_(None))).scalar(),
                "student_count": len(student_ids),
                "pending_selections": pending_selections, "pending_opening": pending_opening,
                "pending_midterm": pending_midterm, "pending_final": pending_final,
                "pending_material": pending_material, "risk_count": risk,
            },
            "announcements": ann_items, "unread": unread,
        }
        return ResponseUtil.success(data)

    # STUDENT
    from app.shared.progress import ProgressService

    process = db.execute(select(GraduationProcess).where(GraduationProcess.student_id == user.profile_id)).scalar_one_or_none()
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

    total_students = db.execute(select(func.count(StudentProfile.id)).where(StudentProfile.deleted_at.is_(None))).scalar() or 1

    # 进度分布(饼图)
    stage_dist_rows = db.execute(
        select(GraduationProcess.current_stage, func.count(GraduationProcess.id)).group_by(GraduationProcess.current_stage)
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

    # 各阶段通过率(柱状图)
    def _rate(model, teacher_field=True):
        approved = db.execute(select(func.count(model.id)).where(model.status == "APPROVED")).scalar()
        submitted_total = db.execute(
            select(func.count(model.id)).where(model.status.in_(["APPROVED", "REJECTED", "RESUBMIT_REQUIRED", "SUBMITTED"]))
        ).scalar()
        return round(approved / submitted_total * 100, 1) if submitted_total else 0

    selected = db.execute(select(func.count(TopicSelection.id)).where(TopicSelection.active_flag == 1)).scalar()
    pass_rates = [
        {"name": "选题", "value": db.execute(select(func.count(TopicSelection.id)).where(TopicSelection.status == "APPROVED", TopicSelection.active_flag == 1)).scalar()},
        {"name": "开题", "value": db.execute(select(func.count(OpeningReport.id)).where(OpeningReport.status == "APPROVED")).scalar()},
        {"name": "中期", "value": db.execute(select(func.count(MidtermReport.id)).where(MidtermReport.status == "APPROVED")).scalar()},
        {"name": "终期", "value": db.execute(select(func.count(FinalDefense.id)).where(FinalDefense.status == "APPROVED")).scalar()},
        {"name": "毕业", "value": db.execute(select(func.count(GraduationMaterial.id)).where(GraduationMaterial.status == "APPROVED")).scalar()},
    ]

    # 最近7天提交趋势(折线图)
    trend = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).date()
        next_day = day + timedelta(days=1)
        count = 0
        for model in (OpeningReport, MidtermReport, FinalDefense, GraduationMaterial):
            count += db.execute(
                select(func.count(model.id)).where(model.submitted_at >= day, model.submitted_at < next_day)
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

    # 风险分布
    risk_rows = db.execute(
        select(GraduationProcess.risk_level, func.count(GraduationProcess.id)).group_by(GraduationProcess.risk_level)
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
        "pass_counts": pass_rates,
        "submit_trend": trend,
        "supervision": supervision,
        "risk_distribution": risk_dist,
        "ai_usage": {"total": ai_total, "today": ai_today, "scenes": ai_scenes},
    })
