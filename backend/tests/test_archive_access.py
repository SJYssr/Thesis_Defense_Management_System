"""D26 归档越权 + D28 归档幂等 回归用例.

D26: 姓名/学号/学院/专业/导师/成绩/答辩结论**在可见性判定之后**才构造,
     无权者拿到的响应字典里根本不存在这些键(不是置空, 也不是事后删除)。
D28: ArchivePackage 对 cycle_id 唯一, 撤回后再次提交必须复用同一条记录。
"""
import json
import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, create_engine, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, PermissionDeniedException, register_exception_handlers
from app.core.permissions import CurrentUser
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.models.business import (
    FinalDefense, GraduationCycle, GraduationMaterial, GraduationTopic, TopicSelection,
)
from app.models.defense import (
    ArchiveConsent, ArchiveFile, ArchivePackage, DefenseAssignment, DefenseExaminer,
    DefenseResult, DefenseSession,
)
from app.models.system import ReviewRecord, SysFile
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.modules.archive import router as archive_router
from app.modules.archive.router import (
    ArchiveCreate, archive_transition, create_archive, package_detail, revoke_package, search_packages,
)

SENSITIVE_KEYS = ("student_name", "student_no", "result_score", "result_decision")
IDENTITY_KEYS = ("student_name", "student_no", "college", "major", "teacher_name")

ADMIN = CurrentUser(id=5, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
SUPERVISOR = CurrentUser(id=1, username="t001", real_name="导师甲", role="TEACHER", profile_id=1)
STRANGER_TEACHER = CurrentUser(id=2, username="t002", real_name="无关教师", role="TEACHER", profile_id=2)
EXAMINER = CurrentUser(id=6, username="t003", real_name="评委丙", role="TEACHER", profile_id=3)
OWNER = CurrentUser(id=3, username="s001", real_name="学生甲", role="STUDENT", profile_id=1)
OTHER_STUDENT = CurrentUser(id=4, username="s002", real_name="学生乙", role="STUDENT", profile_id=2)


def new_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return engine


def seed(db: Session) -> dict:
    db.add(College(id=1, name="计算机学院", code="CS"))
    db.flush()
    db.add(Major(id=1, college_id=1, name="软件工程", code="SE"))
    for uid, username, name, role in ((1, "t001", "导师甲", "TEACHER"), (2, "t002", "无关教师", "TEACHER"),
                                      (3, "s001", "学生甲", "STUDENT"), (4, "s002", "学生乙", "STUDENT"),
                                      (5, "admin", "管理员", "ADMIN"), (6, "t003", "评委丙", "TEACHER")):
        db.add(SysUser(id=uid, username=username, real_name=name, password_hash="x", role=role))
    db.flush()
    db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
    db.add(TeacherProfile(id=2, user_id=2, teacher_no="T002"))
    db.add(TeacherProfile(id=3, user_id=6, teacher_no="T003"))
    db.add(StudentProfile(id=1, user_id=3, student_no="2022001", college_id=1, major_id=1))
    db.add(StudentProfile(id=2, user_id=4, student_no="2022002", college_id=1, major_id=1))
    db.add(GraduationTopic(id=1, title="毕业设计题目", teacher_id=1, max_students=1, status="PUBLISHED"))
    db.flush()
    db.add(GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1,
                           teacher_id=1, status="ACTIVE"))
    db.flush()
    now = datetime(2026, 5, 20, 9, 0)
    db.add(DefenseSession(id=1, academic_year="2026-2027", stage="FINAL", starts_at=now,
                          ends_at=now + timedelta(hours=2), status="PUBLISHED", capacity=10))
    db.flush()
    db.add(DefenseAssignment(id=1, session_id=1, cycle_id=1))
    # 只有 teacher_profile 3 是评委; teacher_profile 2 是"无关教师"(不得借此获得可见性)
    db.add(DefenseExaminer(id=1, session_id=1, teacher_id=3, examiner_role="EXAMINER"))
    db.flush()
    db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL", score=88, decision="PASS",
                         status="PUBLISHED", published_at=now))
    db.add(TopicSelection(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                          status="APPROVED", active_flag=1))
    db.add(FinalDefense(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1, status="APPROVED"))
    db.add(GraduationMaterial(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1, status="APPROVED"))
    db.add(SysFile(id=1, original_name="论文.pdf", stored_name="a.pdf", size=1024,
                   storage_path="2026/06/a.pdf", sha256="a" * 64, uploader_id=3))
    db.add(SysFile(id=2, original_name="源码.zip", stored_name="b.zip", size=2048,
                   storage_path="2026/06/b.zip", sha256="b" * 64, uploader_id=3))
    db.flush()
    return {"cycle_id": 1}


