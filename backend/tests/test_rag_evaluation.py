"""RAG 评测指标与相关性解析单测(roadmap 九十一章)."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ai.rag.evaluation import (
    AIDocumentChunk_student_filter,
    _resolve_relevant,
    reciprocal_rank,
    recall_at_k,
)
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocumentChunk


class MetricTest(unittest.TestCase):
    def test_recall_at_k(self):
        relevant = {1, 2, 3}
        self.assertEqual(recall_at_k([1, 2, 9, 4], relevant, 4), 2 / 3)
        self.assertEqual(recall_at_k([1, 2, 3], relevant, 5), 1.0)
        self.assertEqual(recall_at_k([9, 1], relevant, 1), 0.0, "只看 top-1")
        self.assertEqual(recall_at_k([1, 2], relevant, 2), 2 / 3)
        self.assertEqual(recall_at_k([1], set(), 5), 0.0, "无相关集返回 0")

    def test_reciprocal_rank(self):
        relevant = {7, 9}
        self.assertEqual(reciprocal_rank([3, 7, 5], relevant), 0.5)
        self.assertEqual(reciprocal_rank([9, 7], relevant), 1.0)
        self.assertEqual(reciprocal_rank([1, 2, 3], relevant), 0.0, "无命中")


class RelevantResolveTest(unittest.TestCase):
    def test_resolve_by_section_and_content(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([
                AIDocumentChunk(id=1, document_id=1, student_id=21, scope="STUDENT",
                                document_type="THESIS", chunk_index=0,
                                section_title="3.2 毕业设计状态机", content="选题→开题→中期→终期→毕业材料 五阶段"),
                AIDocumentChunk(id=2, document_id=1, student_id=21, scope="STUDENT",
                                document_type="THESIS", chunk_index=1,
                                section_title="3.3 数据库设计", content="核心表包括 sys_user 用户表"),
                AIDocumentChunk(id=3, document_id=2, student_id=None, scope="GLOBAL",
                                document_type="DEFENSE_RULE", chunk_index=0,
                                section_title="答辩纪律", content="迟到 10 分钟视为缺席"),
            ])
            db.flush()

            by_section = _resolve_relevant(db, 21, {"expected_sections": ["3.2 毕业设计状态机"]})
            self.assertEqual(by_section, {1})

            by_content = _resolve_relevant(db, 21, {"expected_content": ["sys_user 用户表"]})
            self.assertEqual(by_content, {2})

            explicit = _resolve_relevant(db, 21, {"expected_chunk_ids": [3]})
            self.assertEqual(explicit, {3}, "显式标注可跨 scope 指定")

            none = _resolve_relevant(db, 21, {"question": "无标注"})
            self.assertEqual(none, set())

    def test_student_filter_includes_global_chunks(self):
        cond = AIDocumentChunk_student_filter(21)
        self.assertIn("IN", str(cond))
        cond_global = AIDocumentChunk_student_filter(None)
        self.assertIn("IS NULL", str(cond_global))


if __name__ == "__main__":
    unittest.main()


class FtSearchParseTest(unittest.TestCase):
    """Redis 不同版本 FT.SEARCH 应答格式解析(修复: 扁平格式曾被静默丢弃)."""

    def test_flat_format(self):
        from app.ai.vector.store import _parse_ftsearch_rows

        rows = [2, b"rag:chunk:7", [b"__score", b"0.31", b"student_id", b"1"],
                b"rag:chunk:3", [b"__score", b"0.66", b"student_id", b"1"]]
        parsed = _parse_ftsearch_rows(rows)
        self.assertEqual([(k, f[1]) for k, f in parsed],
                         [(b"rag:chunk:7", b"0.31"), (b"rag:chunk:3", b"0.66")])

    def test_nested_format(self):
        from app.ai.vector.store import _parse_ftsearch_rows

        rows = [1, [b"rag:chunk:7", [b"__score", b"0.31"]]]
        parsed = _parse_ftsearch_rows(rows)
        self.assertEqual(parsed, [(b"rag:chunk:7", [b"__score", b"0.31"])])

    def test_empty(self):
        from app.ai.vector.store import _parse_ftsearch_rows

        self.assertEqual(_parse_ftsearch_rows([0]), [])
