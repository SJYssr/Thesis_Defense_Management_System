"""模拟答辩只读取当前学生本期的材料，源码按 ZIP 内的文件与行号引用。"""
import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.document.source_archive import parse_source_archive
from app.ai.embedding.factory import LocalHashEmbedding
from app.ai.rag.rag_service import RAGService
from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import GraduationCycle, GraduationMaterialFile
from app.models.system import SysFile
from app.modules.ai.router import list_documents
from app.modules.material import router as materials
from tests.test_material_version import STUDENT, new_engine, seed


def source_zip() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as z:
        z.writestr("src/allocator.py", "def reserve_last_slot(capacity, booked):\n    return booked < capacity\n"
                   'api_key = "private-literal"\n')
        for name in ("node_modules/vendor.js", ".env", ".git/dev.py", "../outside.py",
                     "src/credentials.py", "src/generated/parser.ts", "src/build/bundle.js", "secrets.py"):
            z.writestr(name, "sensitive excluded file")
        link = ZipInfo("src/linked.py")
        link.external_attr = 0o120777 << 16
        z.writestr(link, "sensitive excluded file")
    return stream.getvalue()


class PracticeSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "project.zip"
        self.path.write_bytes(source_zip())
        self.engine = new_engine()
        self.addCleanup(self.engine.dispose)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        seed(self.db)
        self.db.commit()
        self.rag = RAGService(self.db)
        for obj, name, value in (
            (materials.storage, "abs_path", self.path),
            (materials, "read_upload", (self.path.read_bytes(), "project.zip", "application/zip", self.path.stat().st_size, "a" * 64)),
            (materials.storage, "save", ("project.zip", str(self.path))),
        ):
            stub = patch.object(obj, name, return_value=value)
            stub.start()
            self.addCleanup(stub.stop)
        for stub in (patch("app.ai.rag.rag_service.EmbeddingFactory.get", return_value=LocalHashEmbedding(16)),
                     patch("app.ai.rag.rag_service.vector_store"), patch("app.ai.health.mark_embedding_indexed")):
            stub.start()
            self.addCleanup(stub.stop)

    def upload(self):
        bg = BackgroundTasks()
        response = materials.upload_material_file(file=None, material_type="SOURCE_CODE", business_type=None,
                                                  business_id=None, user=STUDENT, db=self.db, background_tasks=bg)
        self.assertEqual(json.loads(response.body)["code"], 0)
        self.assertEqual(len(bg.tasks), 1)
        return self.db.get(AIDocument, bg.tasks[0].args[0])

    def test_source_filters_and_line_location(self):
        sections = parse_source_archive(str(self.path))
        self.assertEqual([s.title for s in sections], ["src/allocator.py L1–3"])
        self.assertIn("return booked < capacity", sections[0].content)
        self.assertNotIn("private-literal", sections[0].content)
        with ZipFile(self.path, "w") as z:
            z.writestr("images/binary.png", b"\0" * 80)
        with self.assertRaisesRegex(ValueError, "没有可分析"):
            parse_source_archive(str(self.path))

    def test_backfill_retry_and_citation_invalidation(self):
        doc = self.upload()
        self.db.delete(doc)
        self.db.commit()
        sync = self.rag.index_stage_reports(1)
        self.assertEqual(sync["indexed"], 1)
        self.assertEqual(sync["failed_document_ids"], [])
        found, = self.rag.practice_documents(1)
        self.assertEqual((found.document_type, found.cycle_id), ("SOURCE_CODE", 1))
        chunk_id = self.db.scalar(select(AIDocumentChunk.id).where(AIDocumentChunk.document_id == found.id))
        self.assertEqual(self.rag.practice_chunks(1, [chunk_id])[0].extra["document_name"], "project.zip")
        self.assertEqual(self.rag.practice_chunks(2, [chunk_id]), [])
        self.assertEqual(self.rag.index_stage_reports(1)["indexed"], 0)
        self.db.delete(self.db.scalar(select(GraduationMaterialFile)))
        self.db.commit()
        self.assertEqual(self.rag.practice_chunks(1, [chunk_id]), [])

    def test_practice_only_upload_checks_file_owner_cycle_and_formal_use(self):
        doc = self.upload()
        self.rag.index_document(doc.id)
        sf = self.db.get(SysFile, doc.file_id)
        sf.business_type, sf.business_id = "AI_PRACTICE", 1
        self.db.commit()

        def listing():
            response = list_documents(page=1, size=20, user=STUDENT, db=self.db)
            return json.loads(response.body)["data"]["items"][0]

        # A formal material attachment cannot be deleted using the practice-only controls.
        self.assertFalse(listing()["practice_upload"])
        self.db.delete(self.db.scalar(select(GraduationMaterialFile)))
        self.db.commit()
        self.assertTrue(listing()["practice_upload"])
        self.assertTrue(listing()["practice_eligible"])
        sf.uploader_id = STUDENT.id + 1
        self.db.commit()
        self.assertFalse(listing()["practice_upload"])
        self.assertFalse(listing()["practice_eligible"])
        sf.uploader_id, sf.business_id = STUDENT.id, 777
        self.db.commit()
        self.assertFalse(listing()["practice_eligible"])

    def test_failed_index_retry_does_not_claim_success(self):
        doc = self.upload()
        with patch("app.ai.rag.rag_service.EmbeddingFactory.get") as factory:
            factory.return_value.embed.return_value = []
            result = self.rag.index_stage_reports(1)
        self.assertEqual(result["failed_document_ids"], [doc.id])
        self.assertEqual(doc.index_status, "FAILED")
        self.assertEqual(self.rag.practice_documents(1), [])
        self.assertEqual(self.rag.index_stage_reports(1)["indexed"], 1)
        self.assertEqual(self.db.scalar(select(func.count(AIDocument.id))), 1)

    def test_cross_cycle_same_topic_and_global_candidates_excluded(self):
        doc = self.upload()
        self.rag.index_document(doc.id)
        chunk_id = self.db.scalar(select(AIDocumentChunk.id))
        for i, (sid, scope) in enumerate(((1, "STUDENT"), (2, "STUDENT"), (None, "GLOBAL")), 10):
            self.db.add(AIDocument(id=i, cycle_id=None, student_id=sid, topic_id=1, document_type="THESIS",
                                   document_name="额外文档", index_status="SUCCESS", scope=scope, chunk_count=1))
            self.db.flush()
            self.db.add(AIDocumentChunk(id=i + 100, document_id=i, student_id=sid, document_type="THESIS",
                                        scope=scope, chunk_index=0, content="其他来源"))
        self.db.commit()
        with patch("app.ai.rag.retriever.vector_store.search", return_value=[(110, 1), (111, 1), (112, 1), (chunk_id, .5)]) as vector, \
                patch("app.ai.rag.retriever.HybridRetriever.keyword_search", return_value=[]), \
                patch("app.ai.rag.retriever.HybridRetriever._apply_rerank", return_value=None):
            hits = self.rag.retrieve_practice("分配", 1)
        self.assertEqual(vector.call_args.kwargs["document_ids"], [doc.id])
        self.assertEqual([hit.chunk_id for hit in hits], [chunk_id])
        self.assertEqual(self.rag.retrieve_practice("分配", 1, document_ids=[111]), [])
        # Same student and topic, later academic year, current material is no longer eligible.
        self.db.get(GraduationCycle, 1).status = "COMPLETED"
        self.db.add(GraduationCycle(id=2, academic_year="2027-2028", student_id=1, topic_id=1, status="ACTIVE"))
        self.db.commit()
        self.assertEqual(self.rag.practice_chunks(1, [chunk_id]), [])
