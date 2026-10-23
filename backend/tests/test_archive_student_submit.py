"""P9-A 学生自助提交毕业成果归档 + 归档包归属越权回归.

契约:
- 学生 POST /student/archive/packages 只 submit(→PENDING), 学院 approve 才 ARCHIVED。
- ArchivePackage 补 student_id 后, state_machine 对 STUDENT 的归属校验才真正生效:
  此前 getattr(obj, "student_id", None) 恒为 None → 不校验 → 学生 A 可提交学生 B 的归档包。
- 归档是"是否毕业"的判定输入之一, approve 后必须 recompute, GraduationProcess.completed_at 才会落值。
"""
import ast
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BusinessException, PermissionDeniedException, ResourceNotFoundException, register_exception_handlers,
)
from app.core.permissions import CurrentUser, get_current_user
from app.db.base import Base
from app.db.mysql import get_db
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocument
from app.models.business import GraduationCycle, GraduationMaterial, GraduationProcess, GraduationTopic
from app.models.defense import ArchiveFile, ArchivePackage, DefenseAssignment, DefenseResult, DefenseSession
from app.models.system import SysFile, TopicAttachment
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.modules.archive import router as archive_router
from app.modules.archive.router import (
    ArchiveApprove, StudentArchiveFile, StudentArchiveSubmit, approve_package, archive_transition,
    search_packages, student_archive_eligibility, submit_archive_package,
)

ADMIN = CurrentUser(id=5, username="admin", real_name="管理员", role="ADMIN", profile_id=None)
STUDENT_A = CurrentUser(id=3, username="s20230001", real_name="学生甲", role="STUDENT", profile_id=1)
STUDENT_B = CurrentUser(id=4, username="s20230002", real_name="学生乙", role="STUDENT", profile_id=2)
STUDENT_FAIL = CurrentUser(id=7, username="s20230003", real_name="学生丙", role="STUDENT", profile_id=3)
STUDENT_NO_PROFILE = CurrentUser(id=9, username="s999", real_name="无档案", role="STUDENT", profile_id=None)


def new_engine():
    """sqlite 内存库范式: BigInteger 主键改 Integer 才能自增."""
    # StaticPool + check_same_thread=False: TestClient 在独立线程里跑 app, 必须共用同一条
    # 内存库连接, 否则 sqlite :memory: 会为那个线程新开一个空库(no such table)。
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return engine


