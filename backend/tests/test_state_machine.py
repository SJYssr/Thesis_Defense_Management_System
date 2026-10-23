"""统一状态机内核回归用例(docs/refactor-plan.md §4, 验收标准 1 / 缺陷 D6·D7·A2)."""
import unittest
from types import SimpleNamespace

from sqlalchemy import BigInteger, Integer, create_engine, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.business import GraduationCycle, OpeningReport
from app.models.defense import ArchivePackage, DefenseResult
from app.models.system import ReviewRecord
from app.shared import state_machine as sm


def actor(role: str, profile_id: int | None = None, user_id: int = 1):
    return SimpleNamespace(id=user_id, role=role, profile_id=profile_id)


def new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    # sqlite 只有 INTEGER PRIMARY KEY 才自增; 模型里的 BigInteger 主键在测试库里改成 INTEGER,
    # 否则状态机内部写 ReviewRecord 会撞 NOT NULL 约束(仅测试环境 DDL 兼容, 不改模型定义)。
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return Session(engine)


def add_report(db, report_id=1, status="NOT_SUBMITTED", teacher_id=1, student_id=1):
    report = OpeningReport(id=report_id, student_id=student_id, cycle_id=1, topic_id=1,
                           teacher_id=teacher_id, title="开题", status=status,
                           current_version=0, review_comment="上一次意见")
    db.add(report)
    db.flush()
    return report


def records(db, business_id=1):
    return db.execute(
        select(ReviewRecord).where(ReviewRecord.business_id == business_id).order_by(ReviewRecord.id)
    ).scalars().all()


class TransitionTableTest(unittest.TestCase):
    """转移表本身必须与 §4 的表逐行一致."""

    def test_entities_cover_plan(self):
        self.assertEqual(
            set(sm.TRANSITIONS),
            {sm.TOPIC, sm.SELECTION, sm.OPENING, sm.MIDTERM, sm.FINAL, sm.MATERIAL,
             sm.SESSION, sm.SEAT, sm.RESULT, sm.ARCHIVE, sm.CYCLE,
             sm.ANNOUNCEMENT, sm.STAGE_TASK},
        )
        # 不锁规则总数(每次扩表都会变), 只要求每个实体都有规则、且关键动作在表内
        described = {row["entity"] for row in sm.describe()}
        for entity in sm.TRANSITIONS:
            self.assertIn(entity, described)
        self.assertTrue(
            {("SEAT", "postpone"), ("SEAT", "retake"), ("SEAT", "reset"), ("RESULT", "review")}
            <= set(sm.RULES)
        )

    def test_announcement_can_be_republished_after_offline(self):
        """下线不是终点: 重新上线必须走下线的反向转移, 否则只能新建重复公告."""
        rule = sm.RULES[(sm.ANNOUNCEMENT, "publish")]
        self.assertEqual(rule.target, "PUBLISHED")
        self.assertEqual(set(rule.sources), {"DRAFT", "OFFLINE"})

    def test_archive_submit_allows_admin_on_behalf(self):
        """产品唯一归档提交入口是管理员(require_admin), 不能靠身份代理绕过校验."""
        rule = sm.RULES[(sm.ARCHIVE, "submit")]
        self.assertEqual(set(rule.roles), {"STUDENT", "ADMIN"})

    def test_stage_actions_and_targets(self):
        for entity in sm.STAGE_ENTITIES:
            self.assertEqual(sm.TARGETS[entity]["save"], "DRAFT")
            self.assertEqual(sm.TARGETS[entity]["submit"], "SUBMITTED")
            self.assertEqual(sm.TARGETS[entity]["withdraw"], "DRAFT")
            self.assertEqual(sm.TARGETS[entity]["approve"], "APPROVED")
            self.assertEqual(sm.TARGETS[entity]["reject"], "REJECTED")
            self.assertEqual(sm.TARGETS[entity]["request_revision"], "RESUBMIT_REQUIRED")
            self.assertEqual(sm.TRANSITIONS[entity]["approve"], frozenset({"SUBMITTED"}))
            # reject / request_revision 必须带意见, 且不共用同一条规则(§4/D7)
            self.assertTrue(sm.RULES[(entity, "reject")].require_comment)
            self.assertTrue(sm.RULES[(entity, "request_revision")].require_comment)

    def test_other_entities(self):
        self.assertEqual(sm.TRANSITIONS[sm.SELECTION]["submit"], frozenset({"PENDING"}))
        self.assertEqual(sm.TARGETS[sm.SELECTION]["submit"], "PENDING")
        self.assertEqual(sm.TARGETS[sm.SESSION]["cancel"], "CANCELLED")
        self.assertTrue(sm.RULES[(sm.SESSION, "cancel")].require_comment)
        self.assertEqual(sm.TARGETS[sm.SEAT]["attend"], "ATTENDED")
        self.assertEqual(sm.TARGETS[sm.SEAT]["absent"], "ABSENT")
        self.assertEqual(sm.TARGETS[sm.RESULT]["publish"], "PUBLISHED")
        self.assertTrue(sm.RULES[(sm.RESULT, "withdraw")].require_comment)
        # 归档状态真源是 ArchiveStatus(PENDING/ARCHIVED/REVOKED), 不是提交态那一套
        self.assertEqual(sm.TRANSITIONS[sm.ARCHIVE]["submit"], frozenset({"PENDING", "REVOKED"}))
        self.assertEqual(sm.TARGETS[sm.ARCHIVE]["submit"], "PENDING")
        self.assertEqual(sm.TARGETS[sm.ARCHIVE]["approve"], "ARCHIVED")
        self.assertEqual(sm.TARGETS[sm.ARCHIVE]["revoke"], "REVOKED")
        self.assertTrue(sm.RULES[(sm.ARCHIVE, "revoke")].require_comment)
        # 题目生命周期(教师侧)
        self.assertEqual(sm.TARGETS[sm.TOPIC]["close"], "CLOSED")
        self.assertEqual(sm.TARGETS[sm.CYCLE]["complete"], "COMPLETED")
        # compute 是服务端内部动作, 不出现在业务动作列表
        self.assertTrue(sm.RULES[(sm.RESULT, "compute")].internal)
        self.assertNotIn("compute", [r.action for r in sm.PLAN_RULES])

    def test_entity_for_stage(self):
        self.assertEqual(sm.entity_for_stage("FINAL"), sm.FINAL)
        self.assertEqual(sm.entity_for_stage(sm.ProcessStage.MATERIAL), sm.MATERIAL)
        with self.assertRaises(BusinessException) as ctx:
            sm.entity_for_stage("COMPLETED")
        self.assertEqual(ctx.exception.code, 40003)


