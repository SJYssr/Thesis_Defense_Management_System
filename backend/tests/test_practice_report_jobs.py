"""Durable queue regression tests; no Redis, real clock, or provider calls."""
import importlib
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.ai import AIDefenseSession, AIDefenseTurn
from app.tasks.celery_app import celery_app
from app.tasks.practice_reports import (
    ATTEMPT_LIMIT,
    LEASE_SECONDS,
    TASK_NAME,
    _release,
    claim_one,
    process_one,
)


class PracticeReportJobsTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 2, 10)
        self.engine = create_engine("sqlite://")
        AIDefenseSession.__table__.create(self.engine)
        AIDefenseTurn.__table__.create(self.engine)
        self.session = sessionmaker(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _seed(self, session_id=1, **overrides):
        values = {"status": "FINISHED", "live_request_id": str(session_id), "report_status": "pending",
                  "finished_at": self.now}
        values.update(overrides)
        with self.session.begin() as db:
            db.add(AIDefenseSession(id=session_id, student_id=55, **values))

    def _field(self, name):
        with self.session() as db:
            return getattr(db.get(AIDefenseSession, 1), name)

    def test_only_finished_live_reports_are_claimed(self):
        self._seed(1, status="RUNNING")
        self._seed(2, live_request_id=None)
        self._seed(3, report_status="ready")
        self._seed(4, report_status="failed")
        self._seed(5)
        with self.session() as db:
            self.assertEqual(5, claim_one(db, self.now)[0])
        with self.session() as db:
            self.assertIsNone(claim_one(db, self.now))

    def test_only_one_concurrent_worker_can_claim_a_report(self):
        with TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'queue.db'}")
            try:
                AIDefenseSession.__table__.create(engine)
                factory = sessionmaker(engine)
                with factory.begin() as db:
                    db.add(AIDefenseSession(id=1, student_id=1, status="FINISHED", report_status="pending",
                                            live_request_id="unique", finished_at=self.now))

                gate = Barrier(2)

                def claim():
                    with factory() as db:
                        gate.wait(timeout=3)
                        return claim_one(db, self.now)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    runs = [pool.submit(claim) for _ in range(2)]
                    claims = [job for run in runs if (job := run.result(timeout=10)) is not None]
                self.assertEqual(1, len(claims))
                with factory() as db:
                    row = db.get(AIDefenseSession, 1)
                    self.assertEqual((1, row.report_claim), claims[0])
                    self.assertEqual(1, row.report_attempts)
            finally:
                engine.dispose()

    def test_worker_loss_recovers_job_after_lease_and_old_token_cannot_release_new_one(self):
        self._seed()
        with self.session() as db:
            _, old = claim_one(db, self.now)
        self.now += timedelta(seconds=LEASE_SECONDS)
        with self.session() as db:
            _, new = claim_one(db, self.now)
            self.assertNotEqual(old, new)
            _release(db, 1, old, self.now, failed=True)
        self.assertEqual(new, self._field("report_claim"))
        self.assertEqual(2, self._field("report_attempts"))

    def test_repeated_worker_loss_is_bounded(self):
        self._seed()
        for _ in range(ATTEMPT_LIMIT):
            with self.session() as db:
                self.assertIsNotNone(claim_one(db, self.now))
            self.now += timedelta(seconds=LEASE_SECONDS)
        with self.session() as db:
            self.assertIsNone(claim_one(db, self.now))
        self.assertEqual("failed", self._field("report_status"))
        self.assertIsNone(self._field("report_claim"))

    def test_provider_failure_uses_durable_backoff_then_becomes_failed(self):
        self._seed()
        def now():
            return self.now

        with patch("app.ai.defense.service.DefenseService") as factory:
            factory.return_value.build_live_report.side_effect = RuntimeError("provider down")
            for delay in (15, 30, None):
                self.assertTrue(process_one(self.session, now))
                self.assertFalse(process_one(self.session, now))
                if delay is not None:
                    self.assertEqual(self.now + timedelta(seconds=delay), self._field("report_lease_until"))
                    self.now += timedelta(seconds=delay)
            self.assertEqual(3, factory.return_value.build_live_report.call_count)
        self.assertEqual("failed", self._field("report_status"))
        self.assertIsNone(self._field("report_lease_until"))

    def test_restart_skips_persisted_scores_and_resumes_next_step(self):
        self._seed(report_attempts=2)
        with self.session.begin() as db:
            for turn_id, answer, score in ((1, "已处理", 80), (2, "需处理", None), (3, None, None)):
                db.add(AIDefenseTurn(id=turn_id, session_id=1, turn_no=turn_id,
                                     question="为什么这样实现？", student_answer=answer, total_score=score))

        def score(session_id, token, turn_id):
            # Another session can see the committed claim while the service performs provider work.
            self.assertEqual(token, self._field("report_claim"))
            self.assertEqual((1, 2), (session_id, turn_id))
            with self.session.begin() as db:
                db.get(AIDefenseTurn, turn_id).total_score = 75

        def finish(session_id, token):
            self.assertEqual(token, self._field("report_claim"))
            with self.session.begin() as db:
                row = db.get(AIDefenseSession, session_id)
                row.report_status = "ready"
                row.report_claim = None
                row.report_lease_until = None
                row.summary = "{}"

        with patch("app.ai.defense.service.DefenseService") as factory:
            factory.return_value.score_live_turn.side_effect = score
            factory.return_value.build_live_report.side_effect = finish
            self.assertTrue(process_one(self.session, lambda: self.now))
            self.assertEqual(0, self._field("report_attempts"))
            self.assertTrue(process_one(self.session, lambda: self.now))
            self.assertFalse(process_one(self.session, lambda: self.now))
            factory.return_value.score_live_turn.assert_called_once()
            factory.return_value.build_live_report.assert_called_once()
        self.assertEqual("ready", self._field("report_status"))

    def test_worker_registered_for_beat_and_lease_outlives_hard_limit(self):
        task = celery_app.tasks[TASK_NAME]
        self.assertEqual(TASK_NAME, celery_app.conf.beat_schedule["practice-report-queue"]["task"])
        self.assertIn("app.tasks.practice_reports", celery_app.conf.imports)
        self.assertLess(task.time_limit, LEASE_SECONDS)


class PracticeReportMigrationTest(unittest.TestCase):
    def test_upgrade_preserves_existing_rows_and_adds_scoped_request_uniqueness(self):
        engine = create_engine("sqlite://")
        path = "alembic/versions/0008_practice_report_jobs.py"
        spec = importlib.util.spec_from_file_location("migration_practice_report", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE ai_defense_session (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL)"))
            conn.execute(text("INSERT INTO ai_defense_session VALUES (1, 51), (2, 51), (3, 52), (4, 51)"))
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.upgrade()
            self.assertEqual([(None, None, 0)] * 4, conn.execute(text(
                "SELECT live_request_id, report_status, report_attempts FROM ai_defense_session ORDER BY id"
            )).all())
            conn.execute(text("UPDATE ai_defense_session SET live_request_id = 'same' WHERE id IN (1, 3)"))
            with self.assertRaises(IntegrityError):
                conn.execute(text("UPDATE ai_defense_session SET live_request_id = 'same' WHERE id = 2"))
            self.assertIn("ix_defense_report_due", [index["name"] for index in inspect(conn).get_indexes("ai_defense_session")])
            with Operations.context(context):
                migration.downgrade()
            self.assertEqual(["id", "student_id"], [col["name"] for col in inspect(conn).get_columns("ai_defense_session")])
        engine.dispose()
