"""Freeze extractive marking points and check verdicts against the student's actual words."""
import json

from app.core.exceptions import BusinessException

MARKS = {"supported": 1, "partial": 0.5, "missing": 0, "contradicted": 0}
VERDICTS = {*MARKS, "insufficient_evidence"}


def accuracy_from_evidence(assessment: list[dict]) -> int:
    """材料无法核对的要点免扣分；四舍五入到准确性维度的 35 分量表。"""
    marks = [MARKS[item["verdict"]] for item in assessment if item["verdict"] in MARKS]
    if not marks:
        raise BusinessException("没有可核对的评分要点，本轮未评分。请保留回答重试，或更换材料重新出题", 50302)
    return int(35 * sum(marks) / len(marks) + 0.5)


def snapshot(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return {"sources": []}
    if isinstance(data, list):  # Legacy stored lists of chunk IDs or citation objects.
        data = {"sources": data}
    if not isinstance(data, dict):
        return {"sources": []}
    sources = data.get("sources")
    data["sources"] = [s for s in (sources if isinstance(sources, list) else []) if isinstance(s, dict)
                       and "chunk_id" in s and "document_id" in s]
    return data


def marking_points(candidates, sources: list[dict]) -> list[dict]:
    """Each expected fact is itself a verbatim passage from a cited source."""
    by_id = {s["chunk_id"]: s for s in sources}
    result = []
    for item in candidates if isinstance(candidates, list) else ():
        if not isinstance(item, dict):
            continue
        quote = item.get("expectation")
        ids = item.get("source_ids")
        if not isinstance(quote, str) or not 2 <= len(quote.strip()) <= 220 or not isinstance(ids, list) or not ids:
            continue
        quote = quote.strip()
        if any(type(i) is not int or i not in by_id or quote not in by_id[i]["excerpt"] for i in ids):
            continue
        if not any(p["expectation"] == quote for p in result):
            result.append({"id": len(result) + 1, "expectation": quote, "source_ids": list(dict.fromkeys(ids))})
        if len(result) == 3:
            break
    return result


def mark_snapshot(sources: list[dict], rubric: list[dict], **extra) -> str:
    return json.dumps({"sources": sources, "rubric": rubric, **extra}, ensure_ascii=False)


def assess_points(rows, rubric: list[dict], answer: str) -> list[dict]:
    by_id = {p["id"]: p for p in rubric}
    result = {}
    error = "AI 未按本题已核定的材料要点评价，回答尚未提交，请重试"
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            raise BusinessException(error, 50302)
        key, verdict = row.get("id"), row.get("verdict")
        reason, quote = row.get("reason"), row.get("answer_quote", "")
        if (type(key) is not int or key not in by_id or key in result or not isinstance(verdict, str)
                or verdict not in VERDICTS or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 600
                or not isinstance(quote, str) or len(quote) > 240 or quote not in answer
                or (verdict in {"supported", "partial", "contradicted"} and not quote.strip())):
            raise BusinessException(error, 50302)
        fixed = by_id[key]
        if (row.get("expectation", fixed["expectation"]) != fixed["expectation"]
                or row.get("source_ids", fixed["source_ids"]) != fixed["source_ids"]):
            raise BusinessException(error, 50302)
        result[key] = {**fixed, "verdict": verdict, "reason": reason.strip(), "answer_quote": quote}
    if set(result) != set(by_id):
        raise BusinessException(error, 50302)
    return [result[p["id"]] for p in rubric]
