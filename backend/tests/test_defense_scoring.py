"""P2-A D35 评分口径测试: 单轨/可复现/舍入边界正确(不依赖银行家舍入).

覆盖:
- round_half_up / precise_score / finalize_score 边界(59.95 / 59.94 / 恰好 pass_line)
- 缺项评委整份剔除(EXCLUDE_EXAMINER), 且剔除理由可审计
- 同一份 rubric 数据重复复算结果完全一致(确定性)
- 缺席学生不产生任何样本(出席是结论计算前置条件)
- 评委不在场次名单时评分不计入
"""
import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, GraduationSchedule, GraduationTopic
from app.models.defense import (
    DefenseAssignment,
    DefenseExaminer,
    DefenseSession,
    ExaminerScore,
    ReviewRubric,
)
from app.models.enums import ScoreStatus
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.defense import service
from app.modules.defense.service import compute_result, finalize_score, precise_score, round_half_up


def _base(db: Session, pass_line: int = 60, min_examiners: int = 2,
          items: list[dict] | None = None) -> ReviewRubric:
    db.add(GraduationSchedule(id=1, academic_year="2026-2027", is_active=1))
    db.add_all([
        SysUser(id=11, username="t1", password_hash="x", real_name="评委甲", role="TEACHER"),
        SysUser(id=12, username="t2", password_hash="x", real_name="评委乙", role="TEACHER"),
        SysUser(id=13, username="t3", password_hash="x", real_name="评委丙", role="TEACHER"),
        SysUser(id=21, username="20260001", password_hash="x", real_name="学生A", role="STUDENT"),
    ])
    db.flush()
    db.add_all([
        TeacherProfile(id=1, user_id=11, teacher_no="T1"),
        TeacherProfile(id=2, user_id=12, teacher_no="T2"),
        TeacherProfile(id=3, user_id=13, teacher_no="T3"),
        StudentProfile(id=1, user_id=21, student_no="20260001"),
    ])
    db.add(GraduationTopic(id=1, teacher_id=1, title="题目X", status="PUBLISHED"))
    db.flush()
    db.add(GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1, teacher_id=3))
    rubric = ReviewRubric(
        id=1, academic_year="2026-2027", stage="FINAL", version=1,
        min_examiners=min_examiners, pass_line=pass_line,
        weights_json=json.dumps(items or [{"name": "报告质量", "weight": 50}, {"name": "答辩表现", "weight": 50}],
                                ensure_ascii=False),
        enabled=1)
    db.add(rubric)
    db.flush()
    return rubric


def _assignment(db: Session, seat_status: str = "ATTENDED") -> DefenseAssignment:
    start = datetime(2026, 10, 10, 9, 0)
    db.add(DefenseSession(id=1, academic_year="2026-2027", stage="FINAL", starts_at=start,
                          ends_at=start + timedelta(hours=2), location="A101", capacity=5,
                          status="PUBLISHED"))
    db.flush()
    db.add_all([
        DefenseExaminer(session_id=1, teacher_id=1, examiner_role="CHAIR"),
        DefenseExaminer(session_id=1, teacher_id=2, examiner_role="EXAMINER"),
    ])
    a = DefenseAssignment(id=1, session_id=1, cycle_id=1, seat_status=seat_status)
    db.add(a)
    db.flush()
    return a


def _submit(db: Session, examiner_id: int, scores: dict, version: int = 1,
            status: str = ScoreStatus.SUBMITTED.value) -> ExaminerScore:
    row = ExaminerScore(assignment_id=1, examiner_id=examiner_id, version=version, status=status,
                        scores_json=json.dumps(scores, ensure_ascii=False), submitted_at=datetime.now())
    db.add(row)
    db.flush()
    return row


