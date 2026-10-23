"""Progress 模块: 毕设进度时间轴/教师提醒/风险视图."""
import logging
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
from app.shared.cycle import academic_year_for, formal_defense_snapshot
from app.shared.progress import ProgressService, STAGE_LABELS
from app.shared.workflow import GraduationWorkflowService

logger = logging.getLogger(__name__)

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
    # D71: GET 只读现值 —— 请求内不再 recompute()+commit(), 重算下沉到状态变更点与每日定时任务
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
    text = (keyword or "").strip()
    if text:
        # D57: 关键字条件下推到 SQL(姓名/学号子查询), count 与分页同源, 不再"取页后内存过滤"
        conds.append(GraduationProcess.student_id.in_(
            select(StudentProfile.id)
            .join(SysUser, SysUser.id == StudentProfile.user_id)
            .where(or_(SysUser.real_name.contains(text), StudentProfile.student_no.contains(text)))
        ))
    total = db.execute(select(func.count(GraduationProcess.id)).where(*conds)).scalar()
    processes = db.execute(
        select(GraduationProcess).where(*conds).order_by(GraduationProcess.risk_level.desc(), GraduationProcess.progress_percent)
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = [_progress_item(db, p, p.student_id) for p in processes]
    return ResponseUtil.page(items, total, page, page_size)


@teacher_router.get("/{student_id}/progress")
def student_progress_detail(student_id: int, user: CurrentUser = Depends(require_teacher), db: Session = Depends(get_db)):
    PermissionService.ensure_teacher_can_access_student(db, user, student_id)
    # D71: 同为 GET 读接口, 只读现值, 请求内不写库
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
    # D63/D72: 全表统计只有一个学年口径 —— 显式学年优先, 缺省即当前学年,
    # 不再混用"跨学年累计 + current_stage 字面值"; 完成与否只认 progress.compute() 的投影 completed_at。
    year = academic_year or academic_year_for(db)
    year_cycles = select(GraduationCycle.id).where(GraduationCycle.academic_year == year)
    year_students = select(GraduationCycle.student_id).where(GraduationCycle.academic_year == year)
    base = [GraduationProcess.cycle_id.in_(year_cycles)]
    total_students = db.execute(select(func.count(StudentProfile.id)).where(
        StudentProfile.deleted_at.is_(None), StudentProfile.id.in_(year_students))).scalar_one()
    process_total = db.execute(select(func.count(GraduationProcess.id)).where(*base)).scalar_one()
    completed = db.execute(select(func.count(GraduationProcess.id)).where(
        *base, GraduationProcess.completed_at.is_not(None))).scalar_one()
    risk = {level: db.execute(select(func.count(GraduationProcess.id)).where(
        *base, GraduationProcess.completed_at.is_(None),
        GraduationProcess.risk_level == level)).scalar_one()
            for level in ("NORMAL", "WARNING", "HIGH")}
    stage_models = [("选题", TopicSelection), ("开题报告", OpeningReport),
                    ("中期材料", MidtermReport), ("终期材料", FinalDefense),
                    ("毕业材料", GraduationMaterial)]
    stages = []
    for label, model in stage_models:
        # D72: 五段计数统一口径 —— 只认 APPROVED + 同一学年 cycle, 选题额外只认 active_flag=1
        conditions = [model.status == "APPROVED", model.cycle_id.in_(year_cycles)]
        if model is TopicSelection:
            conditions.append(model.active_flag == 1)
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


# ---------- 逾期风险扫描(D67) ----------

# 逾期扫描只有一个实现: app.tasks.risk_scan.scan_overdue_risk_batched()。
# 本模块只保留"入队 / 直接跑"的差异, 批处理主体不在这里复制(避免两套规则引擎漂移)。


def _overdue_scan_module():
    """延迟取任务模块: web 启动不初始化 Celery, 且调用走模块属性以便测试替换."""
    from app.tasks import risk_scan

    return risk_scan


def __getattr__(name):
    """OVERDUE_SCAN_BATCH_SIZE 单一来源在 app.tasks.risk_scan(PEP 562 模块级转发)."""
    if name == "OVERDUE_SCAN_BATCH_SIZE":
        return _overdue_scan_module().OVERDUE_SCAN_BATCH_SIZE
    raise AttributeError(name)


def _enqueue_overdue_scan() -> bool:
    """优先分发到 Celery(与 app/tasks/dispatcher.py 同一套 CELERY_ENABLED/CELERY_AVAILABLE 分支)。

    返回 True = 已入队, 请求立即返回受理信息; False = 队列不可用, 交给同步兜底。
    """
    try:
        from app.tasks.celery_app import CELERY_AVAILABLE, CELERY_ENABLED, celery_app
        from app.tasks.risk_scan import daily_scan_task
    except Exception:  # noqa: BLE001 — 任务模块不可导入等同队列不可用
        logger.warning("逾期扫描入队失败: 任务模块不可导入", exc_info=True)
        return False
    if not (CELERY_ENABLED and CELERY_AVAILABLE and celery_app is not None):
        return False
    if daily_scan_task is None or celery_app.conf.task_always_eager:
        return False
    try:
        queued = daily_scan_task.delay()
    except Exception:  # noqa: BLE001 — broker 不可达时退回同步兜底, 不能让请求失败
        logger.warning("逾期扫描入队失败, 转同步兜底", exc_info=True)
        return False
    logger.info("逾期风险扫描已入队: task_id=%s", getattr(queued, "id", None))
    return True


def _run_overdue_scan_batched(session_factory=None) -> dict:
    """同步兜底: 直接转调逾期扫描唯一实现(每批一个事务, 见 app/tasks/risk_scan.py)."""
    return _overdue_scan_module().scan_overdue_risk_batched(session_factory=session_factory)


@admin_router.post("/overdue-scan")
def admin_overdue_scan(user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """手动触发逾期风险扫描(与每日 01:00 Celery Beat 同一逻辑, roadmap 八十六章).

    D67: HTTP 请求不再遍历全表 —— 优先入队后立即返回受理信息;
    只有 Celery 不可用时才同步兜底, 且兜底按批提交。
    """
    if _enqueue_overdue_scan():
        summary = {"mode": "celery", "queued": True, "scanned": 0, "normal": 0,
                   "warning": 0, "high": 0, "notified": 0}
        message = "逾期风险扫描已提交后台队列"
    else:
        summary = _run_overdue_scan_batched()
        message = "逾期风险扫描完成(队列不可用, 已分批同步执行)"
    db.add(OperationLog(user_id=user.id, username=user.username, module="PROGRESS", action="OVERDUE_SCAN",
                        detail=f"扫描{summary['scanned']}个流程: 高风险{summary['high']} 警告{summary['warning']}"))
    db.commit()
    return ResponseUtil.success(summary, message=message)
