"""正式答辩服务: 排期冲突校验/发布/改期 + 评委评分 + 成绩汇总发布.

状态域分离(docs §1.2):
- 材料通过 ≠ 场次安排 ≠ 正式结论; 三者只能按各自状态机流转
- 正式成绩仅由本服务按启用评分表与有效评委评分在服务端复算, AI 分数永不进入
- 已发布场次改期必须留痕(DefenseScheduleChange)并通知学生与评委
"""
import json
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

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
from app.shared.state_machine import RESULT, SESSION, transition

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


# ============ D35 评分复算: 单轨 / 可复现 / 舍入确定 ============
# 权威精度 0.1 分; 发布分 = 精确分按 0.1 四舍五入后再取整(HALF_UP), 与 defense_result.score 列一致
SCORE_EXP = Decimal("0.1")
ITEM_EXP = Decimal("0.01")
# 缺项评委处理策略: 整份剔除(补 0 属于伪造数据; 按缺项数归一化会让各评委落在不同分母上, 加权不可比)
MISSING_ITEM_POLICY = "EXCLUDE_EXAMINER"
# 口径说明(随结果返回, 便于事后审计到底是哪一步舍入)
ROUNDING_NOTE = "score=ROUND_HALF_UP(raw,1); precise=ROUND_HALF_UP(raw,0.1)"
# 结论判定基准: False = 发布分(整数, 与库中 score 列/返回给前端的 score 完全一致);
# True = 0.1 精确分, 需要 defense_result.score 为 DECIMAL(4,1)(models+alembic 迁移, 见交付报告)
DECIDE_ON_PRECISE = False


def _to_dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def round_half_up(value, exp: Decimal = SCORE_EXP) -> Decimal | None:
    """四舍五入(ROUND_HALF_UP).

    不用内置 round(): 它是银行家舍入(round(2.5)==2, round(59.95, 1)==59.9), 会把分数
    送过或挡在 pass_line 之外, 且不同 Python 版本/浮点表示下不稳定.
    """
    dec = _to_dec(value)
    if dec is None:
        return None
    return dec.quantize(exp, rounding=ROUND_HALF_UP)


def precise_score(raw_total) -> Decimal | None:
    """精确加权总分 -> 0.1 精度权威分(59.95 -> 60.0, 59.94 -> 59.9)."""
    return round_half_up(raw_total, SCORE_EXP)