class RoundingPrimitiveTest(unittest.TestCase):
    """D35-b: 舍入必须是 ROUND_HALF_UP, 不能是 Python 内置 round() 的银行家舍入."""

    def test_builtin_round_is_bankers(self):
        self.assertEqual(round(2.5), 2)      # 银行家舍入: .5 向偶数取整
        self.assertEqual(round(0.5), 0)
        self.assertEqual(round_half_up(2.5, Decimal("1")), Decimal("3"))   # 四舍五入

    def test_boundary_59_95_rounds_up_to_60(self):
        self.assertEqual(round_half_up(59.95, Decimal("0.1")), Decimal("60.0"))
        self.assertEqual(precise_score(59.95), Decimal("60.0"))

    def test_boundary_59_94_rounds_down_to_59_9(self):
        self.assertEqual(round_half_up(59.94, Decimal("0.1")), Decimal("59.9"))
        self.assertEqual(precise_score(59.94), Decimal("59.9"))

    def test_finalize_score_half_up(self):
        self.assertEqual(finalize_score(59.95), 60)
        self.assertEqual(finalize_score(59.94), 60)   # 59.9 -> 60, 见报告「取整口径」
        self.assertEqual(finalize_score(59.4), 59)
        # 单步取整: 59.49 不得因"先 0.1 再整数"的两段舍入变成 60
        self.assertEqual(finalize_score(59.49), 59)
        self.assertEqual(precise_score(59.49), Decimal("59.5"))
        self.assertEqual(finalize_score(67.5), 68)    # 银行家舍入会得 68, 但 2.5 类边界会偏
        self.assertIsNone(finalize_score(None))

    def test_exact_pass_line(self):
        self.assertEqual(service.decide(60, Decimal("60.0"), 60), "PASS")
        self.assertEqual(service.decide(60, Decimal("59.94"), 60), "PASS")   # 默认按发布分判定
        self.assertEqual(service.decide(59, Decimal("59.9"), 60), "FAIL")

    def test_decide_on_precise_switch(self):
        """DECIDE_ON_PRECISE=True 时按 0.1 精确分判定(需 score 列升 DECIMAL(4,1))."""
        old = service.DECIDE_ON_PRECISE
        service.DECIDE_ON_PRECISE = True
        try:
            self.assertEqual(service.decide(60, Decimal("59.94"), 60), "FAIL")
            self.assertEqual(service.decide(60, Decimal("60.0"), 60), "PASS")
        finally:
            service.DECIDE_ON_PRECISE = old


