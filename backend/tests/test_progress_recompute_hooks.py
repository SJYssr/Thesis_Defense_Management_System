"""D74 回归: 结论域 / 归档域状态变化必须立刻触发 progress 重算.

背景: `shared/progress.py` 的 recompute 逻辑本身正确, 但 D71 拿掉了 GET 里的
惰性重算之后, 若发布/撤回结论、撤回归档不主动触发, `graduation_process.completed_at`
会停在旧值, 表现为后台"已毕业人数"虚增/虚减, 最长滞后到下一轮每日扫描。

本文件只验证"钩子存在且传对参数"(用 patch 隔离状态机与评分复算),
端到端的数值正确性由 test_progress.py / test_dashboard_consistency.py 覆盖。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle
from app.models.defense import ArchivePackage, DefenseResult
from app.modules.archive.router import revoke_package
from app.modules.defense.service import _recompute_progress, publish_result, withdraw_result


class RecomputeHookTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.cycle = GraduationCycle(
            academic_year="2025-2026", student_id=1, topic_id=None, teacher_id=None, status="ACTIVE"
        )
        self.db.add(self.cycle)
        self.db.commit()
        self.actor = SimpleNamespace(id=9, role="ADMIN", profile_id=None)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_withdraw_result_recomputes_progress(self):
        r = DefenseResult(
            assignment_id=1, cycle_id=self.cycle.id, stage="FINAL",
            score=80, decision="PASS", status="PUBLISHED",
        )
        self.db.add(r)
        self.db.commit()
        with patch("app.modules.defense.service.transition"),                 patch("app.shared.progress.ProgressService") as svc:
            withdraw_result(self.db, r.id, self.actor, "评委打分有误")
        svc.recompute.assert_called_once_with(self.db, 1, cycle_id=self.cycle.id)

    def test_publish_result_recomputes_progress(self):
        r = DefenseResult(
            assignment_id=2, cycle_id=self.cycle.id, stage="FINAL", status="READY",
        )
        self.db.add(r)
        self.db.commit()
        assignment = SimpleNamespace(cycle_id=self.cycle.id)
        session = SimpleNamespace(status="IN_PROGRESS")
        rubric = SimpleNamespace(min_examiners=1)
        computed = {"seat_attended": True, "valid_count": 3, "score": 88, "decision": "PASS"}
        with patch("app.modules.defense.service.load_result",
                   return_value=(r, assignment, session, self.cycle, rubric)),                 patch("app.modules.defense.service.compute_result", return_value=computed),                 patch("app.modules.defense.service.transition"),                 patch("app.modules.defense.service._apply_result_payload"),                 patch("app.shared.progress.ProgressService") as svc:
            publish_result(self.db, r.id, self.actor)
        svc.recompute.assert_called_once_with(self.db, 1, cycle_id=self.cycle.id)

    def test_revoke_package_recomputes_progress(self):
        pkg = ArchivePackage(
            cycle_id=self.cycle.id, status="ARCHIVED", archived_at=datetime.now(),
            title="论文", created_by=1,
        )
        self.db.add(pkg)
        self.db.commit()
        with patch("app.modules.archive.router.archive_transition"),                 patch("app.shared.progress.ProgressService") as svc:
            revoke_package(pkg.id, user=self.actor, db=self.db)
        svc.recompute.assert_called_once_with(self.db, 1, cycle_id=self.cycle.id)

    def test_missing_cycle_is_skipped_not_crashing(self):
        with patch("app.shared.progress.ProgressService") as svc:
            _recompute_progress(self.db, 999999)
        svc.recompute.assert_not_called()

    def test_none_cycle_is_skipped(self):
        with patch("app.shared.progress.ProgressService") as svc:
            _recompute_progress(self.db, None)
        svc.recompute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