def finalize_score(raw_total) -> int | None:
    """精确加权总分 -> 发布分(整数): 对原始分单步 ROUND_HALF_UP 取整.

    不做"先 0.1 再整数"的两段舍入: 两段舍入会把 59.49 -> 59.5 -> 60(二次误差), 单步取整得 59.
    """
    dec = _to_dec(raw_total)
    if dec is None:
        return None
    return int(dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def decide(score: int | None, precise: Decimal | None, pass_line) -> str | None:
    """结论判定; 判定基准见 DECIDE_ON_PRECISE(默认按发布分, 保证展示分与结论永不矛盾)."""
    basis = precise if DECIDE_ON_PRECISE else score
    if basis is None:
        return None
    return "PASS" if _to_dec(basis) >= _to_dec(pass_line) else "FAIL"


def compute_result(db: Session, assignment: DefenseAssignment, rubric: ReviewRubric) -> dict:
    """按有效评委评分复算加权总分(只读 ExaminerScore, 不写任何成绩表).

    样本口径(全部可审计, 违反者进入 excluded 而不是按 0 分糊进去):
    1. 席位未到场: 不产生任何评分样本(出席情况是结论计算的前置条件)
    2. 评委已不在本场次评委名单: 该评分不计入
    3. 评委存在缺项(MISSING_ITEM_POLICY=EXCLUDE_EXAMINER): 整份不计入
    """
    items = _rubric_items(rubric.weights_json)
    names = [it["name"] for it in items]
    weights = {it["name"]: _to_dec(it.get("weight", 0)) for it in items}
    weight_total = sum(weights.values(), Decimal("0"))

    seat_attended = assignment.seat_status == SeatStatus.ATTENDED.value
    assigned_examiners = set(db.execute(
        select(DefenseExaminer.teacher_id).where(DefenseExaminer.session_id == assignment.session_id)
    ).scalars().all())

    rows = db.execute(
        select(ExaminerScore).where(
            ExaminerScore.assignment_id == assignment.id,
            ExaminerScore.status == ScoreStatus.SUBMITTED.value,
        ).order_by(ExaminerScore.examiner_id, ExaminerScore.version.desc())
    ).scalars().all()
    latest_by_examiner: dict[int, ExaminerScore] = {}
    for s in rows:
        latest_by_examiner.setdefault(s.examiner_id, s)

    samples: list[dict] = []
    excluded: list[dict] = []
    for examiner_id in sorted(latest_by_examiner):
        s = latest_by_examiner[examiner_id]
        try:
            data = json.loads(s.scores_json or "{}")
        except ValueError:
            data = {}
        missing = [n for n in names if data.get(n) is None]
        entry = {"score_id": s.id, "examiner_id": examiner_id, "version": s.version,
                 "scores": data, "comment": s.comment,
                 "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None}
        if not seat_attended:
            excluded.append({**entry, "reason": "SEAT_NOT_ATTENDED", "missing_items": missing})
        elif examiner_id not in assigned_examiners:
            excluded.append({**entry, "reason": "EXAMINER_NOT_ASSIGNED", "missing_items": missing})
        elif missing:
            excluded.append({**entry, "reason": "MISSING_ITEMS", "missing_items": missing})
        else:
            samples.append(entry)

    item_avg: dict[str, float | None] = {}
    item_avg_precise: dict[str, Decimal] = {}
    for it in items:
        values = [_to_dec(smp["scores"][it["name"]]) for smp in samples]
        if values:
            avg = round_half_up(sum(values, Decimal("0")) / Decimal(len(values)), ITEM_EXP)
            item_avg_precise[it["name"]] = avg
            item_avg[it["name"]] = float(avg)
        else:
            item_avg[it["name"]] = None

    raw_total = None
    if items and len(item_avg_precise) == len(items) and weight_total > 0:
        raw_total = sum(
            (item_avg_precise[it["name"]] * weights[it["name"]] for it in items), Decimal("0")
        ) / weight_total
    precise = precise_score(raw_total)
    score = finalize_score(raw_total)
    decision = decide(score, precise, rubric.pass_line)

    blockers: list[str] = []
    if not seat_attended:
        blockers.append("SEAT_NOT_ATTENDED")
    if len(samples) < rubric.min_examiners:
        blockers.append("INSUFFICIENT_EXAMINERS")
    if excluded:
        blockers.append("EXCLUDED_SAMPLES")
    if score is None:
        blockers.append("NO_SAMPLE")

    return {
        "valid_count": len(samples),
        "sample_count": len(latest_by_examiner),
        "min_required": rubric.min_examiners,
        "item_avg": item_avg,
        "item_avg_precise": {k: str(v) for k, v in item_avg_precise.items()},
        "score": score,
        "score_precise": str(precise) if precise is not None else None,
        "score_raw": str(round_half_up(raw_total, Decimal("0.0001"))) if raw_total is not None else None,
        "decision": decision,
        "decision_basis": "PRECISE" if DECIDE_ON_PRECISE else "PUBLISHED_SCORE",
        "rounding": ROUNDING_NOTE,
        "seat_attended": seat_attended,
        "excluded": excluded,
        "blockers": blockers,
        "detail": samples,
        "rubric_snapshot": json.dumps({
            "rubric_id": rubric.id, "version": rubric.version,
            "weights": items, "pass_line": rubric.pass_line, "min_examiners": rubric.min_examiners,
            "missing_item_policy": MISSING_ITEM_POLICY, "rounding": "ROUND_HALF_UP@0.1",
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


# ============ 统一状态机流程(P2-A) ============
# 场次: DRAFT -> PUBLISHED -> IN_PROGRESS -> FINISHED; 任意活态 -> CANCELLED(必须带原因)
# 席位: SCHEDULED -> ATTENDED/ABSENT (见 seat_state.mark_seat)
# 结论: PENDING -> READY -> PUBLISHED -> WITHDRAWN
INVALID_TRANSITION = 40003


def load_session(db: Session, session_id: int, for_update: bool = True) -> DefenseSession:
    s = (db.get(DefenseSession, session_id, with_for_update=True) if for_update
         else db.get(DefenseSession, session_id))
    if s is None:
        raise ResourceNotFoundException("答辩场次不存在")
    return s


def publish_session(db: Session, session_id: int, actor) -> DefenseSession:
    """发布场次: DRAFT -> PUBLISHED(唯一入口), 发布前强制复检排期冲突与评委配置."""
    s = load_session(db, session_id)
    conflicts = validate_session(db, s)
    if conflicts:
        raise ConflictException("存在排期冲突, 不能发布: " + "; ".join(c["detail"] for c in conflicts))
    examiners = db.execute(select(DefenseExaminer).where(DefenseExaminer.session_id == s.id)).scalars().all()
    if not examiners:
        raise BusinessException("请先为场次分配评委")
    if not any(e.examiner_role == "CHAIR" for e in examiners):
        raise BusinessException("场次必须指定一名评委主席(CHAIR)")
    transition(db, SESSION, s, "publish", actor)
    s.revision = (s.revision or 0) + 1
    return s


def start_session(db: Session, session_id: int, actor) -> DefenseSession:
    """开始答辩: PUBLISHED -> IN_PROGRESS; 未到开始时间不允许开始."""
    s = load_session(db, session_id)
    now = datetime.now()
    if s.starts_at and now < s.starts_at:
        raise BusinessException(
            f"未到开始时间({s.starts_at:%Y-%m-%d %H:%M}), 不能开始答辩", INVALID_TRANSITION)
    transition(db, SESSION, s, "start", actor)
    s.revision = (s.revision or 0) + 1
    return s


def finish_session(db: Session, session_id: int, actor) -> DefenseSession:
    """结束答辩: IN_PROGRESS -> FINISHED."""
    s = load_session(db, session_id)
    transition(db, SESSION, s, "finish", actor)
    s.revision = (s.revision or 0) + 1
    return s


def cancel_session(db: Session, session_id: int, actor, reason: str | None) -> DefenseSession:
    """取消场次: 任意活态 -> CANCELLED, 转移表要求必须带 comment."""
    reason = (reason or "").strip()
    if not reason:
        raise BusinessException("取消场次必须填写原因", INVALID_TRANSITION)
    s = load_session(db, session_id)
    transition(db, SESSION, s, "cancel", actor, comment=reason)
    s.revision = (s.revision or 0) + 1
    return s


logger = logging.getLogger(__name__)


def _recompute_progress(db: Session, cycle_id: int | None) -> None:
    """结论域状态变化后重算该学生进度(D74).

    `progress.compute()` 是"是否毕业"的唯一真源, 而 defense 结论是它的输入之一;
    结论发布/撤回若不触发重算, `graduation_process.completed_at` 会停在旧值,
    表现为"已毕业人数"虚增/虚减, 直到下一轮扫描才被发现。
    """
    if cycle_id is None:
        return
    cycle = db.get(GraduationCycle, cycle_id)
    if cycle is None:
        logger.warning("D74 重算跳过: cycle#%s 不存在", cycle_id)
        return
    from app.shared.progress import ProgressService

    ProgressService.recompute(db, cycle.student_id, cycle_id=cycle.id)


def load_result(db: Session, result_id: int, for_update: bool = True) -> tuple:
    """载入结论及其席位/场次/评分表(发布与撤回都取行锁, 挡住并发双击)."""
    r = (db.get(DefenseResult, result_id, with_for_update=True) if for_update
         else db.get(DefenseResult, result_id))
    if r is None:
        raise ResourceNotFoundException("结论不存在")
    a = db.get(DefenseAssignment, r.assignment_id)
    if a is None:
        raise ResourceNotFoundException("答辩席位不存在")
    s = db.get(DefenseSession, a.session_id)
    if s is None:
        raise ResourceNotFoundException("答辩场次不存在")
    cycle = db.get(GraduationCycle, a.cycle_id)
    return r, a, s, cycle, get_rubric_for(db, s, cycle)


def _apply_result_payload(r: DefenseResult, computed: dict) -> None:
    """把复算结果写入结论: score 为唯一正式成绩, 明细全量留痕(可复算)."""
    r.score = computed["score"]
    r.decision = computed["decision"]
    r.rubric_snapshot = computed["rubric_snapshot"]
    r.score_detail_json = json.dumps({
        "summary": {
            "valid_count": computed["valid_count"],
            "sample_count": computed["sample_count"],
            "min_required": computed["min_required"],
            "item_avg": computed["item_avg"],
            "item_avg_precise": computed["item_avg_precise"],
            "score": computed["score"],
            "score_precise": computed["score_precise"],
            "score_raw": computed["score_raw"],
            "decision": computed["decision"],
            "decision_basis": computed["decision_basis"],
            "rounding": computed["rounding"],
            "seat_attended": computed["seat_attended"],
            "blockers": computed["blockers"],
            "excluded": computed["excluded"],
            "missing_item_policy": MISSING_ITEM_POLICY,
        },
        "detail": computed["detail"],
    }, ensure_ascii=False, default=str)


def calculate_result(db: Session, result_id: int, actor) -> tuple[DefenseResult, dict]:
    """复算并置 READY: PENDING -> READY(internal 系统动作). 未 calculate 不能 publish."""
    r, a, s, cycle, rubric = load_result(db, result_id)
    if rubric is None:
        raise BusinessException("未配置启用的评分表")
    computed = compute_result(db, a, rubric)
    transition(db, RESULT, r, "compute", actor)
    _apply_result_payload(r, computed)
    return r, computed


def publish_result(db: Session, result_id: int, actor) -> tuple[DefenseResult, dict]:
    """发布正式结论: 同事务复检场次状态/到场/评委人数/评分完整性, 再走 READY -> PUBLISHED."""
    r, a, s, cycle, rubric = load_result(db, result_id)
    if rubric is None:
        raise BusinessException("未配置启用的评分表")
    if s.status in (SessionStatus.DRAFT.value, SessionStatus.CANCELLED.value):
        raise BusinessException(
            f"场次状态「{SESSION_STATUS_CN.get(s.status, s.status)}」不允许发布结论", INVALID_TRANSITION)
    computed = compute_result(db, a, rubric)
    if not computed["seat_attended"]:
        raise BusinessException("该学生未标记到场, 不能发布结论", INVALID_TRANSITION)
    if computed["valid_count"] < rubric.min_examiners:
        raise BusinessException(
            f"有效评委评分不足({computed['valid_count']}/{rubric.min_examiners}), 不能发布结论", INVALID_TRANSITION)
    if computed["score"] is None:
        raise BusinessException("评分项不完整, 不能发布结论", INVALID_TRANSITION)
    transition(db, RESULT, r, "publish", actor)
    _apply_result_payload(r, computed)
    # D74: 结论是"是否毕业"的判定输入之一, 落库后立刻重算进度
    # (D71 已移除 GET 里的惰性重算, 不在此触发就只能等每日 01:00 扫描)
    _recompute_progress(db, a.cycle_id)
    return r, computed


def withdraw_result(db: Session, result_id: int, actor, reason: str) -> DefenseResult:
    """撤回结论: PUBLISHED -> WITHDRAWN, 转移表要求 comment(reason) 必填."""
    reason = (reason or "").strip()
    if not reason:
        raise BusinessException("撤回结论必须填写原因", INVALID_TRANSITION)
    r = db.get(DefenseResult, result_id, with_for_update=True)
    if r is None:
        raise ResourceNotFoundException("结论不存在")
    transition(db, RESULT, r, "withdraw", actor, comment=reason)
    r.withdrawn_by = getattr(actor, "id", None)
    # D74: 撤回 PASS 后 completed_at 必须被置回 None, 否则"已毕业"虚增
    _recompute_progress(db, r.cycle_id)
    return r


def mark_result_under_review(db: Session, result: DefenseResult, reason: str | None,
                             actor=None) -> DefenseResult:
    """已发布结论转入复核(UNDER_REVIEW), 经统一状态机(PUBLISHED -> UNDER_REVIEW)."""
    transition(db, RESULT, result, "review", actor, comment=reason)
    if reason:
        result.withdraw_reason = reason
    return result