def make_package(db: Session, *, status: str = "ARCHIVED") -> ArchivePackage:
    pkg = ArchivePackage(id=1, cycle_id=1, result_id=1, title="毕业设计成果", abstract="摘要内容",
                         keywords="关键词", status=status, archived_at=datetime(2026, 6, 1), created_by=5)
    db.add(pkg)
    db.flush()
    db.add(ArchiveFile(id=1, package_id=pkg.id, file_id=1, file_role="THESIS", version=1, sha256="a" * 64))
    db.flush()
    return pkg


def body_json(response) -> dict:
    return json.loads(response.body)["data"]


class ArchiveVisibilityTest(unittest.TestCase):
    """D26: 无权身份拿不到姓名/学号/成绩/结论键."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.pkg = make_package(self.db, status="ARCHIVED")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _detail(self, user) -> dict:
        return body_json(package_detail(self.pkg.id, user=user, db=self.db))

    def test_unrelated_teacher_response_has_no_identity_keys(self):
        data = self._detail(STRANGER_TEACHER)
        for key in IDENTITY_KEYS + ("result_score", "result_decision"):
            self.assertNotIn(key, data, f"无关教师响应里不得出现 {key}")
        self.assertEqual(data["files"], [], "无权者不得拿到文件清单")
        self.assertEqual(data["visibility"], "PRIVATE")
        print(f"[D26] 无关教师可见键: {sorted(data.keys())}")

    def test_other_student_response_has_no_identity_keys(self):
        data = self._detail(OTHER_STUDENT)
        for key in SENSITIVE_KEYS + ("college", "major", "teacher_name"):
            self.assertNotIn(key, data, f"其他学生响应里不得出现 {key}")
        self.assertEqual(data["files"], [])
        print(f"[D26] 其他学生可见键: {sorted(data.keys())}")

    def test_anonymous_builder_cannot_emit_identity_keys(self):
        """未登录(无 token)在权限依赖层就被拦; 即使直接调用构造器也拿不到敏感键."""
        data = body_json(package_detail(self.pkg.id, user=None, db=self.db))
        for key in SENSITIVE_KEYS + ("college", "major", "teacher_name"):
            self.assertNotIn(key, data, f"匿名响应里不得出现 {key}")
        print(f"[D26] 未登录(user=None)可见键: {sorted(data.keys())}")

    def test_anonymous_http_request_is_rejected_before_any_field(self):
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(archive_router.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/api/v1/archive-packages/{self.pkg.id}")
        payload = resp.json()
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(payload["code"], 40100)
        self.assertIsNone(payload.get("data"), "未登录不得拿到任何归档字段")
        print(f"[D26] 未登录 HTTP: status={resp.status_code} code={payload['code']} keys={sorted(payload.keys())}")

    def test_owner_and_supervisor_and_examiner_see_identity_and_score(self):
        for label, user in (("本人", OWNER), ("导师", SUPERVISOR), ("评委", EXAMINER)):
            data = self._detail(user)
            for key in SENSITIVE_KEYS:
                self.assertIn(key, data, f"{label}应能看到 {key}")
            self.assertEqual(data["result_score"], 88)
            self.assertEqual(data["result_decision"], "PASS")
            self.assertEqual(len(data["files"]), 1, f"{label}应能看到归档文件清单")

    def test_metadata_consent_exposes_summary_only(self):
        self.db.add(ArchiveConsent(id=1, package_id=self.pkg.id, student_user_id=3,
                                   visibility="METADATA", consented_at=datetime(2026, 6, 2)))
        self.db.commit()
        data = self._detail(STRANGER_TEACHER)
        self.assertEqual(data["visibility"], "METADATA")
        self.assertEqual(data["abstract"], "摘要内容")
        for key in SENSITIVE_KEYS + ("college", "major", "teacher_name"):
            self.assertNotIn(key, data, f"仅元数据授权时不得暴露 {key}")
        self.assertEqual(data["files"], [])

    def test_full_text_consent_exposes_identity_still_not_score(self):
        self.db.add(ArchiveConsent(id=1, package_id=self.pkg.id, student_user_id=3,
                                   visibility="FULL_TEXT", consented_at=datetime(2026, 6, 2)))
        self.db.commit()
        data = self._detail(STRANGER_TEACHER)
        for key in IDENTITY_KEYS:
            self.assertIn(key, data)
        for key in ("result_score", "result_decision"):
            self.assertNotIn(key, data, "公开全文不含成绩与答辩结论")
        self.assertEqual(len(data["files"]), 1)


class ArchiveIdempotencyTest(unittest.TestCase):
    """D28: 提交 → 撤回 → 再提交 成功且始终只有一条记录."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create(self, file_ids=(1,)):
        return body_json(create_archive(
            ArchiveCreate(cycle_id=1, title="毕业设计成果", abstract="摘要内容", keywords="关键词",
                          files=[{"file_id": fid, "file_role": "THESIS"} for fid in file_ids]),
            user=ADMIN, db=self.db))

    def test_submit_withdraw_resubmit_keeps_single_row(self):
        first = self._create((1,))
        self.assertEqual(first["status"], "ARCHIVED")
        self.assertIsNotNone(first["archived_at"])
        self.assertEqual(self.db.execute(select(func.count(ArchivePackage.id))).scalar(), 1)

        revoke_package(package_id=1, user=ADMIN, db=self.db)
        self.db.expire_all()
        self.assertEqual(self.db.get(ArchivePackage, 1).status, "REVOKED")

        second = self._create((1, 2))  # 撤回后重新归档: 复用同一条记录, 不允许插入冲突
        self.assertEqual(second["id"], first["id"], "重新归档必须复用同一行(cycle 唯一约束)")
        self.assertEqual(second["status"], "ARCHIVED")
        self.assertEqual(self.db.execute(select(func.count(ArchivePackage.id))).scalar(), 1,
                         "撤回→再提交后仍然只能有一条归档记录")
        files = self.db.execute(select(func.count(ArchiveFile.id))).scalar()
        self.assertEqual(files, 2, "重新归档按新快照整体替换文件清单")
        print(f"[D28] submit→withdraw→resubmit: 记录数=1, package.status={second['status']}, files={files}")

    def test_duplicate_submit_on_archived_package_is_business_error(self):
        self._create()
        with self.assertRaises(BusinessException) as ctx:
            self._create()
        self.assertIn("已存在有效归档包", ctx.exception.message)

    def test_submit_while_pending_is_business_error(self):
        make_package(self.db, status="PENDING")
        self.db.commit()
        with self.assertRaises(BusinessException) as ctx:
            self._create()
        self.assertIn("正在处理中", ctx.exception.message)

    def test_revoked_row_reuse_does_not_touch_other_cycles(self):
        self._create()
        revoke_package(package_id=1, user=ADMIN, db=self.db)
        self._create()
        pkg = self.db.execute(select(ArchivePackage)).scalars().all()
        self.assertEqual([p.cycle_id for p in pkg], [1])


