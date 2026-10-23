"""D40 论文重传版本一致性 + D30(#6) 材料审核学年归属 回归用例.

D40: 每次重传论文都新建 AIDocument, 旧文档及其 chunk/向量必须作废, 否则 RAG 同时召回新旧两版。
#6 : 审核材料时 on_stage_approved / recompute 必须传本记录自己的 cycle_id, 跨学年不得写错流程实例。
"""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer, create_engine, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.vector.store import vector_store
from app.core.exceptions import BusinessException, register_exception_handlers
from app.core.permissions import CurrentUser, require_teacher
from app.db.mysql import get_db
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import (
    FinalDefense, GraduationCycle, GraduationMaterial, GraduationProcess, GraduationTopic,
    TopicSelection,
)
from app.models.system import ReviewRecord
from app.models.user import College, Major, StudentProfile, SysUser, TeacherProfile
from app.modules.material import router as material_router
from app.modules.material.router import (
    ReviewRequest, _review_material, delete_material_file, upload_material_file,
)

STUDENT = CurrentUser(id=2, username="s001", real_name="学生甲", role="STUDENT", profile_id=1)
TEACHER = CurrentUser(id=1, username="t001", real_name="导师甲", role="TEACHER", profile_id=1)


def new_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if isinstance(column.type, BigInteger):
                column.type = Integer()
    Base.metadata.create_all(engine)
    return engine


def seed(db: Session) -> None:
    db.add(College(id=1, name="计算机学院", code="CS"))
    db.flush()
    db.add(Major(id=1, college_id=1, name="软件工程", code="SE"))
    db.add(SysUser(id=1, username="t001", real_name="导师甲", password_hash="x", role="TEACHER"))
    db.add(SysUser(id=2, username="s001", real_name="学生甲", password_hash="x", role="STUDENT"))
    db.flush()
    db.add(TeacherProfile(id=1, user_id=1, teacher_no="T001"))
    db.add(StudentProfile(id=1, user_id=2, student_no="2022001", college_id=1, major_id=1))
    db.add(GraduationTopic(id=1, title="毕业设计题目", teacher_id=1, max_students=1, status="PUBLISHED"))
    db.flush()
    db.add(GraduationCycle(id=1, academic_year="2026-2027", student_id=1, topic_id=1,
                           teacher_id=1, status="ACTIVE"))
    db.add(TopicSelection(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                          status="APPROVED", active_flag=1))
    db.add(FinalDefense(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1, status="APPROVED"))
    db.flush()


def add_chunks(db: Session, doc_id: int, count: int = 2) -> list[int]:
    ids = []
    for index in range(count):
        chunk = AIDocumentChunk(document_id=doc_id, student_id=1, topic_id=1, scope="STUDENT",
                               document_type="THESIS", chunk_index=index,
                               content=f"论文段落 {index}", vector_key=f"vec:{doc_id}:{index}")
        db.add(chunk)
        db.flush()
        ids.append(chunk.id)
    return ids


