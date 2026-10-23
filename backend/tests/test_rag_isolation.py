"""RAG 隔离/降级单测(D42/D43/D44/D45).

覆盖:
1. D42: 索引时无条件写 MySQL embedding 降级副本(即使当前走 Redis 模式);
2. D43: 全局知识库(student_id IS NULL)在 MySQL 降级路径与关键词路径都能命中;
3. D44: 向量命中他人 chunk 时, 回表 student 过滤必须把它挡掉(索引残留不串学生);
4. D45: 阶段报告按当前 cycle 取, 历史脏数据不再触发 MultipleResultsFound。
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.embedding.factory import EmbeddingFactory
from app.ai.rag.rag_service import RAGService
from app.ai.rag.retriever import HybridRetriever
from app.ai.vector.store import VectorStore, b64_to_vec, safe_document_types, vec_to_b64, vector_store
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocument, AIDocumentChunk
from app.models.business import GraduationCycle, OpeningReport

OWN, GLOBAL, OTHER = 21, None, 99

# splitter 会丢弃 len(text.strip()) < MIN_CHUNK_SIZE(50) 的 chunk, 因此造数据必须够长
FILLER = ("本报告围绕毕业设计答辩管理系统的边界可信性展开论述, 覆盖提示注入防护、检索隔离、"
          "向量降级可用与计费完整四个维度, 并给出可复现的验收证据。")

_AUTOINCREMENT_READY: set[type] = set()
_ID_SEQ: dict[type, int] = {}


def _enable_sqlite_autoincrement(model) -> None:
    """SQLite 下 BigInteger 主键不自增(仅 MySQL 侧 AUTO_INCREMENT), 这里补上 MySQL 语义.

    被测服务代码在 mysql 里允许不传 id 建 AIDocument, 测试要跑在内存库上就必须还原这一点。
    """
    if model in _AUTOINCREMENT_READY:
        return
    _AUTOINCREMENT_READY.add(model)

    @event.listens_for(model, "before_insert")
    def _assign_pk(mapper, connection, target):  # noqa: ANN001
        if getattr(target, "id", None) is None:
            current = connection.execute(select(func.coalesce(func.max(model.id), 0))).scalar()
            # 同一次 flush 里的多行在 DB 侧还看不到彼此, 用内存计数器兜住
            nxt = max(int(current or 0), _ID_SEQ.get(model, 0)) + 1
            _ID_SEQ[model] = nxt
            target.id = nxt


class RagIsolationTest(unittest.TestCase):
    def setUp(self):
        # StaticPool: 让 _search_mysql 内部新建的 Session 与测试共用同一条内存库连接
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        _enable_sqlite_autoincrement(AIDocument)
        _enable_sqlite_autoincrement(AIDocumentChunk)
        self.SessionFactory = sessionmaker(bind=self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ---------- 造数据 ----------

    def _add_chunk(self, cid: int, student_id: int | None, content: str, embedding: list[float] | None = None):
        emb = embedding or EmbeddingFactory.get().embed_one(content)
        row = AIDocumentChunk(id=cid, document_id=1, student_id=student_id, topic_id=None,
                              scope="GLOBAL" if student_id is None else "STUDENT",
                              document_type="THESIS" if student_id is not None else "DEFENSE_RULE",
                              chunk_index=cid, section_title=f"章节{cid}", content=content,
                              token_count=len(content), embedding=vec_to_b64(emb))
        self.db.add(row)
        self.db.commit()
        return emb

    # ---------- D43 ----------

    def test_global_chunk_retrievable_in_mysql_fallback(self):
        """全局 chunk(student_id IS NULL) 必须在 MySQL 降级路径被检索到."""
        emb = EmbeddingFactory.get().embed_one("毕业设计状态机包含选题开题中期终期四个阶段")
        self._add_chunk(1, OWN, "本系统状态机包含选题开题中期终期四个阶段", emb)
        self._add_chunk(2, GLOBAL, "毕业设计答辩通用规范 迟到十分钟视为缺席", emb)
        self._add_chunk(3, OTHER, "他人私有论文 预算预测模型", emb)

        with mock.patch.object(VectorStore, "_probe_redis", return_value=False), \
                mock.patch("app.db.mysql.SessionLocal", self.SessionFactory):
            hits = [cid for cid, _ in vector_store.search(emb, OWN, None, top_k=5)]

        self.assertIn(1, hits, "本人 chunk 应命中")
        self.assertIn(2, hits, "D43: 全局 chunk 必须在降级路径命中")
        self.assertNotIn(3, hits, "他人 chunk 必须被过滤")

    def test_keyword_search_includes_global_chunk(self):
        self._add_chunk(1, OWN, "本系统的毕业设计状态机包含选题开题中期终期毕业材料五个阶段")
        self._add_chunk(2, GLOBAL, "毕业设计状态机与阶段流转的通用规范说明")
        self._add_chunk(3, OTHER, "无关内容 他人论文私有章节")

        retriever = HybridRetriever(self.db)
        hits = [cid for cid, _ in retriever.keyword_search("毕业设计状态机 阶段", OWN, None, top_k=10)]

        self.assertIn(2, hits, "D43: 关键词路径同样要覆盖全局 chunk")
        self.assertNotIn(3, hits)

    # ---------- D44 ----------

    def test_other_students_chunk_never_leaks_on_vector_hit(self):
        self._add_chunk(1, OWN, "自己的论文 状态机设计说明")
        self._add_chunk(2, OTHER, "他人的私有论文 预算预测模型机密内容")

        retriever = HybridRetriever(self.db)
        with mock.patch.object(vector_store, "search", return_value=[(2, 0.91)]), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]):
            results = retriever.retrieve("状态机", OWN, None, top_k=5)

        self.assertEqual(results, [], "D44: 他人 chunk 的向量命中必须被回表过滤")

    def test_ranked_trimmed_to_rows_actually_fetched(self):
        self._add_chunk(1, OWN, "自己的论文 状态机设计说明")
        self._add_chunk(2, OTHER, "他人的私有论文 预算预测模型")

        retriever = HybridRetriever(self.db)
        with mock.patch.object(vector_store, "search", return_value=[(1, 0.9), (2, 0.5)]), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]):
            results = retriever.retrieve("状态机", OWN, None, top_k=5)

        self.assertEqual([r.chunk_id for r in results], [1], "ranked 必须裁剪为实际取到的行")
        self.assertTrue(all(r.chunk_id != 2 for r in results))

    # ---------- D42 ----------

    def test_index_always_writes_mysql_embedding_copy(self):
        doc = AIDocument(id=1, student_id=OWN, document_type="THESIS", document_name="毕业论文",
                         scope="STUDENT", index_status="WAITING", version=1,
                         content_text=f"# 第一章 系统概述\n本系统采用分层架构实现答辩管理, {FILLER}\n\n"
                                      f"# 第二章 数据库设计\n核心表包括用户表、题目表与答辩轮次表, {FILLER}")
        self.db.add(doc)
        self.db.commit()

        stub = mock.Mock()
        stub.mode = "redis"  # 模拟当前走 Redis 模式
        with mock.patch("app.ai.rag.rag_service.vector_store", stub):
            RAGService(self.db).index_document(doc.id)

        rows = self.db.execute(
            select(AIDocumentChunk).where(AIDocumentChunk.document_id == doc.id)
        ).scalars().all()
        self.assertTrue(rows, "文档应被切分为若干 chunk")
        for row in rows:
            self.assertIsNotNone(row.embedding, "D42: Redis 模式下也必须写 MySQL 降级副本")
            self.assertGreater(len(b64_to_vec(row.embedding)), 0)
        stub.upsert_chunk.assert_called()

    # ---------- D45 ----------

    _next_id = 100

    def _opening(self, student_id: int, cycle_id: int | None, title: str) -> OpeningReport:
        type(self)._next_id += 1  # BigInteger 主键在 SQLite 下不自增, 显式赋值
        return OpeningReport(id=type(self)._next_id, student_id=student_id, cycle_id=cycle_id,
                             topic_id=1, teacher_id=1, title=title,
                             research_background=f"{title}的背景说明。{FILLER}",
                             research_content=f"{title}的研究内容。{FILLER}",
                             technical_route=f"{title}的技术路线。{FILLER}",
                             status="APPROVED")

    def test_stage_reports_filtered_by_active_cycle(self):
        cycle = GraduationCycle(id=1, academic_year="2024-2025", student_id=OWN, status="ACTIVE")
        self.db.add(cycle)
        self.db.flush()

        self.db.add_all([
            self._opening(OWN, None, "旧学年开题报告"),          # 历史脏数据: cycle_id 为空
            self._opening(OWN, cycle.id, "当前学年开题报告"),
        ])
        self.db.commit()

        stub = mock.Mock()
        stub.mode = "mysql"
        with mock.patch("app.ai.rag.rag_service.vector_store", stub):
            RAGService(self.db).index_stage_reports(OWN)  # 旧实现: MultipleResultsFound

        docs = self.db.execute(
            select(AIDocument).where(AIDocument.document_type == "OPENING_REPORT")
        ).scalars().all()
        self.assertEqual(len(docs), 1, "只索引当前 cycle 的报告")
        self.assertIn("当前学年开题报告", docs[0].content_text or "")

    def test_stage_reports_without_active_cycle_does_not_raise(self):
        """无 ACTIVE cycle 且存在多条同学年 APPROVED(cycle_id 为 NULL)时不得 500."""
        self.db.add_all([self._opening(OWN, None, "第一份开题"), self._opening(OWN, None, "第二份开题")])
        self.db.commit()

        stub = mock.Mock()
        stub.mode = "mysql"
        with mock.patch("app.ai.rag.rag_service.vector_store", stub):
            RAGService(self.db).index_stage_reports(OWN)  # 不得抛 MultipleResultsFound

        docs = self.db.execute(
            select(AIDocument).where(AIDocument.document_type == "OPENING_REPORT")
        ).scalars().all()
        self.assertEqual(len(docs), 1)
        self.assertIn("第二份开题", docs[0].content_text or "", "取 id 最大的一条, 不静默 500")

    # ---------- D50(检索侧白名单) ----------

    def test_document_type_tag_whitelist(self):
        self.assertEqual(safe_document_types(["THESIS"]), ["THESIS"])
        self.assertEqual(safe_document_types(["THESIS", "THESIS"]), ["THESIS"])
        self.assertEqual(safe_document_types(["THESIS}}) @content:*"]), [])
        self.assertEqual(safe_document_types(["{THESIS}"]), [])
        self.assertEqual(safe_document_types(None), [])


if __name__ == "__main__":
    unittest.main()
