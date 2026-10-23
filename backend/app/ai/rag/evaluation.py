"""RAG 检索质量评测: Recall@K / MRR / 命中率(roadmap 九十一章).

用途:
1. 管理端 POST /ai/admin/rag/evaluate 在线评测;
2. backend/rag_experiment.py 离线跑三臂对比实验(vector / keyword / hybrid, 九十二章),
   结果可直接写入论文实验章节.

数据集格式(JSON):
{
  "name": "演示论文检索评测集",
  "student_no": "20230001",          # 评测目标学生(检索范围=其私有库+全局库)
  "document_types": ["THESIS"],      # 可选, 限定检索文档类型
  "top_k": 5,
  "questions": [
    {
      "question": "系统的毕业设计状态机包含哪五个阶段?",
      "expected_sections": ["3.2 毕业设计状态机"],   # 命中章节标题(双向包含)即相关
      "expected_content": ["选题→开题→中期→终期→毕业材料"]  # 或内容子串
    }
  ]
}

标注规则(按优先级): expected_chunk_ids 直接指定 > 章节标题匹配 > 内容子串匹配。
"""
import json
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.rag import retriever as retriever_module
from app.ai.rag.retriever import HybridRetriever
from app.ai.vector.store import student_scope_filter, vector_store
from app.models.ai import AIDocumentChunk

STRATEGIES = ("vector", "keyword", "hybrid")


# ---------- 指标(纯函数, 便于单测) ----------

def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    """Recall@K = |top-K 检索结果 ∩ 相关 chunk| / |相关 chunk|."""
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
    """首个相关结果的倒数排名(1/rank), 无命中为 0; 对问题集求均值即 MRR."""
    for rank, cid in enumerate(retrieved, start=1):
        if cid in relevant:
            return 1.0 / rank
    return 0.0


# ---------- 相关性解析 ----------

@dataclass
class EvalQuestion:
    question: str
    relevant: set[int]
    document_types: list[str] | None

    @property
    def valid(self) -> bool:
        return bool(self.relevant)


def _resolve_relevant(db: Session, student_id: int | None, item: dict) -> set[int]:
    """把章节/内容标注解析为当前库中的 chunk id 集合."""
    explicit = item.get("expected_chunk_ids") or []
    if explicit:
        return set(explicit)

    sections = [s for s in item.get("expected_sections") or [] if s]
    contents = [c for c in item.get("expected_content") or [] if c]
    if not sections and not contents:
        return set()

    # 与检索侧同一口径(student_scope_filter): 全局 chunk(student_id IS NULL)也参与标注解析,
    # 旧写法 student_id.in_([sid, None]) 在 SQL 里对 NULL 行永不成立(D43), 会漏掉全局标注。
    conds = [student_scope_filter(student_id)]
    rows = db.execute(select(AIDocumentChunk.id, AIDocumentChunk.section_title, AIDocumentChunk.content)
                      .where(*conds)).all()

    relevant = set()
    for cid, title, content in rows:
        title = title or ""
        content = content or ""
        if any(s in title or title in s for s in sections):
            relevant.add(cid)
            continue
        if any(c in content for c in contents):
            relevant.add(cid)
    return relevant


def AIDocumentChunk_student_filter(student_id: int | None):
    """已废弃: 保留仅为兼容既有调用点, 新代码一律用 student_scope_filter().

    in_([student_id, None]) 对 NULL 行永不成立, 会漏掉全局知识库(D43)。
    """
    return (AIDocumentChunk.student_id.in_([student_id, None]) if student_id is not None
            else AIDocumentChunk.student_id.is_(None))


# ---------- 评估器 ----------