class ThesisVersionTest(unittest.TestCase):
    """D40: 连续上传两次论文, AIDocument 只剩一条有效记录, chunk 不重复, 旧 chunk id 不在库里."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        self.db.commit()
        self.deleted_chunks: list[list[int]] = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _upload(self, name="论文.pdf", size=1024):
        with patch.object(material_router, "read_upload",
                          return_value=(b"%PDF-1.4", name, "application/pdf", size, "a" * 64)), \
             patch.object(material_router.storage, "save",
                          return_value=(f"stored_{name}", f"2026/06/{name}")), \
             patch.object(vector_store, "delete_chunks",
                          side_effect=lambda ids: self.deleted_chunks.append(list(ids))):
            return upload_material_file(file=None, material_type="THESIS", user=STUDENT, db=self.db)

    def _docs(self):
        return self.db.execute(select(AIDocument).where(AIDocument.document_type == "THESIS")).scalars().all()

    def _chunk_ids(self):
        return list(self.db.execute(select(AIDocumentChunk.id)).scalars().all())

    def test_reupload_supersedes_previous_thesis(self):
        self._upload("论文v1.pdf")
        docs = self._docs()
        self.assertEqual(len(docs), 1)
        first_doc = docs[0]
        self.assertEqual(first_doc.version, 1)
        old_chunks = add_chunks(self.db, first_doc.id, 2)
        self.db.commit()

        self._upload("论文v2.pdf")
        self.db.expire_all()
        docs = self._docs()
        self.assertEqual(len(docs), 1, "重传后只能有一条有效论文 AIDocument")
        self.assertEqual(docs[0].version, 2, "版本号必须递增")
        self.assertEqual(docs[0].document_name, "论文v2.pdf")
        self.assertNotEqual(docs[0].file_id, first_doc.file_id, "有效文档必须指向最新一版文件")

        remaining = self._chunk_ids()
        self.assertEqual(remaining, [], "旧版 chunk 必须被删除")
        for chunk_id in old_chunks:
            self.assertEqual(
                self.db.execute(select(func.count(AIDocumentChunk.id))
                                .where(AIDocumentChunk.id == chunk_id)).scalar(), 0,
                f"旧 chunk {chunk_id} 不得残留")
        self.assertEqual(self.deleted_chunks, [old_chunks], "旧 chunk 的向量必须显式清理")
        print(f"[D40] 两次上传后: AIDocument={len(docs)} 条(version={docs[0].version}), "
              f"旧 chunk {old_chunks} 已删除, 向量清理调用={self.deleted_chunks}")

    def test_third_upload_keeps_single_document_and_no_duplicate_chunks(self):
        self._upload("v1.pdf")
        doc1 = self._docs()[0]
        add_chunks(self.db, doc1.id, 3)
        self.db.commit()
        self._upload("v2.pdf")
        doc2 = self._docs()[0]
        new_chunks = add_chunks(self.db, doc2.id, 3)
        self.db.commit()
        self._upload("v3.pdf")
        self.db.expire_all()

        docs = self._docs()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].version, 3)
        self.assertEqual(self._chunk_ids(), [], "第三版上传后 v2 的 chunk 也必须清空")
        self.assertEqual(self.deleted_chunks[-1], new_chunks)

    def test_deleting_thesis_file_purges_its_document(self):
        self._upload("v1.pdf")
        doc = self._docs()[0]
        chunks = add_chunks(self.db, doc.id, 1)
        self.db.commit()
        mf_id = self.db.execute(select(material_router.GraduationMaterialFile.id)).scalars().first()
        with patch.object(vector_store, "delete_chunks",
                          side_effect=lambda ids: self.deleted_chunks.append(list(ids))):
            delete_material_file(mf_id, user=STUDENT, db=self.db)
        self.db.expire_all()
        self.assertEqual(self._docs(), [])
        self.assertEqual(self._chunk_ids(), [])
        self.assertEqual(self.deleted_chunks, [chunks])

    def test_submit_goes_through_state_machine(self):
        self._upload("论文.pdf")
        body = material_router.submit_material(user=STUDENT, db=self.db)
        data = json.loads(body.body)["data"]
        m = self.db.get(GraduationMaterial, data["id"])
        self.assertEqual(m.status, "SUBMITTED")
        self.assertIsNotNone(m.submitted_at)
        self.db.expire_all()
        records = self.db.execute(select(ReviewRecord)).scalars().all()
        self.assertEqual([r.action for r in records], ["SUBMIT"], "提交留痕只能一条")

    def test_non_thesis_upload_does_not_touch_thesis_documents(self):
        self._upload("v1.pdf")
        doc = self._docs()[0]
        with patch.object(material_router, "read_upload",
                          return_value=(b"zip", "源码.zip", "application/zip", 2048, "b" * 64)), \
             patch.object(material_router.storage, "save", return_value=("stored.zip", "2026/06/源码.zip")):
            upload_material_file(file=None, material_type="SOURCE_CODE", user=STUDENT, db=self.db)
        self.db.expire_all()
        docs = self._docs()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].id, doc.id, "非论文材料不得作废论文文档")


class MaterialReviewCycleTest(unittest.TestCase):
    """#6: 审核材料必须按记录自己的 cycle_id 推进流程与进度."""

    def setUp(self):
        self.engine = new_engine()
        self.db = Session(self.engine)
        seed(self.db)
        # 学年 2 是当前 ACTIVE; 待审核材料属于学年 1(历史学年)
        self.db.add(GraduationCycle(id=2, academic_year="2027-2028", student_id=1, topic_id=1,
                                    teacher_id=1, status="ACTIVE"))
        self.db.execute(update(GraduationCycle)
                        .where(GraduationCycle.id == 1).values(status="COMPLETED"))
        self.db.add(GraduationMaterial(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                       status="SUBMITTED", submitted_at=datetime(2026, 6, 1)))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _processes(self):
        return self.db.execute(select(GraduationProcess)).scalars().all()

    def test_approve_writes_process_for_own_cycle_not_active_one(self):
        _review_material(self.db, 1, TEACHER, "approve", "通过")
        self.db.expire_all()
        self.assertEqual(self.db.get(GraduationMaterial, 1).status, "APPROVED")
        processes = self._processes()
        cycle_ids = [p.cycle_id for p in processes]
        self.assertEqual(cycle_ids, [1], "流程实例必须落在材料自己的学年(cycle 1), 不得写成当前 ACTIVE 学年")
        self.assertNotIn(2, cycle_ids)
        print(f"[#6] 审核后 GraduationProcess.cycle_id = {cycle_ids} (材料所属=1, 当时 ACTIVE=2)")

    def test_reject_also_uses_own_cycle(self):
        _review_material(self.db, 1, TEACHER, "reject", "需补充材料")
        self.db.expire_all()
        self.assertEqual(self.db.get(GraduationMaterial, 1).status, "REJECTED")
        self.assertEqual([p.cycle_id for p in self._processes()], [1])

    def test_review_writes_single_review_record_with_teacher_role(self):
        _review_material(self.db, 1, TEACHER, "approve", "通过")
        self.db.expire_all()
        records = self.db.execute(select(ReviewRecord)).scalars().all()
        self.assertEqual(len(records), 1, "留痕只能有一条(不得手写+transition 双记)")
        self.assertEqual(records[0].reviewer_role, "TEACHER")
        self.assertEqual(records[0].after_status, "APPROVED")