class TransitionBehaviourTest(unittest.TestCase):
    def test_illegal_transition_uses_business_code_40003(self):
        with new_session() as db:
            report = add_report(db, status="APPROVED")
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 1))
            self.assertEqual(ctx.exception.code, 40003)
            self.assertIn("不允许动作", ctx.exception.message)
            self.assertEqual(report.status, "APPROVED")

    def test_unknown_action_is_rejected(self):
        with new_session() as db:
            report = add_report(db)
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.OPENING, report, "teleport", actor("STUDENT", 1))
            self.assertEqual(ctx.exception.code, 40003)

    def test_submit_side_effects_are_unified(self):
        with new_session() as db:
            report = add_report(db)
            sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 1, user_id=7))
            self.assertEqual(report.status, "SUBMITTED")
            self.assertEqual(report.current_version, 1)        # 版本号 +1
            self.assertIsNotNone(report.submitted_at)
            self.assertIsNone(report.review_comment)           # 重新提交清空旧意见
            rows = records(db)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0].action, rows[0].before_status, rows[0].after_status),
                             ("SUBMIT", "NOT_SUBMITTED", "SUBMITTED"))
            self.assertEqual(rows[0].comment, "提交第 1 版")
            self.assertEqual(rows[0].reviewer_id, 7)

    def test_second_submit_bumps_version_again(self):
        with new_session() as db:
            report = add_report(db)
            sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 1))
            sm.transition(db, sm.OPENING, report, "withdraw", actor("STUDENT", 1))
            sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 1))
            self.assertEqual(report.current_version, 2)

    def test_permission_denied_for_wrong_role(self):
        with new_session() as db:
            report = add_report(db, status="SUBMITTED")
            with self.assertRaises(PermissionDeniedException):
                sm.transition(db, sm.OPENING, report, "approve", actor("STUDENT", 1))
            self.assertEqual(report.status, "SUBMITTED")

    def test_teacher_cannot_approve_other_teachers_report(self):
        with new_session() as db:
            report = add_report(db, status="SUBMITTED", teacher_id=1)
            with self.assertRaises(PermissionDeniedException):
                sm.transition(db, sm.OPENING, report, "approve", actor("TEACHER", 99))
            self.assertEqual(report.status, "SUBMITTED")

    def test_student_cannot_touch_other_students_report(self):
        with new_session() as db:
            report = add_report(db, student_id=1)
            with self.assertRaises(PermissionDeniedException):
                sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 2))
            self.assertEqual(report.status, "NOT_SUBMITTED")

    def test_reject_and_request_revision_are_distinct(self):
        with new_session() as db:
            report = add_report(db, status="SUBMITTED")
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.OPENING, report, "reject", actor("TEACHER", 1), comment="  ")
            self.assertEqual(ctx.exception.code, 40003)
            self.assertEqual(report.status, "SUBMITTED")

            sm.transition(db, sm.OPENING, report, "reject", actor("TEACHER", 1), comment="方向不对")
            self.assertEqual(report.status, "REJECTED")            # 终态
            self.assertEqual(report.review_comment, "方向不对")
            self.assertIsNotNone(report.reviewed_at)

            sm.transition(db, sm.OPENING, report, "save", actor("STUDENT", 1))
            sm.transition(db, sm.OPENING, report, "submit", actor("STUDENT", 1))
            sm.transition(db, sm.OPENING, report, "request_revision", actor("TEACHER", 1), comment="补实验")
            self.assertEqual(report.status, "RESUBMIT_REQUIRED")
            actions = [r.action for r in records(db)]
            self.assertEqual(actions, ["REJECT", "SUBMIT", "RETURN"])

    def test_double_approve_only_first_lands(self):
        """A2: 条件更新保证并发第二审必失败且不产生多余留痕."""
        with new_session() as db:
            report = add_report(db, status="SUBMITTED")
            sm.transition(db, sm.OPENING, report, "approve", actor("TEACHER", 1))
            self.assertEqual(len(records(db)), 1)
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.OPENING, report, "approve", actor("ADMIN", None, user_id=2))
            self.assertEqual(ctx.exception.code, 40003)
            self.assertEqual(len(records(db)), 1)
            self.assertEqual(report.status, "APPROVED")

    def test_same_state_action_keeps_status(self):
        with new_session() as db:
            cycle = GraduationCycle(id=1, academic_year="2026-2027", student_id=1, status="ACTIVE")
            db.add(cycle)
            from app.models.business import TopicSelection
            selection = TopicSelection(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                       status="PENDING", active_flag=1)
            db.add(selection)
            db.flush()
            sm.transition(db, sm.SELECTION, selection, "submit", actor("STUDENT", 1))
            self.assertEqual(selection.status, "PENDING")

    def test_can_predicts_without_mutation(self):
        with new_session() as db:
            report = add_report(db, status="SUBMITTED")
            self.assertTrue(sm.can(sm.OPENING, "approve", report, actor("TEACHER", 1)))
            self.assertFalse(sm.can(sm.OPENING, "approve", report, actor("TEACHER", 2)))
            self.assertFalse(sm.can(sm.OPENING, "submit", report, actor("STUDENT", 1)))
            self.assertEqual(report.status, "SUBMITTED")
            self.assertEqual(sm.allowed_actions(sm.OPENING, "SUBMITTED"),
                             ["approve", "reject", "request_revision", "withdraw"])

    def test_cycle_complete_requires_archive_and_pass(self):
        with new_session() as db:
            cycle = GraduationCycle(id=1, academic_year="2026-2027", student_id=1, status="ACTIVE")
            db.add(cycle)
            db.flush()
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.CYCLE, cycle, "complete", actor("ADMIN"))
            self.assertEqual(ctx.exception.code, 40003)
            self.assertEqual(cycle.status, "ACTIVE")

            db.add(ArchivePackage(id=1, cycle_id=1, status="ARCHIVED"))
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL",
                                 status="PUBLISHED", decision="FAIL"))
            db.flush()
            with self.assertRaises(BusinessException):
                sm.transition(db, sm.CYCLE, cycle, "complete", actor("ADMIN"))

            result = db.get(DefenseResult, 1)
            result.decision = "PASS"
            db.flush()
            sm.transition(db, sm.CYCLE, cycle, "complete", actor("ADMIN"))
            self.assertEqual(cycle.status, "COMPLETED")

    def test_archive_submit_requires_published_result(self):
        with new_session() as db:
            archive = ArchivePackage(id=1, cycle_id=1, status="PENDING")
            db.add(archive)
            db.flush()
            with self.assertRaises(BusinessException) as ctx:
                sm.transition(db, sm.ARCHIVE, archive, "submit", actor("STUDENT", 1))
            self.assertEqual(ctx.exception.code, 40003)
            db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL",
                                 status="PUBLISHED", decision="PASS"))
            db.flush()
            sm.transition(db, sm.ARCHIVE, archive, "submit", actor("STUDENT", 1))
            # submit 的目标态与来源态同为 PENDING(幂等提交), 状态列不变
            self.assertEqual(archive.status, "PENDING")


