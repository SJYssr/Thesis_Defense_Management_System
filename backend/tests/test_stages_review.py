"""阶段材料引擎回归用例(§4/§6/§8/§9; 缺陷 A1/A2/A3/D6/D7/D8/D10)."""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Integer, create_engine, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BusinessException,
    PermissionDeniedException,
    ResourceNotFoundException,
)
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import (
    FinalDefense,
    GraduationCycle,
    GraduationMaterial,
    GraduationProcess,
    MidtermReport,
    OpeningReport,
    TopicSelection,
)
from app.models.defense import DefenseAssignment, DefenseExaminer, DefenseSession
from app.models.enums import BusinessType, ProcessStage
from app.models.system import ReviewRecord
from app.models.user import StudentProfile, SysUser, TeacherProfile
from app.modules.final_defense.router import FINAL_CONFIG
from app.modules.opening.router import OPENING_CONFIG
from app.modules.stages.engine import StageConfig, StageEngine
from app.modules.stages.router_factory import ReviewRequest, make_admin_router


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


ADMIN = 99


def seed(db, cycle_status="ACTIVE"):
    db.add_all([
        SysUser(id=11, username="zhangsan", password_hash="x", real_name="张三", role="STUDENT"),
        SysUser(id=12, username="lisi", password_hash="x", real_name="李四", role="STUDENT"),
        SysUser(id=21, username="t1", password_hash="x", real_name="导师甲", role="TEACHER"),
        SysUser(id=22, username="t2", password_hash="x", real_name="导师乙", role="TEACHER"),
        SysUser(id=ADMIN, username="admin", password_hash="x", real_name="管理员", role="ADMIN"),
        StudentProfile(id=1, user_id=11, student_no="S001"),
        StudentProfile(id=2, user_id=12, student_no="S002"),
        TeacherProfile(id=1, user_id=21, teacher_no="T001"),
        TeacherProfile(id=2, user_id=22, teacher_no="T002"),
    ])
    db.add(GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1,
                           teacher_id=1, status=cycle_status))
    db.add(TopicSelection(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                          status="APPROVED", active_flag=1))
    db.add(GraduationProcess(id=1, student_id=1, cycle_id=1, current_stage="SELECTION",
                             progress_percent=0, risk_level="NORMAL"))
    db.flush()


DRAFT_PAYLOAD = {"title": "开题报告", "research_background": "背景", "research_content": "内容",
                 "research_method": "方法", "schedule_plan": "计划"}


def submitted_opening(db, engine=None):
    engine = engine or StageEngine(OPENING_CONFIG)
    engine.student_save(db, 1, DRAFT_PAYLOAD)
    engine.student_submit(db, 1, user_id=11)
    return engine


def get_report(db, engine):
    return db.execute(select(engine.cfg.model).order_by(engine.cfg.model.id.desc())).scalars().first()


class DraftStateTest(unittest.TestCase):
    """A1: 新建即以草稿保存必须真的进 DRAFT(草稿态可达)."""

    def test_new_save_reaches_draft(self):
        with new_session() as db:
            seed(db)
            engine = StageEngine(OPENING_CONFIG)
            view = engine.student_save(db, 1, DRAFT_PAYLOAD)
            self.assertEqual(view["status"], "DRAFT")
            self.assertEqual(view["status_cn"], "草稿")
            self.assertEqual(get_report(db, engine).status, "DRAFT")
            self.assertTrue(view["can_edit"])

    def test_new_object_created_with_explicit_initial_status(self):
        with new_session() as db:
            seed(db)
            engine = StageEngine(OPENING_CONFIG)
            engine.student_save(db, 1, {"title": "只有标题"})
            report = get_report(db, engine)
            self.assertIsNotNone(report.id)
            self.assertEqual(report.status, "DRAFT")

    def test_missing_cycle_reports_business_error_not_attribute_error(self):
        with new_session() as db:
            seed(db)
            engine = StageEngine(OPENING_CONFIG)
            self.assertIsNone(engine.get_report(db, 2))
            with self.assertRaises(BusinessException) as ctx:
                engine.require_cycle(db, 2)
            self.assertIn("流程实例", str(ctx.exception))
            self.assertIsNone(engine.get_report(db, 2))


