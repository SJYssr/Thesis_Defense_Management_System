"""精排层单测: 降级契约 / Noop 保序 / 注入假 reranker 后精排真正改变顺序.

不依赖真实模型文件:
- 缺模型、缺 tokenizer、坏模型文件都断言"降级不抛";
- "精排改变 top1" 用可控假 reranker 注入 (patch app.ai.rag.retriever.get_reranker)。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.rag import retriever as retriever_module
from app.ai.rag.bm25 import clear_cache
from app.ai.rag.retriever import HybridRetriever
from app.ai.rerank import factory as rerank_factory
from app.ai.rerank.factory import (
    NoopReranker,
    OnnxCrossEncoderReranker,
    get_reranker,
    reset_reranker_cache,
)
from app.ai.vector.store import vector_store
from app.core.config import settings
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocumentChunk

OWN = 21
MISSING_DIR = "/tmp/p10b-missing-rerank-model-dir"


class _FakeEmbedding:
    """单测不依赖任何真实向量服务(ollama / 云端 API): embed_one 只要形状对即可."""

    name = "fake-embedding"
    dim = 8

    def embed_one(self, text: str) -> list[float]:
        return [0.125] * self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.125] * self.dim for _ in texts]


class _KeywordReranker:
    """可控假精排: 按词面命中数打分, 用来验证"精排真的改变了顺序"."""

    name = "fake-keyword-reranker"
    available = True

    def __init__(self):
        self.calls: list[tuple] = []

    def rerank(self, query, texts, top_k=None):
        self.calls.append((query, list(texts), top_k))
        scored = sorted(
            ((i, float(t.count("答辩") * 2 + t.count("流程"))) for i, t in enumerate(texts)),
            key=lambda item: (-item[1], item[0]),
        )
        return scored[:top_k] if top_k is not None else scored


class RerankerContractTest(unittest.TestCase):
    """工厂与实现层契约(不涉及检索管线)."""

    def tearDown(self):
        reset_reranker_cache()

    def test_noop_is_unavailable_and_preserves_order(self):
        reranker = NoopReranker()
        self.assertFalse(reranker.available)
        self.assertEqual(reranker.name, "noop")
        ranked = reranker.rerank("答辩流程", ["a", "b", "c"])
        self.assertEqual([i for i, _ in ranked], [0, 1, 2], "Noop 必须保序")
        scores = [s for _, s in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True), "占位分仍须降序")
        self.assertEqual([i for i, _ in reranker.rerank("q", ["a", "b", "c"], top_k=2)], [0, 1])
        self.assertEqual(reranker.rerank("q", []), [])

    def test_provider_none_returns_noop(self):
        # P11: 精排绑定读数据库(ai_rerank_provider_id + ai_rerank_model), 单测直接钉住解析结果
        with mock.patch.object(rerank_factory, "rerank_setting",
                               return_value=("none", MISSING_DIR)):
            reranker = get_reranker()
        self.assertIsInstance(reranker, NoopReranker)
        self.assertFalse(reranker.available)
        self.assertEqual(reranker.reason, "disabled")

    def test_auto_without_model_dir_degrades_to_noop(self):
        with mock.patch.object(rerank_factory, "rerank_setting",
                               return_value=("local", MISSING_DIR)):
            reranker = get_reranker()
        self.assertFalse(reranker.available, "缺模型时 get_reranker().available 必须为 False")
        self.assertIn("model.onnx", reranker.reason)
        self.assertEqual([i for i, _ in reranker.rerank("答辩流程", ["x", "y"])], [0, 1])

    def test_onnx_provider_without_files_is_unavailable_and_does_not_raise(self):
        reranker = OnnxCrossEncoderReranker(MISSING_DIR)
        self.assertFalse(reranker.available)
        self.assertEqual(reranker.name, "onnx-cross-encoder")
        self.assertIn("model.onnx", reranker.reason)
        self.assertEqual([i for i, _ in reranker.rerank("答辩流程", ["x", "y"])], [0, 1])

    def test_missing_tokenizer_json_marks_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "model.onnx").write_bytes(b"not-a-real-onnx")
            reranker = OnnxCrossEncoderReranker(tmp)
            self.assertFalse(reranker.available)
            self.assertIn("tokenizer.json", reranker.reason)

    def test_broken_model_files_degrade_at_first_use(self):
        """文件存在但内容非法: 构造期 available=True, 首次推理降级且不抛."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "model.onnx").write_bytes(b"not-a-real-onnx")
            Path(tmp, "tokenizer.json").write_bytes(b'{"not":"a tokenizer"}')
            reranker = OnnxCrossEncoderReranker(tmp)
            self.assertTrue(reranker.available, "两个文件都在时构造期应视为可用(延迟加载)")
            ranked = reranker.rerank("答辩流程", ["甲", "乙", "丙"])
            self.assertEqual([i for i, _ in ranked], [0, 1, 2], "加载失败必须保序直通")
            self.assertFalse(reranker.available, "加载失败后本进程永久降级")
            self.assertTrue(reranker.reason.startswith("load-failed") or reranker.reason == "", reranker.reason)

    def test_get_reranker_is_cached_per_settings(self):
        with mock.patch.object(rerank_factory, "rerank_setting",
                               return_value=("none", MISSING_DIR)):
            first = get_reranker()
            self.assertIs(first, get_reranker(), "同配置必须复用实例")
        with mock.patch.object(rerank_factory, "rerank_setting",
                               return_value=("local", MISSING_DIR)):
            self.assertIsNot(first, get_reranker(), "配置变化后必须重建")