class ArchiveTransitionTest(unittest.TestCase):
    """归档状态统一: PENDING/ARCHIVED/REVOKED, 唯一写入口三重校验."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.pkg = make_package(self.db, status="PENDING")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_approve_moves_to_archived_not_approved(self):
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        self.db.commit()
        self.assertEqual(self.db.get(ArchivePackage, self.pkg.id).status, "ARCHIVED")
        self.assertIsNotNone(self.db.get(ArchivePackage, self.pkg.id).archived_at)
        print(f"[归档终态] approve 目标态 = {self.db.get(ArchivePackage, self.pkg.id).status}")

    def test_only_admin_can_approve(self):
        with self.assertRaises(PermissionDeniedException):
            archive_transition(self.db, self.pkg, "approve", SUPERVISOR)

    def test_illegal_source_state_is_invalid_transition(self):
        pkg = self.db.get(ArchivePackage, self.pkg.id)
        archive_transition(self.db, pkg, "approve", ADMIN)
        with self.assertRaises(BusinessException) as ctx:
            archive_transition(self.db, pkg, "approve", ADMIN)
        self.assertEqual(ctx.exception.code, 40003)

    def test_revoke_from_archived_then_resubmit_from_revoked(self):
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        archive_transition(self.db, self.pkg, "revoke", ADMIN, comment="撤回")
        self.assertEqual(self.pkg.status, "REVOKED")
        archive_transition(self.db, self.pkg, "submit", ADMIN)
        self.assertEqual(self.pkg.status, "PENDING")



def seed_thirty(db: Session) -> None:
    """30 条归档(每条一个独立 cycle/topic/学生), 供关键字下推用例构造"第 25 条才命中"."""
    db.add(College(id=1, name="计算机学院", code="CS"))
    db.flush()
    db.add(Major(id=1, college_id=1, name="软件工程", code="SE"))
    db.add(SysUser(id=1, username="t001", real_name="导师甲", password_hash="x", role="TEACHER"))
    db.flush()
    db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
    db.flush()
    for i in range(1, 31):
        db.add(SysUser(id=100 + i, username=f"s{i:03d}", real_name=f"学生{i:02d}",
                       password_hash="x", role="STUDENT"))
    db.flush()
    for i in range(1, 31):
        db.add(StudentProfile(id=i, user_id=100 + i, student_no=f"2022{i:03d}",
                              college_id=1, major_id=1))
        db.add(GraduationTopic(id=i, title=f"论文题目{i:02d}", teacher_id=1,
                               max_students=1, status="PUBLISHED"))
    db.flush()
    for i in range(1, 31):
        db.add(GraduationCycle(id=i, academic_year="2026-2027", student_id=i, topic_id=i,
                               teacher_id=1, status="ACTIVE"))
    db.flush()
    for i in range(1, 31):
        db.add(ArchivePackage(id=i, cycle_id=i, title=f"成果{i:02d}", abstract=f"摘要{i:02d}",
                              keywords=f"关键词{i:02d}", status="ARCHIVED",
                              archived_at=datetime(2026, 6, 1), created_by=5))
    db.flush()


class ArchiveKeywordPushdownTest(unittest.TestCase):
    """D33: 关键字条件下推到 SQL —— 先过滤再分页, total 是过滤之后的数量.

    反例就是本条用例的名字: 关键词只命中第 25 条时, 旧写法(先取一页再在内存里过滤)
    会返回空列表却写着 total=1; 现在必须第一页就返回它。
    """

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed_thirty(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _search(self, user=ADMIN, page=1, page_size=20, **kwargs) -> dict:
        return body_json(search_packages(page=page, page_size=page_size, user=user, db=self.db, **kwargs))

    def test_keyword_hitting_only_the_25th_row_returns_it_on_first_page(self):
        data = self._search(keyword="论文题目25")
        self.assertEqual(data["total"], 1, "total 必须是过滤之后的数量")
        self.assertEqual([item["id"] for item in data["items"]], [25],
                         "命中落在第 25 条时也必须出现在第一页(过滤在分页之前)")
        print(f"[D33] keyword=论文题目25 -> items={[i['id'] for i in data['items']]} total={data['total']}")

    def test_total_equals_filtered_count_not_page_size(self):
        data = self._search(keyword="论文题目0", page=1, page_size=20)
        self.assertEqual(data["total"], 9, "论文题目01..09 共 9 条")
        self.assertEqual(len(data["items"]), 9)
        data2 = self._search(keyword="论文题目0", page=2, page_size=5)
        self.assertEqual(data2["total"], 9, "翻页不改 total")
        self.assertEqual(len(data2["items"]), 4, "第 2 页只剩 4 条(9 - 5)")
        print(f"[D33] keyword=论文题目0 -> page1={len(data['items'])}/total={data['total']}, "
              f"page2_size5={len(data2['items'])}/total={data2['total']}")

    def test_keyword_still_respects_visibility(self):
        """别人的题目关键字(学生无可见性)不会因为下推而泄漏: 返回 0 条."""
        data = self._search(user=OTHER_STUDENT, keyword="论文题目25")
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])
        print(f"[D33] 其他学生 keyword=论文题目25 -> total={data['total']}")

    def test_admin_can_search_by_student_name(self):
        data = self._search(keyword="学生25")
        self.assertEqual(data["total"], 1)
        self.assertEqual([item["id"] for item in data["items"]], [25])
        self.assertEqual(data["items"][0]["student_no"], "2022025")
        print(f"[D33] ADMIN keyword=学生25 -> items={[i['id'] for i in data['items']]} "
              f"student_no={data['items'][0]['student_no']}")


class ArchiveTransitionRecordTest(unittest.TestCase):
    """状态机接管归档后的三件细节: archived_at 单写、revoke 强制原因、留痕带 student_id."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.pkg = make_package(self.db, status="PENDING")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _records(self, action: str | None = None):
        stmt = select(ReviewRecord).where(
            ReviewRecord.business_type == "ARCHIVE_PACKAGE",
            ReviewRecord.business_id == self.pkg.id,
        )
        if action:
            stmt = stmt.where(ReviewRecord.action == action)
        return self.db.execute(stmt.order_by(ReviewRecord.id)).scalars().all()

    def _status(self) -> str:
        self.db.expire_all()
        return self.db.get(ArchivePackage, self.pkg.id).status

    def test_approve_writes_archived_at_once_and_one_review_record(self):
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        self.db.commit()
        pkg = self.db.get(ArchivePackage, self.pkg.id)
        self.assertEqual(pkg.status, "ARCHIVED")
        self.assertIsNotNone(pkg.archived_at, "archived_at 必须由 state_machine._apply_effects 写入")
        records = self._records("APPROVE")
        self.assertEqual(len(records), 1, "approve 只留一条 ReviewRecord(不允许双写)")
        record = records[0]
        self.assertEqual((record.before_status, record.after_status), ("PENDING", "ARCHIVED"))
        self.assertEqual(record.student_id, 1, "留痕必须带学生 id(ArchivePackage 无此列, 由 cycle 反查)")
        self.assertEqual(record.reviewer_id, ADMIN.id)
        self.assertEqual(record.reviewer_role, "ADMIN")
        print(f"[归档留痕] approve: business_type={record.business_type} "
              f"{record.before_status}->{record.after_status} student_id={record.student_id}")

    def test_revoke_without_comment_is_rejected(self):
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        self.db.commit()
        with self.assertRaises(BusinessException) as ctx:
            archive_transition(self.db, self.pkg, "revoke", ADMIN)
        self.assertEqual(ctx.exception.code, 40003)
        self.assertIn("必须填写", ctx.exception.message)
        self.db.rollback()
        self.assertEqual(self._status(), "ARCHIVED", "缺原因的撤回不得改变状态")
        print(f"[归档] revoke 缺原因 -> {ctx.exception.code} {ctx.exception.message}")

    def test_revoked_resubmit_then_approve_again(self):
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        archive_transition(self.db, self.pkg, "revoke", ADMIN, comment="材料有误, 退回")
        self.assertEqual(self._status(), "REVOKED")
        revoked = self.db.get(ArchivePackage, self.pkg.id)
        self.assertIsNone(revoked.archived_at, "撤回后 archived_at 必须清空(不再算归档)")
        self.assertEqual(len(self._records("REVOKE")), 1, "revoke 必须留一条 REVOKE 记录")
        archive_transition(self.db, self.pkg, "submit", ADMIN)
        self.assertEqual(self._status(), "PENDING", "REVOKED 重新提交回到 PENDING")
        archive_transition(self.db, self.pkg, "approve", ADMIN)
        self.db.commit()
        self.assertEqual(self._status(), "ARCHIVED")
        self.assertEqual(len(self._records("SUBMIT")), 1)
        self.assertEqual(len(self._records("APPROVE")), 2)
        pkg = self.db.get(ArchivePackage, self.pkg.id)
        self.assertIsNotNone(pkg.archived_at, "再次 approve 后 archived_at 必须重新写入")
        print(f"[归档] ARCHIVED->REVOKED->PENDING->ARCHIVED 留痕: "
              f"SUBMIT={len(self._records('SUBMIT'))} APPROVE={len(self._records('APPROVE'))} "
              f"REVOKE={len(self._records('REVOKE'))} archived_at={pkg.archived_at is not None}")

    def test_submit_from_pending_is_idempotent(self):
        archive_transition(self.db, self.pkg, "submit", ADMIN)  # PENDING -> PENDING, 同态跳过
        self.db.commit()
        self.assertEqual(self._status(), "PENDING")
        self.assertEqual(len(self._records("SUBMIT")), 1)
        print("[归档] PENDING 重复 submit 幂等: 状态仍 PENDING")


if __name__ == "__main__":
    unittest.main()