def evaluate_dataset(db: Session, dataset: dict, strategies: tuple[str, ...] = STRATEGIES) -> dict:
    """对标准问题集跑多策略检索并计算指标. 返回结构可直接 JSON 化."""
    top_k = int(dataset.get("top_k", 5))
    doc_types = dataset.get("document_types") or None

    student_id = None
    student_no = dataset.get("student_no")
    if student_no:
        from app.models.user import StudentProfile, SysUser

        student_id = db.execute(
            select(StudentProfile.id).join(SysUser, SysUser.id == StudentProfile.user_id)
            .where(SysUser.username == student_no)
        ).scalar_one_or_none()
        if student_id is None:
            raise ValueError(f"评测数据集引用的学生 {student_no} 不存在")

    questions = []
    skipped = 0
    for item in dataset.get("questions", []):
        relevant = _resolve_relevant(db, student_id, item)
        if not relevant:
            skipped += 1
            continue
        questions.append(EvalQuestion(question=item["question"], relevant=relevant,
                                      document_types=item.get("document_types") or doc_types))
    if not questions:
        raise ValueError("评测数据集中没有可评估的问题(标注未命中任何 chunk, 请先完成索引并核对章节标题)")

    retriever = HybridRetriever(db)
    report: dict = {
        "dataset": dataset.get("name", "unnamed"),
        "questions": len(questions),
        "skipped_unlabeled": skipped,
        "top_k": top_k,
        "vector_mode": vector_store.mode,
        "strategies": {},
    }

    for strategy in strategies:
        per_question = []
        recalls, rrs, hits, latencies = [], [], [], []
        for q in questions:
            started = time.perf_counter()
            chunks = retriever.retrieve(q.question, student_id, q.document_types,
                                        top_k=top_k, strategy=strategy)
            elapsed_ms = (time.perf_counter() - started) * 1000
            retrieved_ids = [c.chunk_id for c in chunks]
            rec = recall_at_k(retrieved_ids, q.relevant, top_k)
            rr = reciprocal_rank(retrieved_ids, q.relevant)
            hit = 1 if any(cid in q.relevant for cid in retrieved_ids) else 0
            recalls.append(rec)
            rrs.append(rr)
            hits.append(hit)
            latencies.append(elapsed_ms)
            per_question.append({
                "question": q.question,
                "retrieved": [{"chunk_id": c.chunk_id, "section": c.section_title,
                               "document_type": c.document_type, "score": c.score,
                               "relevant": c.chunk_id in q.relevant} for c in chunks],
                "relevant_count": len(q.relevant),
                "recall": round(rec, 4),
                "reciprocal_rank": round(rr, 4),
                "hit": bool(hit),
                "latency_ms": round(elapsed_ms, 1),
            })
        n = len(questions)
        report["strategies"][strategy] = {
            "recall_at_k": round(sum(recalls) / n, 4),
            "mrr": round(sum(rrs) / n, 4),
            "hit_rate": round(sum(hits) / n, 4),
            "avg_latency_ms": round(sum(latencies) / n, 1),
            "per_question": per_question,
        }
    return report


def summarize_for_paper(report: dict) -> str:
    """把评测报告压缩成论文实验表格式的多行文本."""
    lines = [f"数据集: {report['dataset']}  问题数: {report['questions']}  K={report['top_k']}  向量模式: {report['vector_mode']}",
             "方案         | Recall@K | MRR    | 命中率 | 平均延迟(ms)"]
    for name, s in report["strategies"].items():
        lines.append(f"{name:<12}| {s['recall_at_k']:<8} | {s['mrr']:<6} | {s['hit_rate']:<6} | {s['avg_latency_ms']}")
    return "\n".join(lines)


# ---------- rerank 前后对照(roadmap 九十二章扩展) ----------

def _normalize_case(case) -> tuple[str, set[int], int | None, list[str] | None, int | None]:
    """用例兼容两种形态: dict(question/relevant/...) 与 EvalQuestion 等对象."""
    if isinstance(case, dict):
        question = str(case.get("question") or case.get("query") or "")
        relevant = case.get("relevant") or case.get("expected_chunk_ids") or ()
        return (question, {int(x) for x in relevant}, case.get("student_id"),
                case.get("document_types"), case.get("top_k"))
    question = str(getattr(case, "question", "") or "")
    relevant = getattr(case, "relevant", ()) or ()
    return (question, {int(x) for x in relevant}, getattr(case, "student_id", None),
            getattr(case, "document_types", None), getattr(case, "top_k", None))


def _warmup(retriever: HybridRetriever, cases: list, *, strategy: str, top_k: int) -> None:
    """先跑一次检索再计时: BM25 建索引、jieba 载词典、ONNX 载模型都只发生一次,
    不预热的话这些一次性成本会全压在先跑的那一臂上, 延迟差值变成噪声。"""
    question, _relevant, student_id, document_types, case_top_k = cases[0]
    try:
        retriever.retrieve(question, student_id, document_types,
                           top_k=int(case_top_k or top_k), strategy=strategy, rerank=False)
    except Exception:  # noqa: BLE001 — 预热失败不影响正式测量
        pass


