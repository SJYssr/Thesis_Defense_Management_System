"""正式答辩服务: 排期冲突校验/发布/改期 + 评委评分 + 成绩汇总发布.

状态域分离(docs §1.2):
- 材料通过 ≠ 场次安排 ≠ 正式结论; 三者只能按各自状态机流转
- 正式成绩仅由本服务按启用评分表与有效评委评分在服务端复算, AI 分数永不进入
- 已发布场次改期必须留痕(DefenseScheduleChange)并通知学生与评委
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ConflictException, PermissionDeniedException, ResourceNotFoundException
from app.models.business import GraduationCycle
from app.models.defense import (
    DefenseAssignment,
    DefenseExaminer,
    DefenseResult,
    DefenseScheduleChange,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
)
from app.models.enums import (
    ResultStatus,
    ScoreStatus,
    SeatStatus,
    SessionStatus,
)
from app.models.system import SysFile
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.shared.events import push_notification

SESSION_STATUS_CN = {
    "DRAFT": "草稿", "READY": "待发布", "PUBLISHED": "已发布", "IN_PROGRESS": "进行中",
    "FINISHED": "已结束", "CANCELLED": "已取消",
}
SEAT_STATUS_CN = {
    "SCHEDULED": "已排期", "ATTENDED": "已到场", "ABSENT": "缺席",
    "POSTPONED": "已延期", "RETAKE": "重答辩",
}
RESULT_STATUS_CN = {
    "PENDING": "待汇总", "READY": "可复核", "UNDER_REVIEW": "复核中",
    "PUBLISHED": "已发布", "WITHDRAWN": "已撤回",
}
SCORE_STATUS_CN = {"DRAFT": "暂存", "SUBMITTED": "已提交", "REOPENED": "复核重开", "SUPERSEDED": "历史版本"}
STAGE_CN = {"OPENING": "开题答辩", "MIDTERM": "中期答辩", "FINAL": "终期答辩"}


# ============ 校验工具 ============

def _overlap(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _is_online(session: DefenseSession) -> bool:
    return bool(session.online_link) and (not session.location or session.location.strip().upper() in ("", "ONLINE", "线上"))


def validate_session(db: Session, session: DefenseSession) -> list[dict]:
    """场次冲突校验: 教师时间 / 学生时间 / 场地占用 / 容量超员. 发布前强制复检."""
    conflicts: list[dict] = []

    examiner_ids = list(db.execute(
        select(DefenseExaminer.teacher_id).where(DefenseExaminer.session_id == session.id)
    ).scalars())

    # 1. 评委时间冲突
    if examiner_ids:
        rows = db.execute(
            select(DefenseExaminer, DefenseSession)
            .join(DefenseSession, DefenseSession.id == DefenseExaminer.session_id)
            .where(
                DefenseExaminer.teacher_id.in_(examiner_ids),
                DefenseSession.id != session.id,
                DefenseSession.status != SessionStatus.CANCELLED.value,
                DefenseSession.starts_at < session.ends_at,
                DefenseSession.ends_at > session.starts_at,
            )
        ).all()
        for _ex, other in rows:
            teacher_name = db.execute(
                select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
                .where(TeacherProfile.id == _ex.teacher_id)
            ).scalar()
            conflicts.append({
                "type": "TEACHER", "type_cn": "评委时间冲突",
                "object": f"{teacher_name or _ex.teacher_id}",
                "with_session_id": other.id,
                "detail": f"与场次「{other.title or other.id}」({other.starts_at:%m-%d %H:%M}~{other.ends_at:%H:%M})时间重叠",
            })

    # 2. 学生时间冲突(同流程实例在其他有效场次)
    cycle_ids = list(db.execute(
        select(DefenseAssignment.cycle_id).where(DefenseAssignment.session_id == session.id)
    ).scalars())
    if cycle_ids:
        rows = db.execute(
            select(DefenseAssignment, DefenseSession, GraduationCycle)
            .join(DefenseSession, DefenseSession.id == DefenseAssignment.session_id)
            .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
            .where(
                DefenseAssignment.cycle_id.in_(cycle_ids),
                DefenseAssignment.session_id != session.id,
                DefenseAssignment.seat_status != SeatStatus.POSTPONED.value,
                DefenseSession.status != SessionStatus.CANCELLED.value,
                DefenseSession.starts_at < session.ends_at,
                DefenseSession.ends_at > session.starts_at,
            )
        ).all()
        for _a, other, cycle in rows:
            conflicts.append({
                "type": "STUDENT", "type_cn": "学生时间冲突",
                "object": f"学生#{cycle.student_id}",
                "with_session_id": other.id,
                "detail": f"流程#{cycle.id}已排在场次「{other.title or other.id}」({other.starts_at:%m-%d %H:%M})",
            })

    # 3. 场地占用冲突(线上场不占场地)
    if not _is_online(session) and session.location:
        rows = db.execute(
            select(DefenseSession).where(
                DefenseSession.id != session.id,
                DefenseSession.location == session.location,
                DefenseSession.status != SessionStatus.CANCELLED.value,
                DefenseSession.starts_at < session.ends_at,
                DefenseSession.ends_at > session.starts_at,
            )
        ).scalars().all()
        for other in rows:
            conflicts.append({
                "type": "ROOM", "type_cn": "场地冲突",
                "object": session.location,
                "with_session_id": other.id,
                "detail": f"场地已被场次「{other.title or other.id}」({other.starts_at:%m-%d %H:%M}~{other.ends_at:%H:%M})占用",
            })

    # 4. 容量
    assigned = db.execute(
        select(DefenseAssignment.id).where(DefenseAssignment.session_id == session.id)
    ).scalars().all()
    if len(assigned) > session.capacity:
        conflicts.append({
            "type": "CAPACITY", "type_cn": "容量超员",
            "object": f"{len(assigned)}/{session.capacity}",
            "detail": f"已排 {len(assigned)} 名学生, 超出场次容量 {session.capacity}",
        })
    return conflicts


def get_effective_score(db: Session, assignment_id: int, examiner_id: int) -> ExaminerScore | None:
    """某评委对某席位的当前有效评分(最新未废弃版本)."""
    return db.execute(
        select(ExaminerScore).where(
            ExaminerScore.assignment_id == assignment_id,
            ExaminerScore.examiner_id == examiner_id,
            ExaminerScore.status != ScoreStatus.SUPERSEDED.value,
        ).order_by(ExaminerScore.version.desc())
    ).scalars().first()


def _rubric_items(weights_json: str) -> list[dict]:
    try:
        return json.loads(weights_json or "[]")
    except ValueError:
        return []


def get_rubric_for(db: Session, session: DefenseSession, cycle: GraduationCycle | None) -> ReviewRubric | None:
    """取场次生效评分表: 学生所在学院配置优先, 否则全校通用; 同版本取 id 最大."""
    year = session.academic_year
    college_id = None
    if cycle:
        college_id = db.execute(
            select(StudentProfile.college_id).where(StudentProfile.id == cycle.student_id)
        ).scalar()
    conds = [
        ReviewRubric.academic_year == year,
        ReviewRubric.stage == session.stage,
        ReviewRubric.enabled == 1,
    ]
    if college_id is not None:
        row = db.execute(
            select(ReviewRubric).where(*conds, ReviewRubric.college_id == college_id)
            .order_by(ReviewRubric.version.desc(), ReviewRubric.id.desc())
        ).scalars().first()
        if row:
            return row
    return db.execute(
        select(ReviewRubric).where(*conds, ReviewRubric.college_id.is_(None))
        .order_by(ReviewRubric.version.desc(), ReviewRubric.id.desc())
    ).scalars().first()


def compute_result(db: Session, assignment: DefenseAssignment, rubric: ReviewRubric) -> dict:
    """按有效评委评分复算加权总分: 可复算性要求保留每个评委每个评分项原始分."""
    items = _rubric_items(rubric.weights_json)
    scores = db.execute(
        select(ExaminerScore).where(
            ExaminerScore.assignment_id == assignment.id,
            ExaminerScore.status == ScoreStatus.SUBMITTED.value,
        ).order_by(ExaminerScore.version.desc())
    ).scalars().all()

    latest_by_examiner: dict[int, ExaminerScore] = {}
    for s in scores:
        latest_by_examiner.setdefault(s.examiner_id, s)
    valid = list(latest_by_examiner.values())

    detail = []
    item_totals = {it["name"]: [] for it in items}
    for s in valid:
        try:
            data = json.loads(s.scores_json or "{}")
        except ValueError:
            data = {}
        detail.append({"score_id": s.id, "examiner_id": s.examiner_id, "version": s.version,
                       "scores": data, "comment": s.comment,
                       "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None})
        for it in items:
            if it["name"] in data:
                item_totals[it["name"]].append(float(data[it["name"]]))

    item_avg = {}
    for it in items:
        values = item_totals[it["name"]]
        item_avg[it["name"]] = round(sum(values) / len(values), 2) if values else None

    if items and all(item_avg[it["name"]] is not None for it in items):
        total = round(sum(item_avg[it["name"]] * it["weight"] / 100 for it in items))
    else:
        total = None
    decision = None
    if total is not None:
        decision = "PASS" if total >= rubric.pass_line else "FAIL"

    return {
        "valid_count": len(valid),
        "min_required": rubric.min_examiners,
        "item_avg": item_avg,
        "score": total,
        "decision": decision,
        "detail": detail,
        "rubric_snapshot": json.dumps({
            "rubric_id": rubric.id, "version": rubric.version,
            "weights": items, "pass_line": rubric.pass_line, "min_examiners": rubric.min_examiners,
        }, ensure_ascii=False),
    }


# ============ 场次序列化 ============

def session_item(db: Session, s: DefenseSession, with_members: bool = True) -> dict:
    examiners = db.execute(
        select(DefenseExaminer, SysUser)
        .join(TeacherProfile, TeacherProfile.id == DefenseExaminer.teacher_id)
        .join(SysUser, SysUser.id == TeacherProfile.user_id)
        .where(DefenseExaminer.session_id == s.id)
    ).all()
    assignments = db.execute(
        select(DefenseAssignment, GraduationCycle)
        .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
        .where(DefenseAssignment.session_id == s.id)
    ).all()
    changes = db.execute(
        select(DefenseScheduleChange).where(DefenseScheduleChange.session_id == s.id).order_by(DefenseScheduleChange.id.desc())
    ).scalars().all()

    item = {
        "id": s.id, "academic_year": s.academic_year, "stage": s.stage, "stage_cn": STAGE_CN.get(s.stage, s.stage),
        "title": s.title, "starts_at": s.starts_at.isoformat() if s.starts_at else None,
        "ends_at": s.ends_at.isoformat() if s.ends_at else None,
        "location": s.location, "online_link": s.online_link,
        "is_online": _is_online(s),
        "capacity": s.capacity, "status": s.status, "status_cn": SESSION_STATUS_CN.get(s.status, s.status),
        "revision": s.revision,
        "examiner_count": len(examiners),
        "assignment_count": len(assignments),
        "changes": [{
            "before_starts_at": c.before_starts_at.isoformat() if c.before_starts_at else None,
            "after_starts_at": c.after_starts_at.isoformat() if c.after_starts_at else None,
            "before_location": c.before_location, "after_location": c.after_location,
            "reason": c.reason, "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in changes],
    }
    if with_members:
        item["examiners"] = [{
            "id": ex.id, "teacher_id": ex.teacher_id, "teacher_name": u.real_name,
            "role": ex.examiner_role, "role_cn": {"CHAIR": "主席", "EXAMINER": "评委", "REVIEWER": "评阅人"}.get(ex.examiner_role, ex.examiner_role),
        } for ex, u in examiners]
        item["assignments"] = [assignment_item(db, a, cycle) for a, cycle in assignments]
    return item


def assignment_item(db: Session, a: DefenseAssignment, cycle: GraduationCycle | None = None) -> dict:
    if cycle is None:
        cycle = db.get(GraduationCycle, a.cycle_id)
    stu_user = None
    student_no = None
    college_id = None
    if cycle:
        row = db.execute(
            select(SysUser, StudentProfile).join(StudentProfile, StudentProfile.user_id == SysUser.id)
            .where(StudentProfile.id == cycle.student_id)
        ).first()
        if row:
            stu_user, sp = row
            student_no = sp.student_no
            college_id = sp.college_id
    topic_title = None
    if cycle and cycle.topic_id:
        from app.models.business import GraduationTopic

        topic_title = db.execute(select(GraduationTopic.title).where(GraduationTopic.id == cycle.topic_id)).scalar()
    teacher_name = None
    if cycle and cycle.teacher_id:
        teacher_name = db.execute(
            select(SysUser.real_name).join(TeacherProfile, TeacherProfile.user_id == SysUser.id)
            .where(TeacherProfile.id == cycle.teacher_id)
        ).scalar()

    effective_scores = db.execute(
        select(ExaminerScore).where(
            ExaminerScore.assignment_id == a.id,
            ExaminerScore.status != ScoreStatus.SUPERSEDED.value,
        )
    ).scalars().all()
    submitted = [x for x in effective_scores if x.status == ScoreStatus.SUBMITTED.value]
    result = db.execute(select(DefenseResult).where(DefenseResult.assignment_id == a.id)).scalar_one_or_none()

    return {
        "id": a.id, "session_id": a.session_id, "cycle_id": a.cycle_id,
        "academic_year": cycle.academic_year if cycle else None,
        "student_id": cycle.student_id if cycle else None,
        "student_name": stu_user.real_name if stu_user else None,
        "student_no": student_no,
        "topic_title": topic_title, "teacher_name": teacher_name,
        "seat_status": a.seat_status, "seat_status_cn": SEAT_STATUS_CN.get(a.seat_status, a.seat_status),
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "remark": a.remark,
        "score_count": len(submitted),
        "has_result": result is not None,
        "result_status": result.status if result else None,
        "result_status_cn": RESULT_STATUS_CN.get(result.status) if result else None,
    }


def _notify_session_members(db: Session, session: DefenseSession, title: str, content: str):
    """向场次全体学生与评委发站内通知(发布/改期均调用)."""
    for _a, cycle in db.execute(
        select(DefenseAssignment, GraduationCycle)
        .join(GraduationCycle, GraduationCycle.id == DefenseAssignment.cycle_id)
        .where(DefenseAssignment.session_id == session.id)
    ).all():
        sp_user = db.execute(select(StudentProfile.user_id).where(StudentProfile.id == cycle.student_id)).scalar()
        if sp_user:
            push_notification(db, user_id=sp_user, type="SYSTEM", title=title, content=content,
                              business_type="DEFENSE_SESSION", business_id=session.id)
    for ex in db.execute(select(DefenseExaminer).where(DefenseExaminer.session_id == session.id)).scalars():
        tu = db.execute(select(TeacherProfile.user_id).where(TeacherProfile.id == ex.teacher_id)).scalar()
        if tu:
            push_notification(db, user_id=tu, type="SYSTEM", title=title, content=content,
                              business_type="DEFENSE_SESSION", business_id=session.id)