class SubmitFlowTest(unittest.TestCase):
    def test_submit_writes_state_and_side_effects(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report = get_report(db, engine)
            self.assertEqual(report.status, "SUBMITTED")
            self.assertEqual(report.current_version, 1)
            self.assertIsNotNone(report.submitted_at)
            self.assertIsNone(report.review_comment)
            self.assertEqual(report.summary if hasattr(report, "summary") else report.title, "开题报告")

    def test_submit_blocked_when_required_field_missing(self):
        with new_session() as db:
            seed(db)
            engine = StageEngine(OPENING_CONFIG)
            engine.student_save(db, 1, {"title": "只有标题"})
            with self.assertRaises(BusinessException):
                engine.student_submit(db, 1, user_id=11)


class DecisionSplitTest(unittest.TestCase):
    """D7: 驳回与退回修改是两个显式动作, 不由 comment 是否为空推断."""

    def test_reject_without_comment_is_rejected_by_machine(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            with self.assertRaises(BusinessException) as ctx:
                engine.review(db, report_id, 21, "TEACHER", decision="reject",
                              teacher_profile_id=1)
            self.assertEqual(ctx.exception.code, 40003)
            self.assertEqual(get_report(db, engine).status, "SUBMITTED")

    def test_reject_with_comment_sets_rejected(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            engine.review(db, report_id, 21, "TEACHER", decision="reject", comment="内容不足",
                          teacher_profile_id=1)
            report = get_report(db, engine)
            self.assertEqual(report.status, "REJECTED")
            self.assertEqual(report.review_comment, "内容不足")
            self.assertIsNotNone(report.reviewed_at)
            actions = [r.action for r in db.execute(select(ReviewRecord)).scalars()]
            self.assertEqual(actions, ["SUBMIT", "REJECT"])

    def test_request_revision_sets_resubmit_required(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            engine.review(db, report_id, 21, "TEACHER", decision="request_revision",
                          comment="请补充实验", teacher_profile_id=1)
            report = get_report(db, engine)
            self.assertEqual(report.status, "RESUBMIT_REQUIRED")
            actions = [r.action for r in db.execute(select(ReviewRecord)).scalars()]
            self.assertEqual(actions, ["SUBMIT", "RETURN"])

    def test_router_reject_passes_explicit_decision_not_comment(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            router = make_admin_router(OPENING_CONFIG, "opening-reports")
            reject_route = next(r for r in router.routes if getattr(r, "path", "").endswith("/{report_id}/reject"))
            admin_user = type("U", (), {"id": ADMIN, "role": "ADMIN", "profile_id": None})()
            body = ReviewRequest(decision="request_revision", comment="请补充数据")
            reject_route.endpoint(report_id=report_id, body=body, user=admin_user, db=db)
            self.assertEqual(get_report(db, engine).status, "RESUBMIT_REQUIRED")

    def test_router_reject_defaults_to_reject_and_still_needs_comment(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            router = make_admin_router(OPENING_CONFIG, "opening-reports")
            reject_route = next(r for r in router.routes if getattr(r, "path", "").endswith("/{report_id}/reject"))
            admin_user = type("U", (), {"id": ADMIN, "role": "ADMIN", "profile_id": None})()
            with self.assertRaises(BusinessException) as ctx:
                reject_route.endpoint(report_id=report_id, body=ReviewRequest(comment=None),
                                      user=admin_user, db=db)
            self.assertEqual(ctx.exception.code, 40003)
            reject_route.endpoint(report_id=report_id, body=ReviewRequest(comment="不合格"),
                                  user=admin_user, db=db)
            self.assertEqual(get_report(db, engine).status, "REJECTED")


class DoubleApproveTest(unittest.TestCase):
    """A2: 并发/重复审核只能成功一次, 第二次抛业务异常且不新增留痕."""

    def test_second_approve_raises_and_records_once(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            engine.review(db, report_id, 21, "TEACHER", decision="approve", teacher_profile_id=1)
            with self.assertRaises(BusinessException) as ctx:
                engine.review(db, report_id, ADMIN, "ADMIN", decision="approve")
            self.assertEqual(ctx.exception.code, 40003)
            self.assertEqual(get_report(db, engine).status, "APPROVED")
            count = db.execute(
                select(func.count()).select_from(ReviewRecord).where(ReviewRecord.action == "APPROVE")
            ).scalar()
            self.assertEqual(count, 1)

    def test_get_for_review_for_update_sees_committed_state(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            report_id = get_report(db, engine).id
            engine.review(db, report_id, 21, "TEACHER", decision="approve", teacher_profile_id=1)
            view = engine.get_for_review(db, report_id, 1, for_update=True)
            self.assertEqual(view.report.status, "APPROVED")
            self.assertTrue(view.can_edit)


class CrossYearReviewTest(unittest.TestCase):
    """A3: 审核历史学年材料不得改写当前学年流程实例的进度."""

    def test_old_year_review_keeps_new_year_progress(self):
        with new_session() as db:
            seed(db, cycle_status="COMPLETED")
            db.add(GraduationCycle(id=2, academic_year="2027-2028", student_id=1, topic_id=1,
                                   teacher_id=1, status="ACTIVE"))
            db.add(GraduationProcess(id=2, student_id=1, cycle_id=2, current_stage="SELECTION",
                                     progress_percent=0, risk_level="NORMAL"))
            db.add(OpeningReport(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                 title="旧学年开题", status="SUBMITTED"))
            db.flush()
            # 当前学年仍是 cycle 2(最新 ACTIVE)
            engine = StageEngine(OPENING_CONFIG)
            engine.review(db, 1, 21, "TEACHER", decision="approve", teacher_profile_id=1)

            db.flush()
            active_process = db.execute(
                select(GraduationProcess).where(GraduationProcess.cycle_id == 2)
            ).scalar_one()
            self.assertEqual((active_process.current_stage, active_process.progress_percent),
                             ("SELECTION", 0))
            self.assertIsNone(active_process.completed_at)

            old_process = db.execute(
                select(GraduationProcess).where(GraduationProcess.cycle_id == 1)
            ).scalar_one()
            self.assertEqual(old_process.current_stage, ProcessStage.MIDTERM.value)
            self.assertEqual(old_process.progress_percent, 30)


class ScoreSingleTrackTest(unittest.TestCase):
    """§6 / D6: 审核通过不再写 FinalDefense.score."""

    def test_approve_ignores_score_parameter(self):
        with new_session() as db:
            seed(db)
            db.add(MidtermReport(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                 status="APPROVED"))
            db.add(FinalDefense(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                summary="总结", technical_solution="方案", test_result="结果",
                                status="SUBMITTED"))
            db.flush()
            engine = StageEngine(FINAL_CONFIG)
            item = engine.review(db, 1, 21, "TEACHER", decision="approve", teacher_profile_id=1,
                                 score=95)
            report = db.get(FinalDefense, 1)
            self.assertEqual(report.status, "APPROVED")
            self.assertIsNone(report.score)
            self.assertIsNone(item["score"])


class ExaminerReadOnlyTest(unittest.TestCase):
    """§8: 该 cycle 场次的答辩评委可读只读视图, 不能审核; 他人视为不存在."""

    def _attach_examiner(self, db, teacher_id=2):
        start = datetime(2026, 5, 1, 9, 0)
        db.add(DefenseSession(id=10, academic_year="2026-2027", stage="FINAL", title="答辩1",
                              starts_at=start, ends_at=start + timedelta(hours=2), status="PUBLISHED"))
        db.add(DefenseAssignment(id=10, session_id=10, cycle_id=1, seat_status="SCHEDULED"))
        db.add(DefenseExaminer(id=10, session_id=10, teacher_id=teacher_id))
        db.flush()

    def test_examiner_gets_read_only_view(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            self._attach_examiner(db)
            view = engine.get_for_review(db, 1, 2)
            self.assertFalse(view.can_edit)
            self.assertTrue(view.is_examiner)
            self.assertEqual(view.report.id, 1)

    def test_examiner_cannot_review(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            self._attach_examiner(db)
            with self.assertRaises(PermissionDeniedException):
                engine.review(db, 1, 22, "TEACHER", decision="approve", teacher_profile_id=2)
            self.assertEqual(get_report(db, engine).status, "SUBMITTED")

    def test_unrelated_teacher_sees_not_found(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            with self.assertRaises(ResourceNotFoundException):
                engine.get_for_review(db, 1, 2)

    def test_admin_view_is_editable(self):
        with new_session() as db:
            seed(db)
            engine = submitted_opening(db)
            view = engine.get_for_review(db, 1, None)
            self.assertTrue(view.can_edit)
            self.assertFalse(view.is_examiner)


class AdminListKeywordTest(unittest.TestCase):
    """§9 / D10: 关键字过滤下推 SQL, total 与过滤结果口径一致."""

    def _seed_two_students(self, db):
        seed(db)
        db.add(OpeningReport(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                             title="报告一", status="SUBMITTED"))
        db.add(OpeningReport(id=2, student_id=2, cycle_id=2, topic_id=1, teacher_id=1,
                             title="报告二", status="SUBMITTED"))
        db.flush()

    def test_keyword_filter_reduces_total(self):
        with new_session() as db:
            self._seed_two_students(db)
            engine = StageEngine(OPENING_CONFIG)
            items, total = engine.admin_list(db, None, 1, 20)
            self.assertEqual((len(items), total), (2, 2))
            items, total = engine.admin_list(db, None, 1, 20, keyword="S002")
            self.assertEqual((len(items), total), (1, 1))
            self.assertEqual(items[0]["student_no"], "S002")

    def test_total_is_filtered_count_not_page_size(self):
        with new_session() as db:
            self._seed_two_students(db)
            engine = StageEngine(OPENING_CONFIG)
            items, total = engine.admin_list(db, None, 1, 1, keyword="张三")
            self.assertEqual((len(items), total), (1, 1))
            items, total = engine.admin_list(db, None, 1, 1)
            self.assertEqual((len(items), total), (1, 2))

    def test_status_filter_combines_with_keyword(self):
        with new_session() as db:
            self._seed_two_students(db)
            engine = StageEngine(OPENING_CONFIG)
            items, total = engine.admin_list(db, "APPROVED", 1, 20, keyword="S002")
            self.assertEqual((len(items), total), (0, 0))

    def test_admin_router_reports_filtered_total(self):
        with new_session() as db:
            self._seed_two_students(db)
            router = make_admin_router(OPENING_CONFIG, "opening-reports")
            list_route = next(r for r in router.routes
                              if getattr(r, "path", "") == router.prefix and "GET" in getattr(r, "methods", ()))
            admin_user = type("U", (), {"id": ADMIN, "role": "ADMIN", "profile_id": None})()
            response = list_route.endpoint(status=None, page=1, page_size=20, keyword="李四",
                                           _=admin_user, db=db)
            data = json.loads(response.body)["data"]
            self.assertEqual(data["total"], 1)
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["student_name"], "李四")


class PaginateTest(unittest.TestCase):
    """验收标准 6: shared/query.paginate 先过滤后分页, total 与 items 口径一致."""

    def test_keyword_searches_multiple_fields(self):
        from app.shared.query import paginate

        with new_session() as db:
            seed(db)
            db.add_all([
                OpeningReport(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                              title="报告一", status="SUBMITTED"),
                OpeningReport(id=2, student_id=2, cycle_id=2, topic_id=1, teacher_id=1,
                              title="报告二", status="SUBMITTED"),
            ])
            db.flush()
            stmt = (select(OpeningReport)
                    .outerjoin(StudentProfile, StudentProfile.id == OpeningReport.student_id)
                    .outerjoin(SysUser, SysUser.id == StudentProfile.user_id)
                    .order_by(OpeningReport.id.desc()))
            fields = (SysUser.real_name, SysUser.username, StudentProfile.student_no)
            # 多字段 OR: username / real_name / student_no 任一处命中都应返回同一行
            for keyword in ("lisi", "李四", "S002"):
                rows, total = paginate(db, stmt, page=1, size=20, keyword=keyword, keyword_fields=fields)
                self.assertEqual((len(rows), total), (1, 1), keyword)
                self.assertEqual(rows[0].id, 2)

    def test_page_and_size_are_clamped(self):
        from app.shared.query import MAX_PAGE_SIZE, paginate

        with new_session() as db:
            seed(db)
            db.add_all([OpeningReport(id=3, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                      title="报告三", status="SUBMITTED")])
            db.flush()
            stmt = select(OpeningReport).order_by(OpeningReport.id)
            rows, total = paginate(db, stmt, page=0, size=0)
            self.assertEqual(total, 1)
            self.assertEqual(len(rows), 1)
            rows, _ = paginate(db, stmt, page=1, size=9999)
            self.assertEqual(len(rows), 1)
            self.assertEqual(MAX_PAGE_SIZE, 100)

    def test_keyword_without_fields_is_business_error(self):
        from app.shared.query import paginate

        with new_session() as db:
            seed(db)
            with self.assertRaises(BusinessException) as ctx:
                paginate(db, select(OpeningReport), page=1, size=20, keyword="张三")
            self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