class RetrieveRerankPipelineTest(unittest.TestCase):
    """检索管线: 召回 → 融合 → 精排/降级."""

    def setUp(self):
        clear_cache()
        reset_reranker_cache()
        retriever_module._LOGGED_FALLBACK.clear()
        # 向量侧固定为假实现: 检索管线单测只考察融合/精排, 不得依赖 ollama 或云端 API
        self._embedding_patch = mock.patch.object(
            retriever_module.EmbeddingFactory, "get", return_value=_FakeEmbedding())
        self._embedding_patch.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._add(101, "系统采用 FastAPI 分层架构，前端为 Vue 单页应用。")
        self._add(102, "毕业论文答辩流程包括资格审查、现场陈述与成绩评定。")
        self._add(103, "预算预测模型采用线性回归实现。")
        self.retriever = HybridRetriever(self.db)
        self.vector_hits = [(101, 0.93), (102, 0.90), (103, 0.85)]

    def tearDown(self):
        self._embedding_patch.stop()
        self.db.close()
        self.engine.dispose()
        clear_cache()
        reset_reranker_cache()
        retriever_module._LOGGED_FALLBACK.clear()

    def _add(self, cid, content, student_id=OWN):
        self.db.add(AIDocumentChunk(
            id=cid, document_id=1, student_id=student_id,
            scope="STUDENT" if student_id else "GLOBAL", document_type="THESIS",
            chunk_index=cid, section_title=f"章节{cid}", content=content, token_count=len(content),
        ))
        self.db.commit()

    # ---------- 降级 ----------

    def test_retrieve_degrades_without_model_and_logs_once(self):
        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(rerank_factory, "rerank_setting",
                                  return_value=("local", MISSING_DIR)), \
                mock.patch.object(retriever_module.logger, "info") as info:
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
            self.assertEqual([r.chunk_id for r in results], [101, 102, 103], "降级必须保持 RRF 顺序")
            self.assertTrue(all(r.stage == "fallback" for r in results), "降级阶段标记为 fallback")
            self.assertTrue(all(r.rerank_score is None for r in results))

            self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
        self.assertEqual(info.call_count, 1, "降级原因只记一次, 不得每请求刷屏")

    def test_rerank_false_skips_model_even_if_available(self):
        fake = _KeywordReranker()
        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5, rerank=False)
        self.assertEqual([r.chunk_id for r in results], [101, 102, 103])
        self.assertTrue(all(r.stage == "fallback" for r in results))
        self.assertEqual(fake.calls, [], "rerank=False 不得调用精排模型")

    # ---------- 精排生效 ----------

    def test_injected_reranker_changes_top1_and_marks_stage(self):
        fake = _KeywordReranker()
        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
        self.assertEqual(results[0].chunk_id, 102, "精排后 top1 应是被 RRF 排到第 2 的答辩文档")
        self.assertTrue(all(r.stage == "reranked" for r in results))
        self.assertTrue(all(r.rerank_score is not None for r in results))
        self.assertGreaterEqual(results[0].rerank_score, results[-1].rerank_score)
        self.assertEqual(fake.calls[0][0], "答辩流程", "精排输入是(改写后的)查询原文")

    def test_rerank_keep_limits_returned_rows(self):
        fake = _KeywordReranker()
        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake), \
                mock.patch.object(settings, "rerank_keep", 2):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
        self.assertEqual(len(results), 2, "rerank_keep 生效")
        self.assertEqual(fake.calls[0][2], 2, "精排只需要 top_k=rerank_keep 条")

    def test_small_top_k_not_inflated_by_rerank_keep(self):
        fake = _KeywordReranker()
        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake), \
                mock.patch.object(settings, "rerank_keep", 6):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=2)
        self.assertEqual(len(results), 2, "limit = min(rerank_keep, top_k), 小 top_k 调用点不被放大")

    def test_keyword_strategy_also_goes_through_rerank(self):
        fake = _KeywordReranker()
        with mock.patch.object(vector_store, "search", return_value=[]), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[(103, 5.0), (102, 3.0)]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5, strategy="keyword")
        self.assertEqual(results[0].chunk_id, 102, "单路 keyword 也要过精排, 否则对照评测失真")
        self.assertTrue(all(r.stage == "reranked" for r in results))
        self.assertEqual(results[0].source, "keyword")

    def test_rerank_pool_is_after_d44_trim(self):
        """回表拿不到的 chunk(他人残留/已删)不进精排池 —— D44 裁剪必须保留."""
        self._add(999, "他人的答辩流程私有内容", student_id=99)
        fake = _KeywordReranker()
        hits = [(999, 0.99)] + self.vector_hits
        with mock.patch.object(vector_store, "search", return_value=hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=fake):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
        self.assertNotIn(999, [r.chunk_id for r in results])
        self.assertEqual(len(fake.calls[0][1]), 3, "精排池只含回表成功且属于本 scope 的 chunk")

    def test_reranker_exception_does_not_break_retrieve(self):
        class _Boom:
            name = "boom"
            available = True

            def rerank(self, *args, **kwargs):
                raise RuntimeError("模型炸了")

        with mock.patch.object(vector_store, "search", return_value=self.vector_hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=_Boom()):
            results = self.retriever.retrieve("答辩流程", OWN, None, top_k=5)
        self.assertEqual([r.chunk_id for r in results], [101, 102, 103], "精排异常退化为 RRF 顺序")
        self.assertTrue(all(r.stage == "fallback" for r in results))