def seed(db: Session) -> dict:
    """学生甲(20230001, 可归档) / 学生乙(20230002, 他人) / 学生丙(20230003, 答辩未通过)."""
    db.add(College(id=1, name="计算机学院", code="CS"))
    db.add(Major(id=1, college_id=1, name="软件工程", code="SE"))
    for uid, username, name, role in ((1, "t001", "导师甲", "TEACHER"), (3, "s20230001", "学生甲", "STUDENT"),
                                      (4, "s20230002", "学生乙", "STUDENT"), (5, "admin", "管理员", "ADMIN"),
                                      (7, "s20230003", "学生丙", "STUDENT")):
        db.add(SysUser(id=uid, username=username, real_name=name, password_hash="x", role=role))
    db.flush()
    db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
    db.add(StudentProfile(id=1, user_id=3, student_no="20230001", college_id=1, major_id=1))
    db.add(StudentProfile(id=2, user_id=4, student_no="20230002", college_id=1, major_id=1))
    db.add(StudentProfile(id=3, user_id=7, student_no="20230003", college_id=1, major_id=1))
    db.add(GraduationTopic(id=1, title="毕业设计题目", teacher_id=1, max_students=3, status="PUBLISHED"))
    db.flush()
    for cid, sid in ((1, 1), (2, 2), (3, 3)):
        db.add(GraduationCycle(id=cid, academic_year="2026-2027", student_id=sid, topic_id=1,
                               teacher_id=1, status="ACTIVE"))
    now = datetime(2026, 5, 20, 9, 0)
    db.add(DefenseSession(id=1, academic_year="2026-2027", stage="FINAL", starts_at=now,
                          ends_at=now + timedelta(hours=2), status="PUBLISHED", capacity=10))
    db.flush()
    db.add(DefenseAssignment(id=1, session_id=1, cycle_id=1))
    db.add(DefenseAssignment(id=3, session_id=1, cycle_id=3))
    db.flush()
    db.add(DefenseResult(id=1, assignment_id=1, cycle_id=1, stage="FINAL", score=88, decision="PASS",
                         status="PUBLISHED", published_at=now))
    db.add(DefenseResult(id=3, assignment_id=3, cycle_id=3, stage="FINAL", score=52, decision="FAIL",
                         status="PUBLISHED", published_at=now))
    db.add(GraduationMaterial(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1, status="APPROVED"))
    db.add(GraduationMaterial(id=3, student_id=3, cycle_id=3, topic_id=1, teacher_id=1, status="APPROVED"))
    # 学生甲(上传人 user 3)三个文件; 学生乙(上传人 user 4)一个文件
    db.add(SysFile(id=1, original_name="论文.pdf", stored_name="a.pdf", size=1024, extension="pdf",
                   storage_path="2026/06/a.pdf", sha256="a" * 64, uploader_id=3))
    db.add(SysFile(id=2, original_name="源码.zip", stored_name="b.zip", size=2048, extension="zip",
                   storage_path="2026/06/b.zip", sha256="b" * 64, uploader_id=3))
    db.add(SysFile(id=3, original_name="他人的论文.pdf", stored_name="c.pdf", size=4096, extension="pdf",
                   storage_path="2026/06/c.pdf", sha256="c" * 64, uploader_id=4))
    db.add(SysFile(id=4, original_name="已挂题目附件.pdf", stored_name="d.pdf", size=512, extension="pdf",
                   storage_path="2026/06/d.pdf", sha256="d" * 64, uploader_id=3))
    db.add(SysFile(id=5, original_name="已删除.pdf", stored_name="e.pdf", size=512, extension="pdf",
                   storage_path="2026/06/e.pdf", sha256="e" * 64, uploader_id=3,
                   deleted_at=datetime(2026, 6, 2)))
    db.flush()
    db.add(TopicAttachment(id=1, topic_id=1, file_id=4, created_at=now))  # 已被业务记录引用
    db.flush()
    # ai_document.file_id 可空且插一条 NULL: NOT IN 写法遇到 NULL 会让谓词恒假(文件列表恒空)
    db.add(AIDocument(id=1, document_type="THESIS", document_name="知识库文档", file_id=None))
    db.add(GraduationProcess(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                             current_stage="FINAL", progress_percent=80, risk_level="LOW",
                             started_at=now, completed_at=None))
    db.flush()
    return {"cycle_id": 1, "failed_cycle_id": 3}


def body_all(response) -> dict:
    """兼容两种响应对象: 直接调 router 拿 JSONResponse(.body), HTTP 拿 httpx.Response(.text)."""
    return json.loads(getattr(response, "body", None) or response.text)


def body_json(response) -> dict:
    return body_all(response)["data"]


