"""Executor: runs the tool calls proposed by the model. Every call goes through
the gateway first; the verdict decides whether it runs, is refused, or pauses
the run for a human."""
from __future__ import annotations

import hashlib
import json
import uuid

from agents.state import RunState
from core.db import Approval, session_scope
from core.tracing import Tracer
from security.gateway import Gateway
from tools.hub import ToolHub


def args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()


class Executor:
    def __init__(self, hub: ToolHub, gateway: Gateway, db_url: str | None = None):
        self.hub, self.gateway, self.db_url = hub, gateway, db_url

    async def process(self, state: RunState, calls: list[dict], tracer: Tracer) -> bool:
        """Process queued tool calls in order. Returns True if the run paused for approval."""
        from agents.planner import plan_tools
        while calls:
            call = calls.pop(0)
            name, args = call["name"], call["arguments"]
            tracer.event("tool_request", name, {"call_id": call["id"], "arguments": args})
            decision = self.gateway.evaluate(name, args, state.principal, plan_tools(state.plan),
                                             state.tool_calls, state.side_effects)
            tracer.event("gateway_decision", name, decision.to_dict())

            if decision.verdict == "DENY":
                self._tool_message(state, call, {"ok": False, "error": f"DENIED by Aegis policy: {decision.reason}"})
                state.tool_log.append({"tool": name, "arguments": args, "ok": False, "verdict": "DENY",
                                       "risk": decision.risk["level"], "result": None, "error": decision.reason})
                continue

            if decision.verdict == "REQUIRE_APPROVAL":
                approval_id = "apr_" + uuid.uuid4().hex[:12]
                with session_scope(self.db_url) as s:
                    s.add(Approval(id=approval_id, run_id=state.run_id, requester_id=state.principal["id"],
                                   tool=name, args=json.dumps(args), args_hash=args_hash(name, args),
                                   risk_level=decision.risk["level"], reasons=json.dumps(decision.risk["reasons"]),
                                   approver_id=decision.risk.get("approver_id"),
                                   approver_role=decision.risk.get("approver_role")))
                state.pending = {"approval_id": approval_id, "call": call, "remaining": calls,
                                 "risk": decision.risk}
                state.status = "awaiting_approval"
                tracer.event("approval_requested", name, {"approval_id": approval_id, "arguments": args,
                                                          "risk": decision.risk})
                return True

            await self.execute(state, call, tracer, verdict="ALLOW", risk=decision.risk["level"])
        return False

    async def execute(self, state: RunState, call: dict, tracer: Tracer, verdict: str, risk: str) -> None:
        name, args = call["name"], call["arguments"]
        state.tool_calls += 1
        result = await self.hub.call(name, args)
        state.retries += result.retries
        data, redacted = result.data, []
        if result.ok:
            data, redacted = self.gateway.filter_output(state.principal, name, args, result.data)
            if self.gateway.is_side_effect(name):
                state.side_effects += 1
        payload = {"ok": True, "result": data} if result.ok else {"ok": False, "error": result.error}
        tracer.event("tool_result", name, {"ok": result.ok, "result": data if result.ok else None,
                                           "error": result.error, "redacted_fields": redacted,
                                           "server": self.hub.specs[name].server if name in self.hub.specs else None,
                                           "retries": result.retries}, result.latency_ms)
        state.tool_log.append({"tool": name, "arguments": args, "ok": result.ok, "verdict": verdict, "risk": risk,
                               "result": data if result.ok else None, "error": result.error,
                               "latency_ms": round(result.latency_ms, 2)})
        self._tool_message(state, call, payload)

    @staticmethod
    def _tool_message(state: RunState, call: dict, payload: dict) -> None:
        state.messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                               "content": json.dumps(payload, default=str)})