class ComputeSamplePolicyTest(unittest.TestCase):
    """D35-a: 缺项评委整份剔除 / 缺席学生零样本 / 非名单评委不计入."""

    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.rubric = _base(self.db)
        self.assignment = _assignment(self.db)

    def tearDown(self):
        self.db.close()

    def test_boundary_59_95_crosses_pass_line_by_half_up(self):
        """精确总分 59.95: HALF_UP 后 60.0, 发布分 60 -> PASS(内置 round 会停在 59.9)."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 59.9, "答辩表现": 60.0})
        _submit(self.db, examiner_id=2, scores={"报告质量": 59.9, "答辩表现": 60.0})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["score_raw"], "59.9500")
        self.assertEqual(computed["score_precise"], "60.0")
        self.assertEqual(computed["score"], 60)
        self.assertEqual(computed["decision"], "PASS")
        self.assertEqual(computed["valid_count"], 2)
        self.assertEqual(computed["rounding"], service.ROUNDING_NOTE)
        self.assertEqual(computed["decision_basis"], "PUBLISHED_SCORE")

    def test_boundary_59_94_stays_below(self):
        """精确总分 59.94: 0.1 精度是 59.9(不是 60.0), 说明舍入方向正确."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 59.88, "答辩表现": 60.0})
        _submit(self.db, examiner_id=2, scores={"报告质量": 59.88, "答辩表现": 60.0})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["score_raw"], "59.9400")
        self.assertEqual(computed["score_precise"], "59.9")
        old = service.DECIDE_ON_PRECISE
        service.DECIDE_ON_PRECISE = True
        try:
            precise_decision = compute_result(self.db, self.assignment, self.rubric)["decision"]
        finally:
            service.DECIDE_ON_PRECISE = old
        self.assertEqual(precise_decision, "FAIL")
        self.assertEqual(computed["decision"], "PASS")   # 默认按发布分(整数 60)判定
        self.assertEqual(computed["score"], 60)

    def test_exact_pass_line_score_passes(self):
        """恰好 pass_line: 60.0 分必须 PASS."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 60, "答辩表现": 60})
        _submit(self.db, examiner_id=2, scores={"报告质量": 60, "答辩表现": 60})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["score_precise"], "60.0")
        self.assertEqual(computed["score"], 60)
        self.assertEqual(computed["decision"], "PASS")

    def test_missing_item_examiner_excluded_not_zero_filled(self):
        """评委缺项: 整份不计入, 不得按 0 分或部分项混进均值."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 90, "答辩表现": 90})
        _submit(self.db, examiner_id=2, scores={"报告质量": 10})   # 缺「答辩表现」
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["valid_count"], 1)
        self.assertEqual(computed["sample_count"], 2)
        self.assertEqual(len(computed["excluded"]), 1)
        self.assertEqual(computed["excluded"][0]["examiner_id"], 2)
        self.assertEqual(computed["excluded"][0]["reason"], "MISSING_ITEMS")
        self.assertEqual(computed["excluded"][0]["missing_items"], ["答辩表现"])
        # 只按完整评委复算: 90/90 -> 90, 若按 0 补齐会变成 (90+10)/2 与 (90+0)/2
        self.assertEqual(computed["score"], 90)
        self.assertEqual(computed["item_avg"]["答辩表现"], 90.0)
        self.assertEqual(service.MISSING_ITEM_POLICY, "EXCLUDE_EXAMINER")
        self.assertIn("INSUFFICIENT_EXAMINERS", computed["blockers"])

    def test_absent_seat_yields_no_sample(self):
        """出席是结论计算的前置条件: 缺席学生零样本, 不出分."""
        self.assignment.seat_status = "ABSENT"
        self.db.flush()
        _submit(self.db, examiner_id=1, scores={"报告质量": 90, "答辩表现": 90})
        _submit(self.db, examiner_id=2, scores={"报告质量": 90, "答辩表现": 90})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertFalse(computed["seat_attended"])
        self.assertEqual(computed["valid_count"], 0)
        self.assertIsNone(computed["score"])
        self.assertIsNone(computed["decision"])
        self.assertIn("SEAT_NOT_ATTENDED", computed["blockers"])

    def test_score_from_unassigned_examiner_excluded(self):
        """非本场次评委名单里的评分不计入样本."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 80, "答辩表现": 80})
        _submit(self.db, examiner_id=3, scores={"报告质量": 100, "答辩表现": 100})  # 3 号不在名单
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["valid_count"], 1)
        self.assertEqual(computed["excluded"][0]["reason"], "EXAMINER_NOT_ASSIGNED")
        self.assertEqual(computed["score"], 80)

    def test_superseded_version_not_counted(self):
        """废弃版本不参与复算(既有口径回归保护)."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 60, "答辩表现": 60},
                status=ScoreStatus.SUPERSEDED.value)
        _submit(self.db, examiner_id=1, scores={"报告质量": 60, "答辩表现": 60}, version=2)
        _submit(self.db, examiner_id=2, scores={"报告质量": 80, "答辩表现": 70})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["valid_count"], 2)
        self.assertEqual(computed["score"], 68)      # (60+80)/2*0.5+(60+70)/2*0.5 = 67.5 -> 68

    def test_recompute_is_deterministic(self):
        """同一份 rubric 数据重复复算两次结果完全一致(D35-c)."""
        _submit(self.db, examiner_id=1, scores={"报告质量": 87.3, "答辩表现": 91.7})
        _submit(self.db, examiner_id=2, scores={"报告质量": 88.9, "答辩表现": 84.1})
        first = compute_result(self.db, self.assignment, self.rubric)
        second = compute_result(self.db, self.assignment, self.rubric)
        dump = lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        self.assertEqual(dump(first), dump(second))
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["score_precise"], second["score_precise"])
        self.assertEqual(first["decision"], second["decision"])

    def test_weights_not_summing_to_100_normalized(self):
        """权重合计非 100 时按合计归一, 不放大/缩小总分."""
        self.rubric.weights_json = json.dumps([{"name": "报告质量", "weight": 2},
                                               {"name": "答辩表现", "weight": 3}], ensure_ascii=False)
        self.db.flush()
        _submit(self.db, examiner_id=1, scores={"报告质量": 100, "答辩表现": 0})
        _submit(self.db, examiner_id=2, scores={"报告质量": 100, "答辩表现": 0})
        computed = compute_result(self.db, self.assignment, self.rubric)
        self.assertEqual(computed["score"], 40)   # 100*2/5 + 0*3/5


if __name__ == "__main__":
    unittest.main()
