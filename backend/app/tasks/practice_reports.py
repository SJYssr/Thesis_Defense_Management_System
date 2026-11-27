"""Process committed practice reports one scoring or summary step at a time.

A FINISHED live session with report_status=pending is a durable queue item.
Workers claim an item in a short transaction, so provider I/O never happens
under the claim lock. A process/broker restart leaves committed work in MySQL.
"""
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.ai import AIDefenseSession, AIDefenseTurn
from app.tasks.celery_app import celery_app

logger = logging.getLogger("app.tasks.practice_reports")
TASK_NAME = "app.tasks.practice_reports.process_one"
ATTEMPT_LIMIT = 3
HARD_LIMIT_SECONDS = 600
LEASE_SECONDS = HARD_LIMIT_SECONDS + 60


def _pending():
    row = AIDefenseSession
    return (row.status == "FINISHED", row.live_request_id.is_not(None), row.report_status == "pending")


def claim_one(db: Session, now: datetime) -> tuple[int, str] | None:
    row = AIDefenseSession
    due = or_(row.report_lease_until.is_(None), row.report_lease_until <= now)
    candidate = db.execute(select(row.id, row.report_attempts).where(*_pending(), due)
                           .order_by(row.finished_at, row.id).limit(1)).one_or_none()
    if candidate is None:
        db.rollback()
        return None

    session_id, attempts = candidate
    # Compare-and-set on one primary key; concurrent scans may select the same row,
    # but a loser cannot steal an active lease or hold locks across unrelated reports.
    current = update(row).where(row.id == session_id, *_pending(), due, row.report_attempts == attempts)
    if attempts >= ATTEMPT_LIMIT:
        db.execute(current.values(report_status="failed", report_claim=None, report_lease_until=None))
        db.commit()
        return None

    token = str(uuid4())
    claimed = db.execute(current.values(report_claim=token, report_lease_until=now + timedelta(seconds=LEASE_SECONDS),
                                        report_attempts=attempts + 1)).rowcount
    db.commit()
    return (session_id, token) if claimed == 1 else None


def _release(db: Session, session_id: int, token: str, now: datetime, *, failed: bool) -> None:
    row = AIDefenseSession
    # End any read transaction before inspecting the claim (including under MySQL REPEATABLE READ).
    db.rollback()
    owns = (row.id == session_id, *_pending(), row.report_claim == token, row.report_lease_until > now)
    attempts = db.scalar(select(row.report_attempts).where(*owns))
    if attempts is None:
        db.rollback()
        return
    if failed:
        final_failure = attempts >= ATTEMPT_LIMIT
        retry_at = None if final_failure else now + timedelta(seconds=15 * (2 ** (attempts - 1)))
        values = {"report_status": "failed" if final_failure else "pending", "report_lease_until": retry_at}
    else:
        values = {"report_attempts": 0, "report_lease_until": None}
    db.execute(update(row).where(*owns).values(report_claim=None, **values))
    db.commit()


def process_one(session_factory: Callable[[], Session], clock: Callable[[], datetime] = datetime.now) -> bool:
    from app.ai.defense.service import DefenseService

    with session_factory() as db:
        job = claim_one(db, clock())
        if job is None:
            return False
        session_id, token = job
        turn_id = None
        try:
            turn = AIDefenseTurn
            turn_id = db.scalar(select(turn.id).where(turn.session_id == session_id, turn.student_answer.is_not(None),
                                                       turn.total_score.is_(None)).order_by(turn.turn_no, turn.id).limit(1))
            db.rollback()
            service = DefenseService(db)
            if turn_id is not None:
                service.score_live_turn(session_id, token, turn_id)
            else:
                service.build_live_report(session_id, token)
        except Exception as exc:  # Failed or uncommitted work is retried, without disclosing student content.
            logger.warning("模拟答辩后台报告失败 session_id=%s turn_id=%s error=%s",
                           session_id, turn_id, type(exc).__name__)
            _release(db, session_id, token, clock(), failed=True)
        else:
            _release(db, session_id, token, clock(), failed=False)
        return True


def process_due_practice_report() -> bool:
    from app.db.mysql import SessionLocal

    return process_one(SessionLocal)


process_due_task = (celery_app.task(process_due_practice_report, name=TASK_NAME,
                                    time_limit=HARD_LIMIT_SECONDS, ignore_result=True, max_retries=0)
                    if celery_app is not None else None)