def stage_fixture(db):
    """建一条当前学年 cycle + 已通过选题, 供流程编排用例复用."""
    from app.models.business import GraduationProcess, TopicSelection
    from app.shared.cycle import academic_year_for

    cycle = GraduationCycle(id=1, academic_year=academic_year_for(db), student_id=1,
                            topic_id=1, teacher_id=1, status="ACTIVE")
    db.add(cycle)
    db.flush()
    selection = TopicSelection(id=1, student_id=1, cycle_id=cycle.id, topic_id=1, teacher_id=1,
                               status="APPROVED", active_flag=1)
    db.add(selection)
    db.flush()
    return cycle, selection


class WorkflowStageTest(unittest.TestCase):
    """验收标准 4: 阶段推进用 dict 映射、末尾返回 None、进度只来自 compute()."""

    def test_next_stage_chain_and_terminals(self):
        from app.models.enums import ProcessStage
        from app.shared.workflow import next_stage

        self.assertEqual(next_stage(ProcessStage.SELECTION), ProcessStage.OPENING)
        self.assertEqual(next_stage(ProcessStage.OPENING), ProcessStage.MIDTERM)
        self.assertEqual(next_stage(ProcessStage.MIDTERM), ProcessStage.FINAL)
        self.assertEqual(next_stage(ProcessStage.FINAL), ProcessStage.MATERIAL)
        self.assertIsNone(next_stage(ProcessStage.MATERIAL))
        # 回归: 旧实现 STAGE_ORDER.index(COMPLETED) 直接 ValueError
        self.assertIsNone(next_stage(ProcessStage.COMPLETED))
        self.assertIsNone(next_stage("COMPLETED"))
        self.assertIsNone(next_stage("NOT_A_STAGE"))
        self.assertIsNone(next_stage(None))

    def test_selection_approved_progress_comes_from_compute(self):
        from app.models.business import GraduationProcess
        from app.models.enums import STAGE_WEIGHTS, ProcessStage
        from app.shared.workflow import GraduationWorkflowService

        with new_session() as db:
            _, selection = stage_fixture(db)
            process = GraduationWorkflowService.on_selection_approved(db, selection)
            # 旧实现手写 progress_percent = 10; 现在必须等于 compute() 的结果
            self.assertEqual(process.progress_percent, STAGE_WEIGHTS[ProcessStage.SELECTION])
            # 选题已通过 → 当前待办阶段是开题(compute 的 stage 语义: 第一个未通过阶段)
            self.assertEqual(process.current_stage, ProcessStage.OPENING.value)
            self.assertIsNone(process.completed_at)

    def test_stage_approved_does_not_fake_completion(self):
        from app.models.business import GraduationProcess
        from app.models.enums import STAGE_WEIGHTS, ProcessStage
        from app.shared.workflow import GraduationWorkflowService

        with new_session() as db:
            cycle, _ = stage_fixture(db)
            db.add(OpeningReport(id=1, student_id=1, cycle_id=cycle.id, topic_id=1, teacher_id=1,
                                 title="开题", status="APPROVED"))
            db.flush()
            process = GraduationWorkflowService.on_stage_approved(db, 1, ProcessStage.OPENING)
            self.assertEqual(process.progress_percent,
                             STAGE_WEIGHTS[ProcessStage.SELECTION] + STAGE_WEIGHTS[ProcessStage.OPENING])
            self.assertEqual(process.current_stage, ProcessStage.MIDTERM.value)

            # 回归 D5: MATERIAL 阶段被调用但材料并未通过 → 不得判定 COMPLETED/100
            GraduationWorkflowService.on_stage_approved(db, 1, ProcessStage.MATERIAL)
            db.flush()
            refreshed = db.execute(
                select(GraduationProcess).where(GraduationProcess.cycle_id == cycle.id)
            ).scalar_one()
            self.assertEqual(refreshed.current_stage, ProcessStage.MIDTERM.value)
            self.assertEqual(refreshed.progress_percent,
                             STAGE_WEIGHTS[ProcessStage.SELECTION] + STAGE_WEIGHTS[ProcessStage.OPENING])
            self.assertIsNone(refreshed.completed_at)


if __name__ == "__main__":
    unittest.main()
