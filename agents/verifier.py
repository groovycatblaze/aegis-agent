"""Post-run verification: deterministic checks that the final answer is grounded
in what actually happened during the run."""
from __future__ import annotations

import json
import re

ID_RE = re.compile(r"\b(?:IT|EXP)-\d{4}\b")
MONEY_RE = re.compile(r"INR\s*([\d,]+)")


def _norm_numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text)}


def verify(request: str, final_answer: str, tool_log: list[dict], plan: dict) -> dict:
    evidence = request + "\n" + "\n".join(json.dumps(t.get("result"), default=str) for t in tool_log)
    evidence_ids = set(ID_RE.findall(evidence))
    evidence_nums = _norm_numbers(evidence)
    issues: list[str] = []

    for rid in sorted(set(ID_RE.findall(final_answer or ""))):
        if rid not in evidence_ids:
            issues.append(f"answer mentions {rid}, which no tool returned")
    for amt in MONEY_RE.findall(final_answer or ""):
        if amt.replace(",", "") not in evidence_nums:
            issues.append(f"answer states INR {amt}, which is not in any tool result or the request")

    executed = [t for t in tool_log if t.get("ok")]
    citations = sorted({r["doc_id"] for t in executed if t["tool"] == "search_policy"
                        for r in (t.get("result") or {}).get("results", [])})
    if any(t["tool"] == "search_policy" for t in executed):
        titles = {r["title"] for t in executed if t["tool"] == "search_policy"
                  for r in (t.get("result") or {}).get("results", [])}
        if not any(title in (final_answer or "") for title in titles):
            issues.append("policy was retrieved but the answer cites no policy")

    planned = [s["tool"] for s in plan.get("steps", [])]
    done = {t["tool"] for t in executed}
    return {"grounded": not any("which" in i for i in issues), "issues": issues,
            "retrieved_docs": citations,
            "plan_steps_skipped": [p for p in planned if p not in done]}
