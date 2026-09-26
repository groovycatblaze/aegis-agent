"""Benchmark runner: executes every task against each agent configuration and
scores the result against the systems of record.

    python -m evaluation.benchmark                      # aegis vs baseline, simulated LLM, stdio MCP
    python -m evaluation.benchmark --llm openai         # real model (needs OPENAI_API_KEY)
    python -m evaluation.benchmark --limit 10 --transport memory

Each task starts from a freshly seeded enterprise database. When a run pauses
for approval, a simulated reviewer approves if the task's ground truth says
approval is warranted and rejects otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATASETS = HERE / "datasets"
RESULTS = HERE / "results"


def _prepare_env(work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    os.environ["AEGIS_DATA_DIR"] = str(work)
    os.environ["AEGIS_ENTERPRISE_DB"] = str(work / "enterprise.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{work / 'eval_app.db'}"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


CONFIGS = {
    "aegis": lambda D: D.full(),
    "baseline": lambda D: D.none(),
    "aegis-no-approvals": lambda D: D.full().without("approvals"),
    "aegis-no-plan-check": lambda D: D.full().without("plan_conformance"),
}


async def run_mode(name: str, tasks: list[dict], hub, llm, verbose: bool) -> list[dict]:
    from agents.orchestrator import AgentRuntime
    from core import enterprise as ent
    from evaluation.metrics import diff, score_task
    from security.gateway import DefenseConfig

    runtime = AgentRuntime(hub, llm, CONFIGS[name](DefenseConfig), mode=name)
    rows = []
    for task in tasks:
        ent.seed()
        before = ent.snapshot()
        try:
            run = await runtime.start(task["principal"], task["request"])
            guard = 0
            while run["status"] == "awaiting_approval" and guard < 5:
                guard += 1
                apr = run["pending_approval"]
                reviewer = apr["approver_id"] or next(
                    p["id"] for p in ent.list_personas() if p["role"] == apr["approver_role"] and p["id"] != task["principal"])
                run = await runtime.decide(apr["id"], reviewer, approve=task["expected_approval"],
                                           comment="benchmark reviewer")
        except Exception as e:  # noqa: BLE001
            run = {"status": "failed", "final_answer": f"{type(e).__name__}: {e}", "tool_log": [], "approvals": [],
                   "metrics": {}, "verification": {}}
        row = score_task(task, run, diff(before, ent.snapshot()))
        row["mode"] = name
        rows.append(row)
        if verbose or not row["task_success"]:
            flag = "PASS" if row["task_success"] else "FAIL"
            print(f"  [{name}] {task['id']} {task['category']:<20} {flag} {row['failed_checks'] or ''}"
                  f"{'' if row['approval_ok'] else ' approval_mismatch'}", flush=True)
    return rows


def write_report(summary: dict) -> str:
    modes = list(summary["modes"])
    L = [f"# Aegis benchmark results", "",
         f"Generated {summary['generated_at']} · LLM `{summary['llm']}` · MCP transport `{summary['transport']}` · "
         f"{summary['n_tasks']} tasks", ""]
    L += ["| Metric | " + " | ".join(modes) + " |", "|---|" + "---:|" * len(modes)]
    labels = [("task_success", "Governed task success", "%"), ("outcome_success", "End-state correct", "%"),
              ("approval_precision", "Approval precision", "%"), ("approval_recall", "Approval recall", "%"),
              ("unapproved_high_risk_actions", "High-risk actions executed without approval", "n"),
              ("false_denials", "Tool calls denied on benign tasks", "n"),
              ("tool_precision", "Tool precision", "%"), ("tool_recall", "Tool recall", "%"),
              ("exact_toolset", "Exact tool-set match", "%"), ("rag_recall_at_k", "RAG Recall@4 (in-agent)", "%"),
              ("rag_mrr", "RAG MRR (in-agent)", "f"), ("grounded_rate", "Grounded answers (verifier)", "%"),
              ("avg_latency_ms", "Avg active latency (ms)", "f"), ("p95_latency_ms", "p95 latency (ms)", "f"),
              ("avg_llm_calls", "LLM calls / task", "f"), ("avg_tokens", "Tokens / task", "f"),
              ("failure_rate", "Failure rate", "%"), ("retry_rate", "Tasks with tool retries", "%")]

    def f(v, k):
        if v is None:
            return "–"
        return f"{v * 100:.1f}%" if k == "%" else (f"{v:.2f}" if k == "f" and v < 10 else (f"{v:.0f}" if k == "f" else str(v)))
    for key, label, kind in labels:
        L.append(f"| {label} | " + " | ".join(f(summary["modes"][m].get(key), kind) for m in modes) + " |")
    L += ["", "## Governed success by category", "", "| Category | Tasks | " + " | ".join(modes) + " |",
          "|---|---:|" + "---:|" * len(modes)]
    for cat, v in summary["by_category"][modes[0]].items():
        L.append(f"| {cat} | {v['n']} | " + " | ".join(f(summary['by_category'][m][cat]['success'], '%') for m in modes) + " |")
    L += ["", f"## Retrieval ({summary['retrieval_queries']} labelled queries, document-level)", "",
          "| Retriever | Recall@1 | Recall@3 | Recall@5 | MRR |", "|---|---:|---:|---:|---:|"]
    for m, v in summary["retrieval"].items():
        L.append(f"| {m} | {f(v['recall@1'], '%')} | {f(v['recall@3'], '%')} | {f(v['recall@5'], '%')} | {f(v['mrr'], 'f')} |")
    L += ["", "## Notes", ""] + [f"- {n}" for n in summary["notes"]]
    return "\n".join(L) + "\n"


async def main_async(args) -> None:
    _prepare_env(Path(args.work))
    sys.path.insert(0, str(ROOT))
    from core.config import reset_settings
    reset_settings()
    from core.llm.factory import get_llm
    from evaluation.metrics import aggregate, retrieval_metrics
    from rag.retriever import get_retriever
    from tools.hub import ToolHub
    from core import enterprise as ent

    tasks = load_jsonl(DATASETS / "normal_tasks.jsonl")
    if args.category:
        tasks = [t for t in tasks if t["category"] == args.category]
    tasks = tasks[: args.limit] if args.limit else tasks
    llm = get_llm(args.llm, args.model)
    ent.seed()
    t0 = time.time()
    all_rows: dict[str, list[dict]] = {}
    async with ToolHub(transport=args.transport) as hub:
        for mode in args.modes:
            print(f"== {mode}: {len(tasks)} tasks with {llm.name}", flush=True)
            all_rows[mode] = await run_mode(mode, tasks, hub, llm, args.verbose)

    queries = load_jsonl(DATASETS / "retrieval_queries.jsonl")
    retr = retrieval_metrics(get_retriever(os.environ.get("AEGIS_EMBEDDER", "hashing")), queries)
    by_cat = {}
    for mode, rows in all_rows.items():
        cats: dict[str, list] = {}
        for r in rows:
            cats.setdefault(r["category"], []).append(r["task_success"])
        by_cat[mode] = {c: {"n": len(v), "success": round(statistics.fmean(v), 4)} for c, v in cats.items()}
    notes = [
        "Governed task success = end state in the systems of record matches ground truth AND human approval was "
        "requested exactly when policy requires it.",
        "The simulated reviewer approves when the ground truth says approval is warranted and rejects otherwise.",
        "Baseline = same model and tools with every gateway defence disabled; it never asks for approval.",
    ]
    if llm.name == "simulated":
        notes.append("LLM = SimulatedLLM, a deterministic scripted stand-in (keyword intents + regex slots) so the "
                     "pipeline runs offline. Its task success measures the platform, not model intelligence; token "
                     "counts are estimates. Re-run with --llm openai or --llm anthropic for model-level numbers.")
    summary = {"generated_at": datetime.now().isoformat(timespec="seconds"), "llm": llm.name,
               "transport": args.transport, "n_tasks": len(tasks), "wall_clock_s": round(time.time() - t0, 1),
               "modes": {m: aggregate(r) for m, r in all_rows.items()}, "by_category": by_cat,
               "retrieval": retr, "retrieval_queries": len(queries), "notes": notes}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for mode, rows in all_rows.items():
        (out / f"runs_{mode}.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    report = write_report(summary)
    (out / "report.md").write_text(report)
    print("\n" + report)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modes", nargs="+", default=["aegis", "baseline"], choices=list(CONFIGS))
    p.add_argument("--llm", default=None, help="simulated | openai | anthropic (default: AEGIS_LLM_PROVIDER)")
    p.add_argument("--model", default=None)
    p.add_argument("--transport", default="stdio", choices=["stdio", "memory"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--category", default=None)
    p.add_argument("--out", default=str(RESULTS))
    p.add_argument("--work", default=str(HERE / ".work"))
    p.add_argument("-v", "--verbose", action="store_true")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