def _run_arm(retriever: HybridRetriever, cases: list, *, rerank: bool, strategy: str,
             top_k: int) -> dict:
    per_case = []
    recalls, rrs, hits, latencies = [], [], [], []
    for question, relevant, student_id, document_types, case_top_k in cases:
        k = int(case_top_k or top_k)
        started = time.perf_counter()
        chunks = retriever.retrieve(question, student_id, document_types, top_k=k,
                                    strategy=strategy, rerank=rerank)
        elapsed_ms = (time.perf_counter() - started) * 1000
        retrieved_ids = [c.chunk_id for c in chunks]
        rec = recall_at_k(retrieved_ids, relevant, k)
        rr = reciprocal_rank(retrieved_ids, relevant)
        hit = 1 if any(cid in relevant for cid in retrieved_ids) else 0
        recalls.append(rec)
        rrs.append(rr)
        hits.append(hit)
        latencies.append(elapsed_ms)
        per_case.append({
            "question": question,
            "retrieved": [{"chunk_id": c.chunk_id, "section": c.section_title,
                           "document_type": c.document_type, "score": c.score,
                           "rerank_score": c.rerank_score, "stage": c.stage,
                           "relevant": c.chunk_id in relevant} for c in chunks],
            "relevant_count": len(relevant),
            "recall": round(rec, 4),
            "reciprocal_rank": round(rr, 4),
            "hit": bool(hit),
            "latency_ms": round(elapsed_ms, 1),
        })
    n = len(per_case) or 1
    return {
        "rerank": rerank,
        "recall_at_k": round(sum(recalls) / n, 4),
        "mrr": round(sum(rrs) / n, 4),
        "hit_rate": round(sum(hits) / n, 4),
        "avg_latency_ms": round(sum(latencies) / n, 1),
        "per_case": per_case,
    }


def compare_with_without_rerank(cases: list, db: Session | None = None, *,
                                strategy: str = "hybrid", top_k: int = 5,
                                retriever: HybridRetriever | None = None) -> dict:
    """同一批 query 分别走 rerank=True / rerank=False, 输出 Recall@K 与 MRR 对照.

    cases: [{"question": ..., "relevant": [chunk_id...], "student_id": ..., 
             "document_types": [...], "top_k": ...}] 或 EvalQuestion 列表;
    db / retriever 二选一(都不给就无法检索)。
    两臂只有 rerank 开关不同 —— 召回池、融合、回表、过滤完全一致, 差值只能归因于精排。
    """
    if not cases:
        raise ValueError("rerank 对照实验没有用例")
    if retriever is None:
        if db is None:
            raise ValueError("compare_with_without_rerank 需要 db 或 retriever 之一")
        retriever = HybridRetriever(db)
    normalized = [_normalize_case(case) for case in cases]
    _warmup(retriever, normalized, strategy=strategy, top_k=top_k)
    # 报告里的精排器必须取检索管线真正用的那一个(而非另起一份工厂结果),
    # 否则两臂精排开关之外的差异会被写进论文实验表。
    reranker = retriever_module.get_reranker()
    without_arm = _run_arm(retriever, normalized, rerank=False, strategy=strategy, top_k=top_k)
    with_arm = _run_arm(retriever, normalized, rerank=True, strategy=strategy, top_k=top_k)
    return {
        "cases": len(normalized),
        "strategy": strategy,
        "top_k": top_k,
        "vector_mode": vector_store.mode,
        "reranker": getattr(reranker, "name", "unknown"),
        "reranker_available": bool(getattr(reranker, "available", False)),
        "without_rerank": without_arm,
        "with_rerank": with_arm,
        "delta": {
            "recall_at_k": round(with_arm["recall_at_k"] - without_arm["recall_at_k"], 4),
            "mrr": round(with_arm["mrr"] - without_arm["mrr"], 4),
            "hit_rate": round(with_arm["hit_rate"] - without_arm["hit_rate"], 4),
            "avg_latency_ms": round(with_arm["avg_latency_ms"] - without_arm["avg_latency_ms"], 1),
        },
    }


def summarize_rerank_comparison(report: dict) -> str:
    """把 rerank 对照报告压成论文可用的两行表."""
    head = (f"rerank 对照: 策略={report['strategy']} 用例={report['cases']} K={report['top_k']} "
            f"精排器={report['reranker']} 可用={report['reranker_available']}")
    rows = ["方案           | Recall@K | MRR    | 命中率 | 平均延迟(ms)"]
    for label, arm in (("rerank=off", report["without_rerank"]), ("rerank=on", report["with_rerank"])):
        rows.append(f"{label:<15}| {arm['recall_at_k']:<8} | {arm['mrr']:<6} | "
                    f"{arm['hit_rate']:<6} | {arm['avg_latency_ms']}")
    delta = report["delta"]
    rows.append(f"delta          | {delta['recall_at_k']:<8} | {delta['mrr']:<6} | "
                f"{delta['hit_rate']:<6} | {delta['avg_latency_ms']}")
    return "\n".join([head] + rows)


def load_dataset_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
