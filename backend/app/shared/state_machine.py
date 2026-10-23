"""统一状态机内核(docs/refactor-plan.md §3/§4).

本模块是"实体 - 动作 - 源状态"转移表的唯一真源:

- 任何模块都不得再自行 if/else 判断"某状态能否做某动作", 也不得直接赋值 entity.status;
- 非法转移统一抛 BusinessException(code=40003, message="当前状态 X 不允许动作 Y");
- 权限不足抛 PermissionDeniedException;
- 副作用(版本号 +1、submitted_at/reviewed_at、published_at、留痕)一律在 transition 内完成,
  业务层只负责先把业务字段写进对象, 再交给 transition;
- 状态列更新走"条件更新 + rowcount 校验", 并发下第二次同样的动作会失败而不是静默重复(A2)。

注意: SUBMITTED 保留为唯一"待审核"态, 不引入 UNDER_REVIEW(§4 向后兼容段)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.models.enums import BusinessType, ProcessStage

log = logging.getLogger("app.state_machine")

# 非法转移统一业务码(§4)
INVALID_TRANSITION = 40003

# ---------- 实体名(§4 Entity Literal) ----------
TOPIC = "TOPIC"
SELECTION = "SELECTION"
OPENING = "OPENING"
MIDTERM = "MIDTERM"
FINAL = "FINAL"
MATERIAL = "MATERIAL"
SESSION = "SESSION"
SEAT = "SEAT"
RESULT = "RESULT"
ARCHIVE = "ARCHIVE"
CYCLE = "CYCLE"
# 公告与阶段任务此前不在状态机内, 状态由路由直写(D53/D59); 纳入后与其它业务同源
ANNOUNCEMENT = "ANNOUNCEMENT"
STAGE_TASK = "STAGE_TASK"

STAGE_ENTITIES: tuple[str, ...] = (OPENING, MIDTERM, FINAL, MATERIAL)

_ENTITY_OF_STAGE: dict[str, str] = {
    ProcessStage.OPENING.value: OPENING,
    ProcessStage.MIDTERM.value: MIDTERM,
    ProcessStage.FINAL.value: FINAL,
    ProcessStage.MATERIAL.value: MATERIAL,
}

BUSINESS_TYPES: dict[str, str] = {
    SELECTION: BusinessType.TOPIC_SELECTION.value,
    OPENING: BusinessType.OPENING_REPORT.value,
    MIDTERM: BusinessType.MIDTERM_REPORT.value,
    FINAL: BusinessType.FINAL_DEFENSE.value,
    MATERIAL: BusinessType.GRADUATION_MATERIAL.value,
    ARCHIVE: BusinessType.ARCHIVE_PACKAGE.value,
}

# ---------- 角色集合 ----------
_STUDENT = frozenset({"STUDENT"})
_REVIEWER = frozenset({"TEACHER", "ADMIN"})
_ADMIN = frozenset({"ADMIN"})
# 归档提交的产品入口是管理员代办(见 modules/archive/router.py), 故两者都放行; 学生自助入口出现时无需再改表
_STUDENT_OR_ADMIN = frozenset({"STUDENT", "ADMIN"})
_SYSTEM: frozenset[str] = frozenset()

# 会话"进行中"态: 以模型枚举 SessionStatus.IN_PROGRESS 为唯一真源
# (app/models/enums.py:111 与 frontend/src/views/admin/DefenseSchedule.vue:19 都只认 IN_PROGRESS)。
# ONGOING 仅为历史数据兼容留在来源集合里, 永不再作为目标值写出。
SESSION_IN_PROGRESS = "IN_PROGRESS"
SESSION_ONGOING = frozenset({SESSION_IN_PROGRESS, "ONGOING"})


@dataclass(frozen=True)
class Rule:
    """一条转移规则: 实体 + 动作 → 允许源状态 / 目标状态 / 权限 / 附加守卫."""

    entity: str
    action: str
    sources: frozenset[str]
    target: str
    roles: frozenset[str] = _SYSTEM
    require_comment: bool = False
    internal: bool = False
    guard: Callable[[Session, Any, dict], None] | None = None


Guard = Callable[[Session, Any, dict], None]


# ---------- 附加守卫 ----------

def _guard_archive_submit(db: Session, obj: Any, payload: dict) -> None:
    """ARCHIVE submit 前置: 该 cycle 的正式结论已发布(§4)."""
    from app.models.defense import DefenseResult

    published = db.execute(
        select(DefenseResult.id).where(
            DefenseResult.cycle_id == getattr(obj, "cycle_id", None),
            DefenseResult.status == "PUBLISHED",
        )
    ).first()
    if published is None:
        raise BusinessException("答辩结论尚未发布, 不能提交归档", INVALID_TRANSITION)


def _guard_cycle_complete(db: Session, obj: Any, payload: dict) -> None:
    """CYCLE complete 前置: 归档已批准且正式结论 PASS(§4)."""
    from app.models.defense import ArchivePackage, DefenseResult

    cycle_id = getattr(obj, "id", None)
    archive_status = db.execute(
        select(ArchivePackage.status).where(ArchivePackage.cycle_id == cycle_id)
    ).scalar_one_or_none()
    if archive_status != "ARCHIVED":
        raise BusinessException("归档材料尚未批准, 不能判定毕业", INVALID_TRANSITION)
    decision = db.execute(
        select(DefenseResult.decision).where(
            DefenseResult.cycle_id == cycle_id,
            DefenseResult.status == "PUBLISHED",
        )
    ).scalars().first()
    if decision != "PASS":
        raise BusinessException("答辩结论不为通过, 不能判定毕业", INVALID_TRANSITION)


_STAGE_SAVE_FROM = frozenset({"NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"})
# 未落库新建对象状态为 None/NOT_SUBMITTED, 故 submit 也接受 NOT_SUBMITTED(与 engine 可编辑集对齐)
_STAGE_SUBMIT_FROM = frozenset({"NOT_SUBMITTED", "DRAFT", "REJECTED", "RESUBMIT_REQUIRED"})

# ---------- 转移表(严格对齐 docs/refactor-plan.md §4) ----------
_RULES: tuple[Rule, ...] = (
    # --- 题目(教师侧, 决策权在指导教师/管理员) ---
    Rule(TOPIC, "publish", frozenset({"DRAFT"}), "PUBLISHED", _REVIEWER),
    Rule(TOPIC, "close", frozenset({"PUBLISHED"}), "CLOSED", _REVIEWER),
    # --- 选题 ---
    Rule(SELECTION, "submit", frozenset({"PENDING"}), "PENDING", _STUDENT),
    Rule(SELECTION, "approve", frozenset({"PENDING"}), "APPROVED", _REVIEWER),
    Rule(SELECTION, "reject", frozenset({"PENDING"}), "REJECTED", _REVIEWER, require_comment=True),
    Rule(SELECTION, "cancel", frozenset({"PENDING"}), "CANCELLED", _STUDENT),
)

for _entity in STAGE_ENTITIES:
    # --- 开题/中期/终期/毕业材料(生命周期完全一致) ---
    _rules_list = [
        Rule(_entity, "save", _STAGE_SAVE_FROM, "DRAFT", _STUDENT),
        Rule(_entity, "submit", _STAGE_SUBMIT_FROM, "SUBMITTED", _STUDENT),
        Rule(_entity, "withdraw", frozenset({"SUBMITTED"}), "DRAFT", _STUDENT),
        Rule(_entity, "approve", frozenset({"SUBMITTED"}), "APPROVED", _REVIEWER),
        Rule(_entity, "reject", frozenset({"SUBMITTED"}), "REJECTED", _REVIEWER, require_comment=True),
        Rule(_entity, "request_revision", frozenset({"SUBMITTED"}), "RESUBMIT_REQUIRED", _REVIEWER,
             require_comment=True),
    ]
    _RULES = _RULES + tuple(_rules_list)

_RULES = _RULES + (
    # --- 答辩场次 ---
    Rule(SESSION, "publish", frozenset({"DRAFT"}), "PUBLISHED", _ADMIN),
    Rule(SESSION, "start", frozenset({"PUBLISHED"}), SESSION_IN_PROGRESS, _ADMIN),
    Rule(SESSION, "finish", SESSION_ONGOING, "FINISHED", _ADMIN),
    Rule(SESSION, "cancel", frozenset({"DRAFT", "PUBLISHED"}) | SESSION_ONGOING, "CANCELLED", _ADMIN,
         require_comment=True),
    # --- 席位 ---
    Rule(SEAT, "attend", frozenset({"SCHEDULED"}), "ATTENDED", _REVIEWER),
    Rule(SEAT, "absent", frozenset({"SCHEDULED"}), "ABSENT", _REVIEWER),
    # 延期/补答辩/撤销延期: 缺任一条都会让既有前端入口失效——
    # frontend/src/views/admin/DefenseSchedule.vue:227 的「延期」按钮传 POSTPONED,
    # frontend/src/views/student/MyDefense.vue:10 展示 POSTPONED/RETAKE,
    # 二者都依赖 SeatStatus(POSTPONED/RETAKE) 可达。
    Rule(SEAT, "postpone", frozenset({"SCHEDULED", "ATTENDED"}), "POSTPONED", _REVIEWER),
    Rule(SEAT, "retake", frozenset({"ABSENT", "POSTPONED"}), "RETAKE", _REVIEWER),
    Rule(SEAT, "reset", frozenset({"POSTPONED", "RETAKE"}), "SCHEDULED", _ADMIN),
    # --- 正式结论 ---
    Rule(RESULT, "compute", frozenset({"PENDING"}), "READY", internal=True),
    Rule(RESULT, "publish", frozenset({"READY", "UNDER_REVIEW"}), "PUBLISHED", _ADMIN),
    Rule(RESULT, "withdraw", frozenset({"PUBLISHED"}), "WITHDRAWN", _ADMIN, require_comment=True),
    # 评分被重开复核时, 已发布结论必须转入复核中, 否则页面仍显示已定档的旧分
    Rule(RESULT, "review", frozenset({"PUBLISHED"}), "UNDER_REVIEW", _ADMIN),
    # --- 归档(状态真源是 ArchiveStatus: PENDING/ARCHIVED/REVOKED) ---
    # 提交即进入 PENDING; 已 REVOKED 的归档可重新提交并复用同一条记录(D28)
    Rule(ARCHIVE, "submit", frozenset({"PENDING", "REVOKED"}), "PENDING", _STUDENT_OR_ADMIN,
         guard=_guard_archive_submit),
    Rule(ARCHIVE, "approve", frozenset({"PENDING"}), "ARCHIVED", _ADMIN),
    Rule(ARCHIVE, "revoke", frozenset({"PENDING", "ARCHIVED"}), "REVOKED", _ADMIN,
         require_comment=True),
    # --- 流程实例 ---
    Rule(CYCLE, "complete", frozenset({"ACTIVE"}), "COMPLETED", _ADMIN, guard=_guard_cycle_complete),
    # --- 公告(状态真源 Announcement.status: DRAFT/PUBLISHED/OFFLINE) ---
    # 下线后重新上线是公告的常规操作, 不给这条就只能新建一条(同一内容重复入库)
    Rule(ANNOUNCEMENT, "publish", frozenset({"DRAFT", "OFFLINE"}), "PUBLISHED", _ADMIN),
    Rule(ANNOUNCEMENT, "offline", frozenset({"PUBLISHED"}), "OFFLINE", _ADMIN),
    # --- 阶段任务(状态真源 StageTask.status: DRAFT/PUBLISHED/CLOSED) ---
    Rule(STAGE_TASK, "publish", frozenset({"DRAFT"}), "PUBLISHED", _REVIEWER),
    Rule(STAGE_TASK, "close", frozenset({"PUBLISHED"}), "CLOSED", _REVIEWER),
)

TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {}
TARGETS: dict[str, dict[str, str]] = {}
RULES: dict[tuple[str, str], Rule] = {}
for _rule in _RULES:
    TRANSITIONS.setdefault(_rule.entity, {})[_rule.action] = _rule.sources
    TARGETS.setdefault(_rule.entity, {})[_rule.action] = _rule.target
    RULES[(_rule.entity, _rule.action)] = _rule

# 面向业务层的动作(排除只有服务端内部才能触发的 compute)
PLAN_RULES: tuple[Rule, ...] = tuple(r for r in _RULES if not r.internal)


# ---------- 查询辅助 ----------

def entity_for_stage(stage: Any) -> str:
    """ProcessStage(或字符串) → 状态机实体名."""
    value = stage.value if hasattr(stage, "value") else str(stage)
    try:
        return _ENTITY_OF_STAGE[value]
    except KeyError:
        raise BusinessException(f"未知阶段 {value}, 无法映射状态机实体", INVALID_TRANSITION) from None


def rule_for(entity: str, action: str) -> Rule:
    rule = RULES.get((entity, action))
    if rule is None:
        raise BusinessException(f"未知动作 {entity}.{action}", INVALID_TRANSITION)
    return rule


def target_of(entity: str, action: str) -> str:
    return rule_for(entity, action).target


def allowed_actions(entity: str, status: str | None) -> list[str]:
    """当前状态下可执行的(人类可触发的)动作列表, 供前端/校验复用."""
    actions = [
        r.action for r in PLAN_RULES
        if r.entity == entity and status in r.sources
    ]
    return sorted(actions)


def can(entity: str, action: str, obj: Any, actor: Any) -> bool:
    """不改状态地预判能否执行; 供 router/UI 展示按钮与只读视图使用."""
    rule = RULES.get((entity, action))
    if rule is None or obj is None:
        return False
    if getattr(obj, "status", None) not in rule.sources:
        return False
    try:
        _assert_permission(rule, obj, actor)
    except PermissionDeniedException:
        return False
    return True


# ---------- 权限 ----------

def _assert_permission(rule: Rule, obj: Any, actor: Any) -> None:
    if not rule.roles:  # 系统内部动作
        return
    role = getattr(actor, "role", None)
    if role is None:
        raise PermissionDeniedException("缺少操作人身份, 无法执行该动作")
    if role not in rule.roles:
        raise PermissionDeniedException(f"当前身份「{role}」无权执行动作「{rule.action}」")
    if role == "ADMIN":
        return
    actor_profile = getattr(actor, "profile_id", None)
    if actor_profile is None:
        return
    if role == "STUDENT":
        owner = getattr(obj, "student_id", None)
        if owner is not None and owner != actor_profile:
            raise PermissionDeniedException("只能操作本人的业务记录")
    elif role == "TEACHER":
        # 归属列名不统一: 审核类记录用 teacher_id, 教师自建的任务类记录用 created_by。
        # 只看 teacher_id 会让无该列的实体(如 StageTask)在状态机层完全不做归属校验,
        # 一旦有人绕过 router 直接调 transition 就失去保护。
        owner = getattr(obj, "teacher_id", None)
        if owner is not None:
            if owner != actor_profile:
                raise PermissionDeniedException("只能审核本人指导学生的业务记录")
        else:
            # created_by 存的是 sys_user.id, 与 profile_id 不同源, 不能混用
            creator = getattr(obj, "created_by", None)
            actor_id = getattr(actor, "id", None)
            if creator is not None and actor_id is not None and creator != actor_id:
                raise PermissionDeniedException("只能操作本人创建的业务记录")


# ---------- 唯一入口 ----------

def transition(db: Session, entity: str, obj: Any, action: str, actor: Any, *,
               comment: str | None = None, **payload) -> Any:
    """统一状态转移入口: 校验 → 守卫 → 条件更新 → 副作用(含留痕).

    payload 原样交给附加守卫(例如需要 cycle_id 时), 不写入对象本身。
    """
    if obj is None:
        raise BusinessException(f"{entity} 记录不存在, 无法执行动作「{action}」", INVALID_TRANSITION)
    rule = rule_for(entity, action)
    current = getattr(obj, "status", None)
    if current not in rule.sources:
        raise BusinessException(f"当前状态 {current} 不允许动作 {action}", INVALID_TRANSITION)
    _assert_permission(rule, obj, actor)
    text = (comment or "").strip()
    if rule.require_comment and not text:
        raise BusinessException(f"动作「{action}」必须填写意见或原因", INVALID_TRANSITION)
    if rule.guard is not None:
        rule.guard(db, obj, payload)

    _apply_status(db, obj, rule, current)
    _apply_effects(db, rule, obj, actor, text or None, current)
    return obj


def _pk_predicate(obj: Any):
    mapper = sa_inspect(obj.__class__)
    columns = list(mapper.primary_key)
    if len(columns) != 1:  # 复合主键不在本批范围
        raise BusinessException(f"{obj.__class__.__name__} 复合主键不支持条件更新", INVALID_TRANSITION)
    column = columns[0]
    return getattr(obj.__class__, column.key) == getattr(obj, column.key)


def _apply_status(db: Session, obj: Any, rule: Rule, current: str | None) -> None:
    """条件更新: UPDATE ... WHERE pk=? AND status=<current>, rowcount 必须为 1(A2)."""
    if rule.target == current:  # 同态动作(选题 submit)不产生状态列更新
        return
    status_column = getattr(obj.__class__, "status")
    result = db.execute(
        update(obj.__class__)
        .where(_pk_predicate(obj), status_column == current)
        .values(status=rule.target)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        # 并发下另一个审批人已经处理过同一行
        raise BusinessException(
            f"当前状态 {current} 不允许动作 {rule.action}", INVALID_TRANSITION)
    obj.status = rule.target


_RECORD_ACTIONS = {
    "submit": "SUBMIT",
    "approve": "APPROVE",
    "reject": "REJECT",
    "request_revision": "RETURN",
    # 撤回/复核同样改变对外可见状态, 必须留痕(ReviewRecord.action 是自由文本 String(16))
    "revoke": "REVOKE",
    "review": "REVIEW",
}


def _apply_effects(db: Session, rule: Rule, obj: Any, actor: Any,
                   comment: str | None, before: str | None) -> None:
    """统一副作用: 版本号/时间戳/审核意见/留痕(§4)."""
    ts = datetime.now()
    if rule.action == "submit" and hasattr(obj, "current_version"):
        obj.current_version = (obj.current_version or 0) + 1
    if rule.action == "submit" and hasattr(obj, "submitted_at"):
        obj.submitted_at = ts
    if rule.action in ("approve", "reject", "request_revision", "revoke") and hasattr(obj, "reviewed_at"):
        obj.reviewed_at = ts
    if rule.action == "approve" and hasattr(obj, "archived_at"):
        obj.archived_at = ts
    if rule.action == "revoke" and hasattr(obj, "archived_at"):
        # 撤回即不再算归档, 残留 archived_at 会让归档率与时间轴谎报
        obj.archived_at = None
    if rule.action == "publish":
        # 各表发布时间列名不统一: defense 用 published_at, topic/announcement 用 publish_time
        if hasattr(obj, "published_at"):
            obj.published_at = ts
        if hasattr(obj, "publish_time"):
            obj.publish_time = ts
        actor_id = getattr(actor, "id", None)
        if hasattr(obj, "published_by"):
            obj.published_by = actor_id
        if hasattr(obj, "publisher_id") and getattr(obj, "publisher_id", None) is None:
            obj.publisher_id = actor_id
    if rule.action == "withdraw":
        if hasattr(obj, "withdrawn_at"):
            obj.withdrawn_at = ts
        if comment and hasattr(obj, "withdraw_reason"):
            obj.withdraw_reason = comment
    if hasattr(obj, "review_comment"):
        if rule.action == "submit":
            obj.review_comment = None
        elif comment:
            obj.review_comment = comment
    _write_review_record(db, rule, obj, actor, comment, before)


def _resolve_student_id(db: Session, obj: Any) -> int | None:
    """留痕需要 student_id(NOT NULL); ArchivePackage 等表没有该列, 用 cycle 反查."""
    direct = getattr(obj, "student_id", None)
    if direct is not None:
        return direct
    cycle_id = getattr(obj, "cycle_id", None)
    if cycle_id is None:
        return None
    from app.models.business import GraduationCycle

    return db.execute(
        select(GraduationCycle.student_id).where(GraduationCycle.id == cycle_id)
    ).scalar()


def _write_review_record(db: Session, rule: Rule, obj: Any, actor: Any,
                         comment: str | None, before: str | None) -> None:
    business_type = BUSINESS_TYPES.get(rule.entity)
    action = _RECORD_ACTIONS.get(rule.action)
    if business_type is None or action is None:
        return
    business_id = getattr(obj, "id", None)
    if business_id is None:  # 尚未落库的对象交给调用方 flush 后再留痕
        return
    if rule.action == "submit" and comment is None and hasattr(obj, "current_version"):
        comment = f"提交第 {obj.current_version} 版"
    student_id = _resolve_student_id(db, obj)
    if student_id is None:
        # ReviewRecord.student_id 为 NOT NULL; 面向全体/无学生维度的实体(如 StageTask)
        # 应走 operation_log 审计, 不在此处硬造学生归属
        return
    from app.shared.events import add_review_record

    add_review_record(
        db,
        business_type=business_type,
        business_id=business_id,
        student_id=student_id,
        action=action,
        before_status=before,
        after_status=rule.target,
        reviewer_id=getattr(actor, "id", None),
        reviewer_role=getattr(actor, "role", None),
        comment=comment,
    )


def describe() -> list[dict]:
    """转移表快照(供文档/测试断言)."""
    return [
        {
            "entity": r.entity,
            "action": r.action,
            "sources": sorted(r.sources),
            "target": r.target,
            "roles": sorted(r.roles),
            "require_comment": r.require_comment,
            "internal": r.internal,
        }
        for r in _RULES
    ]
