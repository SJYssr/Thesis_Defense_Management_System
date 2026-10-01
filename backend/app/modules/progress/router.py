"""Progress 模块: 毕设进度时间轴/教师提醒/风险视图."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.permissions import CurrentUser, PermissionService, require_admin, require_student, require_teacher
from app.core.response import ResponseUtil
from app.db.mysql import get_db
from app.models.business import (FinalDefense, GraduationCycle, GraduationMaterial,
                                 GraduationProcess, GraduationTopic, MidtermReport,
                                 OpeningReport, TopicSelection)
from app.models.enums import ProcessStage
from app.models.system import OperationLog, Reminder, ReviewRecord
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.shared.events import push_notification
from app.shared.cycle import formal_defense_snapshot
from app.shared.progress import ProgressService, STAGE_LABELS
from app.shared.workflow import GraduationWorkflowService

router = APIRouter(prefix="/student/progress", tags=["Progress"])
teacher_router = APIRouter(prefix="/teacher/students", tags=["Progress-Teacher"])
admin_router = APIRouter(prefix="/admin/progress", tags=["Progress-Admin"])

STAGE_STATUS_CN = {"APPROVED": "已通过", "SUBMITTED": "待审核", "DRAFT": "草稿中",
                   "REJECTED": "已驳回", "RESUBMIT_REQUIRED": "需重新提交", "NOT_SUBMITTED": "未提交", "PENDING": "待审核"}


def _student_base(db: Session, student_id: int) -> dict:
    row = db.execute(
        select(StudentProfile, SysUser).join(SysUser, SysUser.id == StudentProfile.user_id).where(StudentProfile.id == student_id)
    ).first()
    return {
        "student_id": student_id,
        "student_name": row[1].real_name if row else None,
        "student_no": row[0].student_no if row else None,
        "grade": row[0].grade if row else None,
    }


def _timeline(db: Session, student_id: int) -> list[dict]:
    snap = GraduationWorkflowService.get_stage_snapshot(db, student_id)
    steps = [
        (ProcessStage.SELECTION, snap["selection"].status if snap["selection"] else "NOT_SUBMITTED", snap["selection"]),
        (ProcessStage.OPENING, snap["opening"].status if snap["opening"] else "NOT_SUBMITTED", snap["opening"]),
        (ProcessStage.MIDTERM, snap["midterm"].status if snap["midterm"] else "NOT_SUBMITTED", snap["midterm"]),
        (ProcessStage.FINAL, snap["final"].status if snap["final"] else "NOT_SUBMITTED", snap["final"]),
        (ProcessStage.MATERIAL, snap["material"].status if snap["material"] else "NOT_SUBMITTED", snap["material"]),
    ]
    timeline = []
    for stage, status, obj in steps:
        item = {
            "stage": stage.value, "stage_cn": STAGE_LABELS[stage],
            "status": status, "status_cn": STAGE_STATUS_CN.get(status, status),
            "done": status == "APPROVED",
            "submitted_at": obj.submitted_at.isoformat() if obj and getattr(obj, "submitted_at", None) else None,
            "reviewed_at": obj.reviewed_at.isoformat() if obj and getattr(obj, "reviewed_at", None) else None,
        }
        timeline.append(item)
    return timeline


def _progress_item(db: Session, process: GraduationProcess, student_id: int) -> dict:
    data = _student_base(db, student_id)
    topic_title = None
    teacher_name = None
    if process.topic_id:
        topic_title = db.execute(select(GraduationTopic.title).where(GraduationTopic.id == process.topic_id)).scalar()
    if process.teacher_id:
        teacher_name = db.execute(
            select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
            .where(TeacherProfile.id == process.teacher_id)
        ).scalar()
    student = db.get(StudentProfile, student_id)
    college = db.get(College, student.college_id) if student and student.college_id else None
    major = db.get(Major, student.major_id) if student and student.major_id else None
    cycle = db.get(GraduationCycle, process.cycle_id) if process.cycle_id else None
    data.update({
        "topic_id": process.topic_id, "topic_title": topic_title, "teacher_name": teacher_name,
        "college_name": college.name if college else None,
        "major_name": major.name if major else None,
        "academic_year": cycle.academic_year if cycle else None,
        "teacher_id": process.teacher_id,
        "current_stage": process.current_stage,
        "current_stage_cn": STAGE_LABELS.get(ProcessStage(process.current_stage), process.current_stage),
        "progress_percent": process.progress_percent,
        "risk_level": process.risk_level,
        "risk_reason": process.risk_reason,
        "started_at": process.started_at.isoformat() if process.started_at else None,
        "completed_at": process.completed_at.isoformat() if process.completed_at else None,
        "updated_at": process.updated_at.isoformat() if process.updated_at else None,
    })
    return data


# ---------- 学生 ----------

@router.get("")
def my_progress(user: CurrentUser = Depends(require_student), db: Session = Depends(get_db)):
    student_id = user.profile_id
    ProgressService.recompute(db, student_id)  # 惰性重算, 保证截止时间相关的风险实时
    db.commit()
    process = GraduationWorkflowService.get_process(db, student_id)
    item = _progress_item(db, process, student_id) if process else {
        **_student_base(db, student_id), "current_stage": "SELECTION", "current_stage_cn": "选题",
        "progress_percent": 0, "risk_level": "NORMAL", "risk_reason": None, "topic_title": None,
    }
    sel = GraduationWorkflowService.get_active_selection(db, student_id)
    item["selection"] = {
        "topic_title": db.get(GraduationTopic, sel.topic_id).title if sel and sel.topic_id else None,
        "status": sel.status if sel else "NOT_SELECTED",
    } if sel else None
    item["timeline"] = _timeline(db, student_id)
    item["formal_defense"] = formal_defense_snapshot(db, student_id)
    item["deadline_info"] = ProgressService.stage_deadline_info(db, item["current_stage"])
    reminders = db.execute(select(Reminder).where(Reminder.student_id == student_id)
                           .order_by(Reminder.id.desc()).limit(5)).scalars().all()
    item["reminders"] = [{"id": reminder.id, "title": reminder.title,
                          "content": reminder.content,
                          "created_at": reminder.created_at.isoformat() if reminder.created_at else None}
                         for reminder in reminders]
    return ResponseUtil.success(item)


# ---------- 教师 ----------

@teacher_router.get("/progress")
def teacher_students_progress(risk_level: str | None = None, stage: str | None = None,
                              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                              keyword: str | None = None,
                              user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    student_ids = PermissionService.teacher_student_ids(db, user.profile_id)
    if not student_ids:
        return ResponseUtil.page([], 0, page, page_size)
    conds = [GraduationProcess.student_id.in_(student_ids)]
    if risk_level:
        conds.append(GraduationProcess.risk_level == risk_level)
    if stage:
        conds.append(GraduationProcess.current_stage == stage)
    total = db.execute(select(func.count(GraduationProcess.id)).where(*conds)).scalar()
    processes = db.execute(
        select(GraduationProcess).where(*conds).order_by(GraduationProcess.risk_level.desc(), GraduationProcess.progress_percent)
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = [_progress_item(db, p, p.student_id) for p in processes]
    if keyword:
        items = [i for i in items if keyword in (i.get("student_name") or "") or keyword in (i.get("student_no") or "")]
    return ResponseUtil.page(items, total, page, page_size)


@teacher_router.get("/{student_id}/progress")
def student_progress_detail(student_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    PermissionService.ensure_teacher_can_access_student(db, user, student_id)
    ProgressService.recompute(db, student_id)
    db.commit()
    process = GraduationWorkflowService.get_process(db, student_id)
    item = _progress_item(db, process, student_id) if process else _student_base(db, student_id)
    item["timeline"] = _timeline(db, student_id)
    item["deadline_info"] = ProgressService.stage_deadline_info(db, item.get("current_stage", "SELECTION"))
    return ResponseUtil.success(item)


class ReminderRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    content: str = Field(min_length=2, max_length=2000)


@teacher_router.post("/{student_id}/reminders")
def send_reminder(student_id: int, body: ReminderRequest, user: CurrentUser = Depends(require_teacher),
                  db: Session = Depends(get_db)):
    PermissionService.ensure_teacher_can_access_student(db, user, student_id)
    student_user_id = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == student_id)).scalar()
    db.add(Reminder(sender_id=user.id, receiver_id=student_user_id, student_id=student_id,
                    title=body.title, content=body.content))
    push_notification(db, user_id=student_user_id, type="REMINDER", title=body.title, content=body.content)
    db.commit()
    return ResponseUtil.success(message="提醒已发送")


# ---------- 管理员 ----------

@admin_router.get("")
def admin_progress(risk_level: str | None = None, stage: str | None = None,
                   academic_year: str | None = None, college_id: int | None = None,
                   major_id: int | None = None, teacher_id: int | None = None,
                   student: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if risk_level:
        if risk_level == "COMPLETED":
            conds.append(GraduationProcess.current_stage == "COMPLETED")
        else:
            conds.extend([GraduationProcess.risk_level == risk_level,
                          GraduationProcess.current_stage != "COMPLETED"])
    if stage:
        conds.append(GraduationProcess.current_stage == stage)
    if academic_year:
        conds.append(GraduationProcess.cycle_id.in_(select(GraduationCycle.id).where(
            GraduationCycle.academic_year == academic_year)))
    if teacher_id:
        conds.append(GraduationProcess.teacher_id == teacher_id)
    if college_id or major_id or student:
        students = select(StudentProfile.id).join(SysUser, SysUser.id == StudentProfile.user_id)
        if college_id:
            students = students.where(StudentProfile.college_id == college_id)
        if major_id:
            students = students.where(StudentProfile.major_id == major_id)
        if student:
            students = students.where(or_(SysUser.real_name.contains(student),
                                          StudentProfile.student_no.contains(student)))
        conds.append(GraduationProcess.student_id.in_(students))
    total = db.execute(select(func.count(GraduationProcess.id)).where(*conds)).scalar()
    processes = db.execute(
        select(GraduationProcess).where(*conds).order_by(GraduationProcess.risk_level.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return ResponseUtil.page([_progress_item(db, p, p.student_id) for p in processes], total, page, page_size)


@admin_router.get("/summary")
def admin_progress_summary(academic_year: str | None = None,
                           _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    base = [GraduationProcess.cycle_id.in_(select(GraduationCycle.id).where(
        GraduationCycle.academic_year == academic_year))] if academic_year else []
    total_students = db.execute(select(func.count(StudentProfile.id)).where(
        StudentProfile.deleted_at.is_(None),
        *([StudentProfile.id.in_(select(GraduationCycle.student_id).where(
            GraduationCycle.academic_year == academic_year))] if academic_year else []))).scalar_one()
    process_total = db.execute(select(func.count(GraduationProcess.id)).where(*base)).scalar_one()
    completed = db.execute(select(func.count(GraduationProcess.id)).where(
        *base, GraduationProcess.current_stage == "COMPLETED")).scalar_one()
    risk = {level: db.execute(select(func.count(GraduationProcess.id)).where(
        *base, GraduationProcess.current_stage != "COMPLETED",
        GraduationProcess.risk_level == level)).scalar_one()
            for level in ("NORMAL", "WARNING", "HIGH")}
    stage_models = [("选题", TopicSelection), ("开题报告", OpeningReport),
                    ("中期材料", MidtermReport), ("终期材料", FinalDefense),
                    ("毕业材料", GraduationMaterial)]
    stages = []
    for label, model in stage_models:
        conditions = [model.status == "APPROVED"]
        if academic_year:
            conditions.append(model.cycle_id.in_(select(GraduationCycle.id).where(
                GraduationCycle.academic_year == academic_year)))
        stages.append({"name": label, "value": db.execute(select(func.count(model.id)).where(
            *conditions)).scalar_one()})
    trend = []
    today = datetime.now().date()
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        next_day = day + timedelta(days=1)
        count = db.execute(select(func.count(GraduationProcess.id)).where(
            *base, GraduationProcess.updated_at >= day,
            GraduationProcess.updated_at < next_day)).scalar_one()
        trend.append({"date": day.isoformat(), "value": count})
    return ResponseUtil.success({"student_total": total_students, "process_total": process_total,
                                 "not_started": max(total_students - process_total, 0),
                                 "normal": risk["NORMAL"], "warning": risk["WARNING"],
                                 "high": risk["HIGH"], "completed": completed,
                                 "stages": stages, "trend": trend})


class AdminReminderRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=100)
    title: str = Field(min_length=2, max_length=255)
    content: str = Field(min_length=2, max_length=2000)


@admin_router.post("/reminders")
def admin_send_reminders(body: AdminReminderRequest, user: CurrentUser = Depends(require_admin),
                         db: Session = Depends(get_db)):
    ids = sorted(set(body.student_ids))
    recipients = db.execute(select(StudentProfile.id, StudentProfile.user_id).where(
        StudentProfile.id.in_(ids), StudentProfile.deleted_at.is_(None))).all()
    if len(recipients) != len(ids):
        raise BusinessException("部分学生不存在或已停用")
    for _, user_id in recipients:
        push_notification(db, user_id=user_id, type="REMINDER", title=body.title, content=body.content)
    db.add(OperationLog(user_id=user.id, username=user.username, module="PROGRESS", action="SEND_REMINDER",
                        detail=f"向{len(ids)}名学生发送进度提醒：{body.title}"))
    db.commit()
    return ResponseUtil.success({"sent": len(ids)}, message="提醒已发送")
