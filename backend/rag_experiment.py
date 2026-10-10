#!/usr/bin/env python3
"""RAG 检索三臂对比实验(roadmap 九十二章): 仅向量 / 仅关键词 / Hybrid(RRF).

用法:
    .venv/bin/python rag_experiment.py                     # 默认演示论文评测集, K=5
    .venv/bin/python rag_experiment.py --top-k 3
    .venv/bin/python rag_experiment.py --dataset eval_datasets/demo_thesis.json
    .venv/bin/python rag_experiment.py --with-answers      # 追加答案层实验(需真实 LLM Key)

答案层实验(九十二章 表: 普通LLM vs RAG):
  对每个标准问题分别以「无检索直接回答」与「检索增强回答」生成答案,
  再由 LLM 依据标准出处判定 正确性/忠实性(幻觉), 汇总正确率与幻觉率。
  离线 Mock 模式下答案与判定均为模板输出, 不具统计意义, 脚本会明确提示跳过。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from sqlalchemy import select

from app.ai.llm.factory import LLMFactory, extract_json
from app.ai.rag.evaluation import STRATEGIES, evaluate_dataset, load_dataset_file, summarize_for_paper
from app.ai.rag.retriever import HybridRetriever
from app.ai.rag.rag_service import RAGService
from app.db.mysql import SessionLocal
from app.models.user import StudentProfile, SysUser


def resolve_student_id(db, student_no: str) -> int | None:
    return db.execute(
        select(StudentProfile.id).join(SysUser, SysUser.id == StudentProfile.user_id)
        .where(SysUser.username == student_no)
    ).scalar_one_or_none()


def answer_arm(db, dataset: dict, top_k: int) -> dict | None:
    """答案层实验: LLM 直答 vs RAG 增强回答, LLM-as-judge 判定正确率/幻觉率."""
    llm = LLMFactory.get()
    if llm.name == "mock":
        print("\n[答案层实验] 当前为离线 Mock LLM, 判定无统计意义, 跳过。(配置 LLM_API_KEY 后重跑)")
        return None

    student_no = dataset.get("student_no", "")
    with SessionLocal() as db2:
        student_id = resolve_student_id(db2, student_no)
    rag = RAGService(db)
    retriever = HybridRetriever(db)

    correct = {"plain": 0, "rag": 0}
    hallucinated = {"plain": 0, "rag": 0}
    details = []
    for item in dataset["questions"]:
        question = item["question"]
        reference = rag.build_context(retriever.retrieve(question, student_id, dataset.get("document_types"), top_k=top_k))

        plain_answer = llm.complete("你是一个助教, 请直接回答学生关于其毕业设计论文的问题。", question)
        rag_answer = llm.complete(
            "你是一个助教, 只允许依据检索上下文回答论文相关问题, 上下文无法支持时明确说明。"
            f"【检索上下文】\n{reference}\n\n【学生问题】{question}",
        )
        verdicts = {}
        for name, answer in (("plain", plain_answer), ("rag", rag_answer)):
            try:
                verdict = extract_json(llm.complete(
                    "你是评测裁判。依据【标准出处】判定【回答】是否正确、是否包含标准出处之外的编造内容(幻觉)。"
                    '只输出 JSON: {"correct": true/false, "hallucinated": true/false}',
                    f"【标准出处】\n{reference[:2000]}\n\n【问题】{question}\n\n【回答】{answer}",
                ))
                verdicts[name] = (bool(verdict.get("correct")), bool(verdict.get("hallucinated")))
            except Exception:
                verdicts[name] = (False, True)
        for name, (ok, hal) in verdicts.items():
            correct[name] += int(ok)
            hallucinated[name] += int(hal)
        details.append({"question": question, **{k: {"correct": v[0], "hallucinated": v[1]} for k, v in verdicts.items()}})

    n = len(dataset["questions"])
    return {
        "questions": n,
        "plain": {"correct_rate": round(correct["plain"] / n, 4), "hallucination_rate": round(hallucinated["plain"] / n, 4)},
        "rag": {"correct_rate": round(correct["rag"] / n, 4), "hallucination_rate": round(hallucinated["rag"] / n, 4)},
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 三臂对比实验")
    parser.add_argument("--dataset", default="eval_datasets/demo_thesis.json")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--strategies", nargs="*", default=list(STRATEGIES))
    parser.add_argument("--with-answers", action="store_true", help="追加答案层实验(需真实 LLM)")
    parser.add_argument("--save", default=None, help="把完整报告保存为 JSON 文件")
    args = parser.parse_args()

    dataset = load_dataset_file(args.dataset)
    if args.top_k:
        dataset["top_k"] = args.top_k

    print(f"加载数据集: {dataset['name']}  问题数: {len(dataset['questions'])}")
    started = time.perf_counter()
    with SessionLocal() as db:
        report = evaluate_dataset(db, dataset, tuple(args.strategies))
    print(f"评测耗时 {time.perf_counter() - started:.1f}s\n")
    print(summarize_for_paper(report))

    if args.with_answers:
        with SessionLocal() as db:
            answer_report = answer_arm(db, dataset, report["top_k"])
        if answer_report:
            print("\n== 答案层实验(LLM 直答 vs RAG 增强, LLM-as-judge) ==")
            for arm in ("plain", "rag"):
                s = answer_report[arm]
                label = "普通LLM " if arm == "plain" else "RAG增强 "
                print(f"{label}| 正确率 {s['correct_rate']:<8} | 幻觉率 {s['hallucination_rate']}")
            report["answer_level"] = answer_report

    if args.save:
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整报告已保存: {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