class RerankComparisonTest(unittest.TestCase):
    """评测对照: 同一批 query 走 rerank=True/False, 只差精排开关."""

    def setUp(self):
        clear_cache()
        reset_reranker_cache()
        retriever_module._LOGGED_FALLBACK.clear()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._patch = mock.patch.object(retriever_module.EmbeddingFactory, "get",
                                        return_value=_FakeEmbedding())
        self._patch.start()
        for cid, content in ((101, "系统采用 FastAPI 分层架构，前端为 Vue 单页应用。"),
                             (103, "预算预测模型采用线性回归实现。"),
                             (102, "毕业论文答辩流程包括资格审查、现场陈述与成绩评定。")):
            self.db.add(AIDocumentChunk(
                id=cid, document_id=1, student_id=OWN, scope="STUDENT", document_type="THESIS",
                chunk_index=cid, section_title=f"S{cid}", content=content, token_count=len(content)))
        self.db.commit()

    def tearDown(self):
        self._patch.stop()
        self.db.close()
        self.engine.dispose()
        clear_cache()
        reset_reranker_cache()
        retriever_module._LOGGED_FALLBACK.clear()

    def test_compare_reports_delta_between_arms(self):
        from app.ai.rag.evaluation import compare_with_without_rerank, summarize_rerank_comparison

        # 召回池把相关 chunk 排在末位, K=1 时未精排必漏, 精排后必中
        hits = [(101, 0.93), (103, 0.85), (102, 0.80)]
        cases = [{"question": "答辩流程", "relevant": [102], "student_id": OWN}]
        with mock.patch.object(vector_store, "search", return_value=hits), \
                mock.patch.object(HybridRetriever, "keyword_search", return_value=[]), \
                mock.patch.object(retriever_module, "get_reranker", return_value=_KeywordReranker()):
            report = compare_with_without_rerank(cases, self.db, top_k=1)

        self.assertEqual(report["reranker"], "fake-keyword-reranker", "报告须如实写出实际精排器")
        self.assertTrue(report["reranker_available"])
        self.assertEqual(report["without_rerank"]["recall_at_k"], 0.0)
        self.assertEqual(report["with_rerank"]["recall_at_k"], 1.0)
        self.assertEqual(report["delta"]["recall_at_k"], 1.0)
        self.assertEqual(report["delta"]["mrr"], 1.0)
        self.assertEqual(report["without_rerank"]["per_case"][0]["retrieved"][0]["chunk_id"], 101)
        self.assertEqual(report["with_rerank"]["per_case"][0]["retrieved"][0]["chunk_id"], 102)
        self.assertIn("rerank=on", summarize_rerank_comparison(report))


if __name__ == "__main__":
    unittest.main()
