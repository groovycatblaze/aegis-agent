"""Agent orchestrator: request -> plan -> tool loop (via gateway) -> verification,
with durable pause/resume around human approvals.

mode="aegis"    : all gateway defences on
mode="baseline" : same model, same tools, no gateway (every call executes)
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from agents import verifier
from agents.executor import Executor, args_hash
from agents.planner import make_plan
from agents.prompts import executor_system, format_plan_for_executor
from agents.state import RunState
from core import enterprise as ent
from core.config import get_settings
from core.db import Approval, Run, session_scope
from core.llm.base import tool_schema
from core.tracing import Tracer, load_trace
from security.gateway import DefenseConfig, Gateway
from security.risk_engine import can_approve
from tools.hub import ToolHub


class RunError(Exception):
    pass


class AgentRuntime:
    def __init__(self, hub: ToolHub, llm, defenses: DefenseConfig | None = None, mode: str | None = None,
                 db_url: str | None = None):
        self.hub, self.llm, self.db_url = hub, llm, db_url
        self.defenses = defenses if defenses is not None else DefenseConfig.full()
        self.mode = mode or ("aegis" if self.defenses.enabled else "baseline")
        self.gateway = Gateway({n: s.input_schema for n, s in hub.specs.items()}, self.defenses)
        self.executor = Executor(hub, self.gateway, db_url)
        self.max_steps = get_settings().max_steps

    # ------------------------------------------------------------ public API
    async def start(self, principal_id: str, request: str) -> dict:
        principal = ent.get_employee(principal_id)
        if not principal:
            raise RunError(f"unknown user {principal_id}")
        run_id = "run_" + uuid.uuid4().hex[:12]
        state = RunState(run_id=run_id, principal=principal, request=request, mode=self.mode,
                         llm=getattr(self.llm, "name", "llm"))
        with session_scope(self.db_url) as s:
            s.add(Run(id=run_id, principal_id=principal_id, request=request, mode=self.mode, llm=state.llm,
                      status="running"))
        tracer = Tracer(run_id, self.db_url)
        t0 = time.perf_counter()
        tracer.event("run_started", self.mode, {"principal": {k: principal[k] for k in ("id", "name", "role", "title")},
                                                "request": request, "llm": state.llm,
                                                "defenses": self.defenses.to_dict()})

        exposed = sorted(self.gateway.exposed_tools(principal))
        tools = [tool_schema(self.hub.specs[n]) for n in exposed]
        tracer.event("tool_exposure", "", {"tools": exposed, "hidden": sorted(set(self.hub.specs) - set(exposed))})

        with tracer.span("plan", "planner") as sp:
            plan, resp, err = await make_plan(self.llm, principal, request, tools)
            sp.update({"plan": plan, "error": err})
        self._account(state, resp)
        state.plan = plan
        state.messages = [{"role": "system", "content": executor_system(principal) + "\n\n" + format_plan_for_executor(plan)},
                          {"role": "user", "content": request}]
        await self._loop(state, tracer)
        state.active_ms += (time.perf_counter() - t0) * 1000
        return self._save(state, tracer)

    async def decide(self, approval_id: str, approver_id: str, approve: bool, comment: str = "") -> dict:
        approver = ent.get_employee(approver_id)
        if not approver:
            raise RunError(f"unknown approver {approver_id}")
        with session_scope(self.db_url) as s:
            apr = s.get(Approval, approval_id)
            if not apr or apr.status != "pending":
                raise RunError("approval not found or already decided")
            allowed, why = can_approve(approver, apr.requester_id, apr.approver_id, apr.approver_role)
            if not allowed:
                raise RunError(f"{approver_id} cannot decide this approval: {why}")
            run = s.get(Run, apr.run_id)
            state = RunState.from_json(run.state)
            apr.status = "approved" if approve else "rejected"
            apr.decided_by, apr.comment, apr.decided_at = approver_id, comment, datetime.utcnow()
            apr_view = {"id": apr.id, "tool": apr.tool, "args_hash": apr.args_hash}

        t0 = time.perf_counter()
        tracer = Tracer(state.run_id, self.db_url, start_seq=state.trace_seq)
        pending = state.pending or {}
        call = pending.get("call")
        if not call or pending.get("approval_id") != approval_id:
            raise RunError("run is not waiting for this approval")
        # The approval is bound to the exact arguments that were shown to the approver.
        if args_hash(call["name"], call["arguments"]) != apr_view["args_hash"]:
            raise RunError("arguments changed after approval was requested")
        tracer.event("approval_decision", call["name"], {"approval_id": approval_id, "approved": approve,
                                                         "approver": approver_id, "reason": why, "comment": comment})
        state.approvals.append({"approval_id": approval_id, "tool": call["name"], "approved": approve,
                                "approver": approver_id, "risk": pending.get("risk", {}).get("level")})
        remaining = pending.get("remaining", [])
        state.pending, state.status = None, "running"

        if approve:
            await self.executor.execute(state, call, tracer, verdict="APPROVED", risk=pending["risk"]["level"])
        else:
            self.executor._tool_message(state, call, {"ok": False, "error": f"REJECTED by approver {approver_id}"
                                                      + (f": {comment}" if comment else "")})
            state.tool_log.append({"tool": call["name"], "arguments": call["arguments"], "ok": False,
                                   "verdict": "REJECTED", "risk": pending["risk"]["level"], "result": None,
                                   "error": "rejected by approver"})
        paused = await self.executor.process(state, remaining, tracer)
        if not paused:
            await self._loop(state, tracer)
        state.active_ms += (time.perf_counter() - t0) * 1000
        return self._save(state, tracer)

    # ------------------------------------------------------------ internals
    def _account(self, state: RunState, resp) -> None:
        if resp is None:
            return
        state.llm_calls += 1
        state.prompt_tokens += resp.usage.prompt_tokens
        state.completion_tokens += resp.usage.completion_tokens
        state.tokens_estimated = state.tokens_estimated or resp.usage.estimated

    async def _loop(self, state: RunState, tracer: Tracer) -> None:
        exposed = sorted(self.gateway.exposed_tools(state.principal))
        tools = [tool_schema(self.hub.specs[n]) for n in exposed]
        try:
            while state.step < self.max_steps:
                state.step += 1
                with tracer.span("llm_call", getattr(self.llm, "name", "llm")) as sp:
                    resp = await self.llm.complete(state.messages, tools)
                    sp.update({"step": state.step, "content": resp.content,
                               "tool_calls": [tc.to_dict() for tc in resp.tool_calls],
                               "usage": vars(resp.usage)})
                self._account(state, resp)
                if not resp.tool_calls:
                    state.final_answer = resp.content or ""
                    break
                calls = [tc.to_dict() for tc in resp.tool_calls]
                state.messages.append({"role": "assistant", "content": resp.content, "tool_calls": calls})
                if await self.executor.process(state, calls, tracer):
                    return  # paused for approval
            else:
                state.final_answer = "I stopped because the step budget was exhausted."
                state.error = "max_steps"
        except Exception as e:  # noqa: BLE001
            state.status, state.error = "failed", f"{type(e).__name__}: {e}"
            state.final_answer = state.final_answer or "The run failed; see the trace for details."
            tracer.event("error", type(e).__name__, {"message": str(e)})
            return

        state.status = "failed" if state.error else "completed"
        v = verifier.verify(state.request, state.final_answer, state.tool_log, state.plan)
        state.__dict__["verification"] = v
        tracer.event("verification", "", v)
        tracer.event("final_answer", "", {"answer": state.final_answer, "status": state.status})

    def _save(self, state: RunState, tracer: Tracer) -> dict:
        state.trace_seq = tracer.seq
        verification = state.__dict__.pop("verification", None)
        with session_scope(self.db_url) as s:
            run = s.get(Run, state.run_id)
            run.status, run.plan, run.state = state.status, json.dumps(state.plan), state.to_json()
            run.final_answer = state.final_answer
            if verification is not None:
                run.verification = json.dumps(verification)
            run.metrics = json.dumps(state.metrics())
        return get_run(state.run_id, self.db_url, include_trace=False)


def get_run(run_id: str, db_url: str | None = None, include_trace: bool = True) -> dict:
    with session_scope(db_url) as s:
        run = s.get(Run, run_id)
        if not run:
            raise RunError("run not found")
        state = json.loads(run.state or "{}")
        pending = None
        if state.get("pending"):
            apr = s.get(Approval, state["pending"]["approval_id"])
            pending = approval_view(apr)
        out = {"id": run.id, "principal_id": run.principal_id, "request": run.request, "mode": run.mode,
               "llm": run.llm, "status": run.status, "plan": json.loads(run.plan or "{}"),
               "final_answer": run.final_answer, "verification": json.loads(run.verification or "{}"),
               "metrics": json.loads(run.metrics or "{}"), "pending_approval": pending,
               "tool_log": state.get("tool_log", []), "approvals": state.get("approvals", []),
               "created_at": run.created_at.isoformat()}
    if include_trace:
        out["trace"] = load_trace(run_id, db_url)
    return out


def approval_view(apr: Approval | None) -> dict | None:
    if apr is None:
        return None
    return {"id": apr.id, "run_id": apr.run_id, "requester_id": apr.requester_id, "tool": apr.tool,
            "arguments": json.loads(apr.args), "risk_level": apr.risk_level, "reasons": json.loads(apr.reasons),
            "approver_id": apr.approver_id, "approver_role": apr.approver_role, "status": apr.status,
            "decided_by": apr.decided_by, "comment": apr.comment, "created_at": apr.created_at.isoformat()}
