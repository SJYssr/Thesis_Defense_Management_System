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

from app.ai.rag.retriever import HybridRetriever
from app.ai.vector.store import vector_store
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

    conds = [AIDocumentChunk_student_filter(student_id)]
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


def load_dataset_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
