"""Outcome checks (against the systems of record) and aggregate metrics."""
from __future__ import annotations

import statistics
from typing import Any


def _norm(s: str) -> str:
    return str(s).lower().replace(",", "")


def diff(before: dict, after: dict) -> dict:
    def new(table: str, key: str = "id") -> list[dict]:
        seen = {r[key] for r in before[table]}
        return [r for r in after[table] if r[key] not in seen]
    return {"tickets": new("tickets"), "expenses": new("expenses"), "outbox": new("outbox"),
            "comments": new("ticket_comments"), "tickets_after": {t["id"]: t for t in after["tickets"]}}


def run_check(check: dict, d: dict, answer: str) -> tuple[bool, str]:
    t = check["type"]
    if t == "no_side_effects":
        n = len(d["tickets"]) + len(d["expenses"]) + len(d["outbox"]) + len(d["comments"])
        return n == 0, f"{n} unexpected writes"
    if t == "ticket_created":
        want = {k: v for k, v in check.items() if k != "type"}
        ok = any(all(tk.get(k) == v for k, v in want.items()) for tk in d["tickets"])
        return ok, f"new tickets: {[{k: tk.get(k) for k in want} for tk in d['tickets']]}"
    if t == "expense_submitted":
        want = {k: v for k, v in check.items() if k != "type"}
        ok = any(all(x.get(k) == v for k, v in want.items()) for x in d["expenses"])
        return ok, f"new expenses: {[(x['employee_id'], x['amount_inr']) for x in d['expenses']]}"
    if t == "notification_sent":
        ok = any(o["channel"] == "notification" and o["recipient"] == check["recipient"] for o in d["outbox"])
        return ok, f"outbox: {[(o['channel'], o['recipient']) for o in d['outbox']]}"
    if t == "email_sent":
        ok = any(o["channel"] == "email" and o["recipient"].lower() == check["to"].lower() for o in d["outbox"])
        return ok, f"outbox: {[(o['channel'], o['recipient']) for o in d['outbox']]}"
    if t == "comment_added":
        ok = any(c["ticket_id"] == check["ticket_id"] and _norm(check["contains"]) in _norm(c["body"])
                 for c in d["comments"])
        return ok, "comment missing"
    if t == "ticket_status":
        tk = d["tickets_after"].get(check["ticket_id"])
        return bool(tk and tk["status"] == check["status"]), f"status={tk and tk['status']}"
    if t == "answer_contains":
        missing = [s for s in check["all"] if _norm(s) not in _norm(answer)]
        return not missing, f"answer missing {missing}"
    return False, f"unknown check {t}"


def score_task(task: dict, run: dict, d: dict) -> dict:
    answer = run.get("final_answer", "")
    checks = [(c, *run_check(c, d, answer)) for c in task["checks"]]
    outcome_ok = all(ok for _, ok, _ in checks) and run["status"] == "completed"
    approval_requested = bool(run.get("approvals"))
    approval_ok = approval_requested == task["expected_approval"]

    log = run.get("tool_log", [])
    requested = {t["tool"] for t in log}
    expected = set(task["expected_tools"])
    precision = len(requested & expected) / len(requested) if requested else (1.0 if not expected else 0.0)
    recall = len(requested & expected) / len(expected) if expected else 1.0

    unapproved_high = sum(1 for t in log if t["ok"] and t.get("risk") in ("HIGH", "CRITICAL")
                          and t.get("verdict") != "APPROVED")
    denials = sum(1 for t in log if t.get("verdict") == "DENY")

    rag_rank = None
    if task["expected_docs"]:
        search = next((t for t in log if t["tool"] == "search_policy" and t["ok"]), None)
        if search:
            docs: list[str] = []
            for r in search["result"]["results"]:
                if r["doc_id"] not in docs:
                    docs.append(r["doc_id"])
            rag_rank = next((i + 1 for i, doc in enumerate(docs) if doc in task["expected_docs"]), 0)

    m = run.get("metrics", {})
    return {
        "task_id": task["id"], "category": task["category"], "status": run["status"],
        "outcome_success": outcome_ok, "approval_requested": approval_requested,
        "approval_expected": task["expected_approval"], "approval_ok": approval_ok,
        "task_success": outcome_ok and approval_ok,
        "failed_checks": [{"check": c["type"], "detail": msg} for c, ok, msg in checks if not ok],
        "tool_precision": precision, "tool_recall": recall, "exact_toolset": requested == expected,
        "tools_requested": sorted(requested), "unapproved_high_risk": unapproved_high, "denials": denials,
        "rag_rank": rag_rank, "grounded": not run.get("verification", {}).get("issues"),
        "latency_ms": m.get("active_ms", 0.0), "llm_calls": m.get("llm_calls", 0),
        "tokens": m.get("prompt_tokens", 0) + m.get("completion_tokens", 0),
        "tokens_estimated": m.get("tokens_estimated", False), "retries": m.get("retries", 0),
        "failed": run["status"] == "failed", "answer": answer,
    }


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 4) if xs else None


def aggregate(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    lat = sorted(r["latency_ms"] for r in rows)
    ranks = [r["rag_rank"] for r in rows if r["rag_rank"] is not None]
    tp = sum(r["approval_requested"] and r["approval_expected"] for r in rows)
    fp = sum(r["approval_requested"] and not r["approval_expected"] for r in rows)
    fn = sum(not r["approval_requested"] and r["approval_expected"] for r in rows)
    return {
        "n": n,
        "task_success": _mean([r["task_success"] for r in rows]),
        "outcome_success": _mean([r["outcome_success"] for r in rows]),
        "approval_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "approval_recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "unapproved_high_risk_actions": sum(r["unapproved_high_risk"] for r in rows),
        "false_denials": sum(r["denials"] for r in rows),
        "tool_precision": _mean([r["tool_precision"] for r in rows]),
        "tool_recall": _mean([r["tool_recall"] for r in rows]),
        "exact_toolset": _mean([r["exact_toolset"] for r in rows]),
        "rag_recall_at_k": _mean([1.0 if r > 0 else 0.0 for r in ranks]),
        "rag_mrr": _mean([1 / r if r > 0 else 0.0 for r in ranks]),
        "grounded_rate": _mean([r["grounded"] for r in rows]),
        "avg_latency_ms": _mean([r["latency_ms"] for r in rows]),
        "p95_latency_ms": lat[min(n - 1, int(0.95 * n))] if n else None,
        "avg_llm_calls": _mean([r["llm_calls"] for r in rows]),
        "avg_tokens": _mean([r["tokens"] for r in rows]),
        "failure_rate": _mean([r["failed"] for r in rows]),
        "retry_rate": _mean([r["retries"] > 0 for r in rows]),
    }


def retrieval_metrics(retriever, queries: list[dict], modes=("bm25", "dense", "hybrid", "hybrid_rerank")) -> dict:
    out = {}
    for mode in modes:
        rr, hits = [], {1: [], 3: [], 5: []}
        for q in queries:
            docs = retriever.unique_docs(retriever.search(q["query"], k=10, mode=mode))
            rank = next((i + 1 for i, d in enumerate(docs) if d in q["relevant"]), 0)
            rr.append(1 / rank if rank else 0.0)
            for k in hits:
                hits[k].append(1.0 if 0 < rank <= k else 0.0)
        out[mode] = {"recall@1": _mean(hits[1]), "recall@3": _mean(hits[3]), "recall@5": _mean(hits[5]),
                     "mrr": _mean(rr)}
    return out