class MaterialDecisionTest(unittest.TestCase):
    """decision 三值显式语义(与 stages 模块 P1-A 对齐).

    旧契约靠 "comment 是否为空" 推断结论, 导致 request_revision 无法表达;
    新契约: decision 显式给出结论, 缺省时按端点语义走 approve / reject(向后兼容)。
    """

    def setUp(self):
        # HTTP 用例在同进程另一线程里跑, 内存库必须用 StaticPool 共享同一条连接
        self.engine = create_engine("sqlite+pysqlite:///:memory:",
                                    connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        for table in Base.metadata.tables.values():
            for column in table.primary_key.columns:
                if isinstance(column.type, BigInteger):
                    column.type = Integer()
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        seed(self.db)
        self.db.add(GraduationMaterial(id=1, student_id=1, cycle_id=1, topic_id=1, teacher_id=1,
                                       status="SUBMITTED", submitted_at=datetime(2026, 6, 1)))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _status(self) -> str:
        self.db.expire_all()
        return self.db.get(GraduationMaterial, 1).status

    def _post(self, action: str, payload: dict):
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(material_router.teacher_router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_teacher] = lambda: TEACHER
        client = TestClient(app, raise_server_exceptions=False)
        return client.post(f"/api/v1/teacher/materials/1/{action}", json=payload)

    def test_request_revision_lands_on_state_machine_action(self):
        _review_material(self.db, 1, TEACHER, "request_revision", "请补充查重报告后重新提交")
        self.assertEqual(self._status(), "RESUBMIT_REQUIRED", "request_revision 必须落到 MATERIAL 转移")
        records = self.db.execute(select(ReviewRecord)).scalars().all()
        self.assertEqual([r.after_status for r in records], ["RESUBMIT_REQUIRED"])
        print(f"[decision] request_revision -> {self._status()}")

    def test_approve_endpoint_defaults_to_approve_without_decision(self):
        resp = self._post("approve", {"comment": "通过"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(), "APPROVED", "不传 decision 时 approve 端点语义仍是通过")

    def test_reject_endpoint_defaults_to_reject_without_decision(self):
        resp = self._post("reject", {"comment": "材料不全"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(), "REJECTED", "不传 decision 时 reject 端点语义仍是驳回")

    def test_request_revision_must_be_explicit(self):
        resp = self._post("reject", {"decision": "request_revision", "comment": "补充封面与目录"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(), "RESUBMIT_REQUIRED",
                         "只有显式传 decision=request_revision 才进入 RESUBMIT_REQUIRED")
        print(f"[decision] reject 端点 + 显式 request_revision -> {self._status()}")

    def test_invalid_decision_rejected_by_request_schema(self):
        resp = self._post("approve", {"decision": "maybe"})
        self.assertIn(resp.status_code, (400, 422), f"非法 decision 必须被拒绝, 实际 {resp.status_code}")
        self.assertNotEqual(self._status(), "APPROVED", "非法结论不得改变材料状态")
        print(f"[decision] 非法值 -> HTTP {resp.status_code}")

    def test_invalid_decision_rejected_by_core(self):
        with self.assertRaises(BusinessException) as ctx:
            _review_material(self.db, 1, TEACHER, "approve_typo", "通过")
        self.assertIn("审核结论非法", ctx.exception.message)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
