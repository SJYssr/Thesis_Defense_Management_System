"""席位状态机适配层: DefenseAssignment 的状态列名是 seat_status, 而统一状态机
(app/shared/state_machine.py) 通过映射类的 status 属性读写状态.

本模块在导入时给 DefenseAssignment 注册 synonym 别名 status -> seat_status, 使
transition(db, SEAT, assignment, "attend"|"absent", actor) 的条件更新
(UPDATE ... WHERE pk=? AND status=<当前值>, rowcount 必须为 1) 直接作用于
seat_status 列. 这样业务层既不必复制转移逻辑, 也不必直接给 seat_status 赋值.

为什么不改 model: P2-A 的边界禁止修改 app/models/** 与 app/shared/**. 建议 P2-B 在
app/models/defense.py 里正式声明 status = synonym("seat_status")(或给状态机增加
status_field 映射参数), 之后删除本模块的运行时注册即可, 业务代码无需改动.
"""
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, synonym

from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.models.defense import DefenseAssignment, DefenseSession
from app.models.enums import SessionStatus
from app.shared.state_machine import SEAT, transition

INVALID_TRANSITION = 40003

# 席位目标态 -> 状态机动作(转移表 SEAT 的五个动作, 见 shared/state_machine.py)
SEAT_ACTION_BY_TARGET: dict[str, str] = {
    "ATTENDED": "attend",
    "ABSENT": "absent",
    "POSTPONED": "postpone",
    "RETAKE": "retake",
    "SCHEDULED": "reset",
}
# 只有已发布/进行中/已结束的场次才允许签到(草稿与已取消场次不产生出席记录)
SEAT_LIVE_SESSION_STATES = (
    SessionStatus.PUBLISHED.value,
    SessionStatus.IN_PROGRESS.value,
    SessionStatus.FINISHED.value,
)


def install_seat_status_alias() -> None:
    """把 status 注册为 seat_status 的 synonym(幂等)."""
    mapper = sa_inspect(DefenseAssignment)
    if "status" in mapper.attrs:
        return
    mapper.add_property("status", synonym("seat_status"))


install_seat_status_alias()


def mark_seat(db: Session, assignment: DefenseAssignment, target_status: str, actor,
              remark: str | None = None) -> DefenseAssignment:
    """席位状态迁移: SCHEDULED -> ATTENDED/ABSENT, 全部经统一状态机.

    出席情况是结论计算的前置条件(compute_result 只统计 ATTENDED 席位的评分).
    """
    action = SEAT_ACTION_BY_TARGET.get(target_status)
    if action is None:
        raise BusinessException(
            f"转移表未定义席位动作「{target_status}」, 待扩展 SEAT.{target_status.lower()} 后才能使用",
            INVALID_TRANSITION,
        )
    session = db.get(DefenseSession, assignment.session_id)
    if session is None:
        raise ResourceNotFoundException("场次不存在")
    if session.status not in SEAT_LIVE_SESSION_STATES:
        raise BusinessException(
            f"场次状态「{session.status}」不允许登记出席情况, 请先发布场次", INVALID_TRANSITION)
    if action == "absent" and session.status != SessionStatus.FINISHED.value:
        raise BusinessException("场次结束后才能标记学生缺席", INVALID_TRANSITION)
    transition(db, SEAT, assignment, action, actor)
    if remark is not None:
        assignment.remark = remark
    return assignment
