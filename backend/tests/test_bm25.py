"""BM25 关键词检索单测: jieba 分词 + rank_bm25 真倒排(替换旧的二元组重叠实现).

覆盖:
1. 中文整词命中 + 相关性排序("答辩流程" 命中含该词的 chunk, 且分数高于弱相关 chunk);
2. 词频单调性(TF 越高分越高);
3. scope 隔离(学生 A 的索引里没有学生 B 的 chunk);
4. document_types 过滤(白名单下推 SQL);
5. 缓存: 同参数连续 2 次 search 只建索引 1 次, 指纹变化后重建;
6. 边界: bm25_enabled=False / 空 query / 空语料 / 纯标点, 一律返回 [] 不抛。
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.rag.bm25 import BM25Index, clear_cache, tokenize
from app.core.config import settings
from app.db.base import Base
from app.models import all as _all_models  # noqa: F401
from app.models.ai import AIDocumentChunk

OWN, OTHER = 21, 99


class Bm25IndexTest(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        clear_cache()

    # ---------- 造数据 ----------

    def _add(self, cid: int, content: str, student_id: int | None = OWN,
             document_type: str = "THESIS") -> int:
        self.db.add(AIDocumentChunk(
            id=cid, document_id=1, student_id=student_id,
            scope="GLOBAL" if student_id is None else "STUDENT",
            document_type=document_type, chunk_index=cid,
            section_title=f"章节{cid}", content=content, token_count=len(content),
        ))
        self.db.commit()
        return cid

    def _corpus(self):
        """6 篇同学论文档: 让 "答辩" 的 IDF 为正(出现在 2/6 文档里)."""
        return [
            (1, "毕业论文答辩流程包括资格审查、现场陈述、教师提问与成绩评定五个环节。"),
            (2, "答辩委员会由三名专业教师组成，负责现场提问与评分。"),
            (3, "预算预测模型采用线性回归实现，误差控制在可接受范围。"),
            (4, "系统采用 FastAPI 分层架构，前端为 Vue 单页应用。"),
            (5, "数据库设计包含用户表、题目表与轮次表。"),
            (6, "开题报告需说明研究背景、技术路线与预期成果。"),
        ]

    # ---------- 1. 中文整词命中 ----------

    def test_chinese_query_hits_and_outranks_weak_match(self):
        for cid, text in self._corpus():
            self._add(cid, text)

        hits = BM25Index(self.db, OWN, None).search("答辩流程", top_k=10)
        ids = [cid for cid, _ in hits]
        scores = dict(hits)

        self.assertIn(1, ids, "含 '答辩流程' 的 chunk 必须命中")
        self.assertGreater(scores[1], 0.0, "BM25 原始分必须为正")
        if 2 in ids:
            self.assertGreater(scores[1], scores[2], "同时含词面 '流程' 的 chunk1 应排在只含 '答辩' 的 chunk2 之前")
        self.assertNotIn(3, ids, "完全不含查询词的 chunk 不进入结果")
        self.assertEqual(ids, sorted(ids, key=lambda cid: -scores[cid]), "必须按分数降序")

    # ---------- 1b. 小语料"公共词"回归(rank_bm25 负 IDF) ----------

    def test_common_terms_in_tiny_corpus_still_hit(self):
        """3 篇语料里查询词全都出现在 2/3 文档: rank_bm25 会给负 IDF, 不得因此丢命中."""
        self._add(71, "本系统的毕业设计状态机包含选题开题中期终期毕业材料五个阶段")
        self._add(72, "毕业设计状态机与阶段流转的通用规范说明", student_id=None)
        self._add(73, "无关内容 他人论文私有章节", student_id=OTHER)

        hits = BM25Index(self.db, OWN, None).search("毕业设计状态机 阶段", 10)
        ids = [cid for cid, _ in hits]
        self.assertIn(71, ids, "本人 chunk 必须命中")
        self.assertIn(72, ids, "D43: 全局 chunk 在关键词路径同样必须命中")
        self.assertNotIn(73, ids)
        self.assertTrue(all(score > 0 for _, score in hits), "分数必须为正(负 IDF 已钳到正下限)")

    # ---------- 2. 词频单调性 ----------

    def test_higher_term_frequency_scores_higher(self):
        self._add(11, "答辩环节、答辩材料、答辩记录、答辩成绩，构成本次毕业设计成果。")
        self._add(12, "答辩环节，构成本次毕业设计成果。")
        self._add(13, "预算预测模型采用线性回归实现，误差控制在可接受范围。")
        self._add(14, "系统采用 FastAPI 分层架构，前端为 Vue 单页应用。")
        self._add(15, "数据库设计包含用户表、题目表与轮次表。")
        self._add(16, "开题报告需说明研究背景、技术路线与预期成果。")

        scores = dict(BM25Index(self.db, OWN, None).search("答辩", top_k=10))
        self.assertIn(11, scores)
        self.assertIn(12, scores)
        self.assertGreater(scores[11], scores[12], "同一词出现 4 次的 chunk 分数必须高于出现 1 次的")

    # ---------- 3. scope 隔离 ----------

    def test_scope_isolation_excludes_other_student_chunks(self):
        self._add(21, "我的答辩流程包括资格审查与现场陈述。", student_id=OWN)
        self._add(22, "全局答辩流程规范：迟到十分钟视为缺席。", student_id=None)
        self._add(23, "他人的答辩流程私有论文内容。", student_id=OTHER)
        for cid, text in ((24, "预算预测模型采用线性回归实现。"), (25, "系统采用 FastAPI 分层架构。"),
                          (26, "数据库设计包含用户表与轮次表。")):
            self._add(cid, text)

        index = BM25Index(self.db, OWN, None)
        ids = [cid for cid, _ in index.search("答辩流程", top_k=10)]
        self.assertIn(21, ids, "本人 chunk 命中")
        self.assertIn(22, ids, "D43: 全局 chunk 必须命中")
        self.assertNotIn(23, ids, "他人私有 chunk 不得出现在学生 A 的索引里")
        self.assertNotIn(23, index._get_entry().chunk_ids, "索引物理上就不该包含他人 chunk")

    # ---------- 4. document_types 过滤 ----------

    def test_document_types_filter_applies(self):
        self._add(31, "答辩流程包括资格审查与现场陈述。", document_type="THESIS")
        self._add(32, "答辩流程规范：迟到十分钟视为缺席。", document_type="OPENING_REPORT")
        for cid in (33, 34, 35, 36):
            self._add(cid, f"无关文档{cid}：预算预测模型与系统架构说明。")

        only_thesis = [cid for cid, _ in BM25Index(self.db, OWN, ["THESIS"]).search("答辩流程", 10)]
        self.assertEqual(only_thesis, [31])

        both = [cid for cid, _ in BM25Index(self.db, OWN, ["THESIS", "OPENING_REPORT"]).search("答辩流程", 10)]
        self.assertEqual(sorted(both), [31, 32])

        # 白名单外的类型值被丢弃(safe_document_types), 不报错、不注入
        injected = [cid for cid, _ in BM25Index(self.db, OWN, ["THESIS}}"]).search("答辩流程", 10)]
        self.assertEqual(sorted(injected), [31, 32], "非法类型被丢弃后等同不过滤")

    # ---------- 5. 缓存 ----------

    def test_cache_builds_once_until_fingerprint_changes(self):
        self._add(41, "毕业论文答辩流程包括资格审查与现场陈述。")
        self._add(42, "预算预测模型采用线性回归实现。")

        calls = []
        original = BM25Index._load_rows

        def counting_load_rows(index_self, conds):
            calls.append(1)
            return original(index_self, conds)

        with mock.patch.object(BM25Index, "_load_rows", counting_load_rows):
            first = BM25Index(self.db, OWN, None).search("答辩流程", 5)
            second = BM25Index(self.db, OWN, None).search("答辩流程", 5)  # 新实例, 进程级缓存命中
            self.assertEqual(first, second, "缓存命中结果必须一致")
            self.assertEqual(len(calls), 1, "同参数连续 2 次 search 只允许 1 次建索引取数")

            self._add(43, "开题报告需说明研究背景与技术路线。")  # 指纹 (2, 42) -> (3, 43)
            BM25Index(self.db, OWN, None).search("答辩流程", 5)
            self.assertEqual(len(calls), 2, "指纹变化后必须重建并重新取数")

    # ---------- 6. 边界 ----------

    def test_disabled_returns_empty(self):
        self._add(51, "毕业论文答辩流程包括资格审查与现场陈述。")
        with mock.patch.object(settings, "bm25_enabled", False):
            self.assertEqual(BM25Index(self.db, OWN, None).search("答辩流程", 5), [])

    def test_empty_query_and_empty_corpus_do_not_raise(self):
        self.assertEqual(BM25Index(self.db, OWN, None).search("", 5), [], "空 query")
        self.assertEqual(BM25Index(self.db, OWN, None).search("   ", 5), [], "全空白 query")
        self.assertEqual(BM25Index(self.db, 12345, None).search("答辩流程", 5), [], "空语料")
        self._add(61, "毕业论文答辩流程包括资格审查与现场陈述。")
        self.assertEqual(BM25Index(self.db, OWN, None).search("？！，。", 5), [], "纯标点 query")
        self.assertEqual(BM25Index(self.db, OWN, None).search("答辩流程", 0), [], "top_k <= 0")

    def test_tokenize_contract(self):
        tokens = tokenize("答辩流程")
        self.assertIn("答辩", tokens)
        self.assertIn("流程", tokens)
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("，。！？"), [])


if __name__ == "__main__":
    unittest.main()