class ArchiveBaseCase(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        self.ids = seed(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def client(self, user) -> TestClient:
        """按 R-W 契约用 HTTP 层验证状态码; 身份用 dependency_overrides 注入."""
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(archive_router.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def submit(self, user=STUDENT_A, file_ids=(1, 2)):
        return submit_archive_package(
            StudentArchiveSubmit(title="毕业设计成果", abstract="摘要", keywords="归档,测试",
                                 files=[StudentArchiveFile(file_id=fid, file_role="THESIS") for fid in file_ids]),
            user=user, db=self.db)

    def package_count(self):
        return len(self.db.execute(select(ArchivePackage)).scalars().all())


class EligibilityTest(ArchiveBaseCase):
    """GET /student/archive/eligibility: 能提交与否 + 可用文件清单."""

    def test_eligible_student_20230001(self):
        data = body_json(student_archive_eligibility(user=STUDENT_A, db=self.db))
        print(f"[eligibility] 学生20230001: eligible={data['eligible']} reason={data['reason']} "
              f"cycle={data['cycle_id']} files={[f['file_id'] for f in data['files']]}")
        self.assertTrue(data["eligible"])
        self.assertIsNone(data["reason"])
        self.assertEqual(data["cycle_id"], 1)
        self.assertEqual(data["academic_year"], "2026-2027")
        self.assertIsNone(data["package_id"])
        self.assertIsNone(data["package_status"])
        # 只列本人上传、未删除、未被任何业务记录引用: 2/1 在, 3(他人)/4(已挂题目)/5(已删除)不在
        self.assertEqual([f["file_id"] for f in data["files"]], [2, 1])
        first = data["files"][0]
        self.assertEqual(first["original_name"], "源码.zip")
        self.assertEqual(first["size"], 2048)
        self.assertEqual(first["extension"], "zip")
        self.assertIsNotNone(first["uploaded_at"])

    def test_failed_defense_student_not_eligible(self):
        data = body_json(student_archive_eligibility(user=STUDENT_FAIL, db=self.db))
        print(f"[eligibility] 学生20230003(答辩未通过): eligible={data['eligible']} reason={data['reason']}")
        self.assertFalse(data["eligible"])
        self.assertTrue(data["reason"])
        self.assertIn("未通过", data["reason"])
        self.assertEqual(data["cycle_id"], 3)

    def test_no_active_cycle_reason(self):
        data = body_json(student_archive_eligibility(user=STUDENT_NO_PROFILE, db=self.db))
        print(f"[eligibility] 无档案账号: eligible={data['eligible']} reason={data['reason']}")
        self.assertFalse(data["eligible"])
        self.assertTrue(data["reason"])
        self.assertIsNone(data["cycle_id"])
        self.assertEqual(data["files"], [])

    def test_existing_package_reflected(self):
        self.submit()
        data = body_json(student_archive_eligibility(user=STUDENT_A, db=self.db))
        print(f"[eligibility] 提交后: package_id={data['package_id']} status={data['package_status']} "
              f"cn={data['package_status_cn']}")
        self.assertIsNotNone(data["package_id"])
        self.assertEqual(data["package_status"], "PENDING")
        self.assertEqual(data["package_status_cn"], "待归档")


class StudentSubmitTest(ArchiveBaseCase):
    """学生提交: 只到 PENDING, 写归属, 重复提交明确拒绝."""

    def test_submit_creates_pending_package_with_owner(self):
        response = self.submit()
        payload = body_all(response)
        data = payload["data"]
        print(f"[submit] message={payload['message']} status={data['status']} "
              f"status_cn={data['status_cn']} topic={data['topic_title']} file_count={data['file_count']}")
        self.assertEqual(payload["message"], "归档申请已提交, 等待学院确认")
        self.assertEqual(data["status"], "PENDING")
        self.assertEqual(data["status_cn"], "待归档")
        self.assertEqual(data["file_count"], 2)
        pkg = self.db.execute(select(ArchivePackage)).scalars().one()
        self.assertEqual(pkg.status, "PENDING")
        self.assertEqual(pkg.student_id, 1, "归属学生必须写入(student_profile.id)")
        self.assertEqual(pkg.teacher_id, 1)
        self.assertIsNone(pkg.archived_at, "学生提交不得自动 approve")
        self.assertEqual(len(pkg_files(self.db, pkg.id)), 2)
        print(f"[submit] package_id={pkg.id} student_id={pkg.student_id} teacher_id={pkg.teacher_id} "
              f"archived_at={pkg.archived_at}")

    def test_resubmit_same_cycle_is_400(self):
        client = self.client(STUDENT_A)
        body = {"title": "t", "files": [{"file_id": 1, "file_role": "THESIS"}]}
        first = client.post("/api/v1/student/archive/packages", json=body)
        second = client.post("/api/v1/student/archive/packages", json=body)
        print(f"[submit] 首次={first.status_code} 再次={second.status_code} "
              f"message={body_all(second)['message']} 记录数={self.package_count()}")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertIn("请勿重复提交", body_all(second)["message"])
        self.assertEqual(self.package_count(), 1, "重复提交不得产生第二条记录")

    def test_archived_package_rejects_resubmit(self):
        self.submit()
        pkg = self.db.execute(select(ArchivePackage)).scalars().one()
        archive_transition(self.db, pkg, "approve", ADMIN, comment="通过")
        self.db.commit()
        with self.assertRaises(BusinessException) as ctx:
            self.submit()
        print(f"[submit] 已归档后再提交: {ctx.exception.message}")
        self.assertIn("已完成归档", ctx.exception.message)

    def test_files_of_other_student_rejected(self):
        client = self.client(STUDENT_A)
        r = client.post("/api/v1/student/archive/packages",
                        json={"files": [{"file_id": 3, "file_role": "THESIS"}]})
        print(f"[submit] 混入他人文件: HTTP={r.status_code} code={body_all(r)['code']} "
              f"message={body_all(r)['message']} 记录数={self.package_count()}")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.package_count(), 0, "校验失败不得落库")
        self.assertEqual(len(db_files(self.db, 3)), 0)

    def test_deleted_file_rejected(self):
        with self.assertRaises(ResourceNotFoundException):
            self.submit(file_ids=(5,))
        self.assertEqual(self.package_count(), 0)

    def test_cross_student_submit_blocked_by_state_machine(self):
        """越权根因回归: 学生甲不得对学生乙的 PENDING 归档包做 submit."""
        other = ArchivePackage(cycle_id=2, result_id=1, title="乙的成果", status="PENDING",
                               student_id=2, teacher_id=1, created_by=4)
        self.db.add(other)
        self.db.commit()
        with self.assertRaises(PermissionDeniedException) as ctx:
            archive_transition(self.db, other, "submit", STUDENT_A)
        print(f"[越权] 学生甲 submit 学生乙归档包: {type(ctx.exception).__name__} {ctx.exception.message}")
        self.db.rollback()
        self.assertEqual(self.db.get(ArchivePackage, other.id).student_id, 2)

    def test_no_profile_cannot_submit(self):
        with self.assertRaises(PermissionDeniedException):
            self.submit(user=STUDENT_NO_PROFILE)


class ApproveTest(ArchiveBaseCase):
    """approve: 仅管理员, 归档生效且进度重算."""

    def test_student_cannot_approve(self):
        self.submit()
        pkg = self.db.execute(select(ArchivePackage)).scalars().one()
        r = self.client(STUDENT_A).post(f"/api/v1/archive-packages/{pkg.id}/approve",
                                       json={"comment": "我自己批"})
        print(f"[approve] 学生越权审批: HTTP={r.status_code} code={body_all(r)['code']}")
        self.assertEqual(r.status_code, 403)
        self.db.expire_all()
        self.assertEqual(self.db.get(ArchivePackage, pkg.id).status, "PENDING")

    def test_admin_approve_archives_and_completes_progress(self):
        self.submit()
        pkg = self.db.execute(select(ArchivePackage)).scalars().one()
        process = self.db.get(GraduationProcess, 1)
        self.assertIsNone(process.completed_at, "归档前的 completed_at 应为空")
        payload = body_all(approve_package(pkg.id, body=ArchiveApprove(comment="材料齐全, 同意归档"),
                                          user=ADMIN, db=self.db))
        self.db.expire_all()
        pkg = self.db.get(ArchivePackage, pkg.id)
        process = self.db.get(GraduationProcess, 1)
        print(f"[approve] message={payload['message']} status={pkg.status} archived_at={pkg.archived_at} "
              f"completed_at={process.completed_at} progress={process.progress_percent}")
        self.assertEqual(payload["message"], "归档已确认")
        self.assertEqual(pkg.status, "ARCHIVED")
        self.assertIsNotNone(pkg.archived_at)
        self.assertIsNotNone(process.completed_at, "approve 后必须 recompute, completed_at 由空变非空")
        self.assertEqual(process.progress_percent, 100)

    def test_approve_twice_is_40003(self):
        self.submit()
        pkg = self.db.execute(select(ArchivePackage)).scalars().one()
        approve_package(pkg.id, body=ArchiveApprove(), user=ADMIN, db=self.db)
        with self.assertRaises(BusinessException) as ctx:
            approve_package(pkg.id, body=ArchiveApprove(), user=ADMIN, db=self.db)
        print(f"[approve] 重复确认: code={ctx.exception.code} message={ctx.exception.message}")
        self.assertEqual(ctx.exception.code, 40003)

    def test_approve_missing_package(self):
        with self.assertRaises(ResourceNotFoundException):
            approve_package(999, body=ArchiveApprove(), user=ADMIN, db=self.db)


class StatusFilterTest(ArchiveBaseCase):
    """search_packages?status= 下推 SQL, total 与 items 同口径."""

    def setUp(self):
        super().setUp()
        for cid, sid, status in ((1, 1, "PENDING"), (2, 2, "ARCHIVED"), (3, 3, "REVOKED")):
            self.db.add(ArchivePackage(cycle_id=cid, result_id=1, title=f"成果{cid}", status=status,
                                       student_id=sid, teacher_id=1, created_by=5))
        self.db.commit()

    def test_status_filter_pushes_down(self):
        data = body_json(search_packages(status="PENDING", page=1, page_size=20, user=ADMIN, db=self.db))
        print(f"[search] status=PENDING total={data['total']} items={[i['status'] for i in data['items']]}")
        self.assertEqual(data["total"], len(data["items"]))
        self.assertEqual(data["total"], 1)
        self.assertTrue(all(i["status"] == "PENDING" for i in data["items"]))

    def test_no_status_returns_all(self):
        data = body_json(search_packages(page=1, page_size=20, user=ADMIN, db=self.db))
        print(f"[search] 无 status total={data['total']} items={[i['status'] for i in data['items']]}")
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["total"], len(data["items"]))


class MigrationTest(unittest.TestCase):
    """迁移 0004 静态校验(不跑真库): 语法正确 + 链路正确 + 回填先于 NOT NULL + downgrade 可逆."""

    def setUp(self):
        self.path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0004_archive_package_owner.py"

    def test_syntax_and_revision_chain(self):
        source = self.path.read_text(encoding="utf-8")
        tree = ast.parse(source)  # 语法必须能过
        values = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                values[node.target.id] = ast.literal_eval(node.value)
        print(f"[migration] revision={values.get('revision')} down_revision={values.get('down_revision')}")
        self.assertEqual(values["revision"], "0004_archive_package_owner")
        self.assertEqual(values["down_revision"], "0003_sys_file_business_owner")

    def test_backfill_before_not_null_and_reversible(self):
        source = self.path.read_text(encoding="utf-8")
        self.assertIn("UPDATE archive_package", source)
        self.assertLess(source.index("UPDATE archive_package"), source.index("nullable=False"),
                        "必须先回填再置 NOT NULL")
        self.assertIn("def downgrade", source)
        for column in ("student_id", "teacher_id"):
            self.assertIn(f"drop_column('archive_package', '{column}')", source)


def db_files(db: Session, file_id: int) -> list:
    return db.execute(select(ArchiveFile).where(ArchiveFile.file_id == file_id)).scalars().all()


def pkg_files(db: Session, package_id: int) -> list:
    return db.execute(select(ArchiveFile).where(ArchiveFile.package_id == package_id)).scalars().all()


if __name__ == "__main__":
    unittest.main()
