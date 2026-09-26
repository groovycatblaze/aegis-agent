"""Planner: turns the user's request into an explicit plan before any tool runs.
The plan is shown to the user, recorded in the trace, and used by the gateway
(state-changing tools must be in it)."""
from __future__ import annotations

from agents.prompts import plan_user_message, planner_system
from core.llm.base import LLMResponse, extract_json


async def make_plan(llm, principal: dict, request: str, tools: list[dict]) -> tuple[dict, LLMResponse | None, str | None]:
    messages = [{"role": "system", "content": planner_system(principal, tools)},
                {"role": "user", "content": plan_user_message(request)}]
    known = {t["name"] for t in tools}
    resp: LLMResponse | None = None
    try:
        resp = await llm.complete(messages, None, json_mode=True)
        raw = extract_json(resp.content or "")
        steps = [s for s in raw.get("steps", []) if isinstance(s, dict) and s.get("tool") in known]
        plan = {"intent": str(raw.get("intent", "unknown")), "summary": str(raw.get("summary", "")),
                "steps": [{"tool": s["tool"], "purpose": str(s.get("purpose", ""))} for s in steps]}
        return plan, resp, None
    except Exception as e:  # noqa: BLE001 - fail closed: an empty plan permits no state changes
        return {"intent": "unknown", "summary": "planning failed", "steps": []}, resp, str(e)


def plan_tools(plan: dict) -> set[str]:
    return {s["tool"] for s in plan.get("steps", [])}
