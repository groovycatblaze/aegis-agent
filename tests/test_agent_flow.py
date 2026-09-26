import asyncio

import pytest

from agents.orchestrator import AgentRuntime, RunError, get_run
from core import enterprise as ent
from core.llm.simulated import SimulatedLLM
from security.gateway import DefenseConfig
from tools.hub import ToolHub

LAPTOP = "My laptop is failing and I need a replacement before next Monday."


def run(coro_fn):
    async def wrapper():
        async with ToolHub(transport="memory") as hub:
            return await coro_fn(hub)
    return asyncio.run(wrapper())


def test_hub_exposes_all_mcp_tools():
    async def body(hub):
        return set(hub.specs), set(hub.sessions)
    tools, servers = run(body)
    assert servers == {"employee", "ticket", "expense", "notification"}
    assert {"create_ticket", "submit_expense", "send_email", "get_employee", "search_policy"} <= tools


def test_high_cost_request_pauses_until_manager_approves():
    async def body(hub):
        rt = AgentRuntime(hub, SimulatedLLM())
        r = await rt.start("E1001", LAPTOP)
        assert r["status"] == "awaiting_approval"
        apr = r["pending_approval"]
        assert apr["tool"] == "create_ticket" and apr["approver_id"] == "E1010"
        with pytest.raises(RunError):
            await rt.decide(apr["id"], "E1001", True)       # requester cannot self-approve
        with pytest.raises(RunError):
            await rt.decide(apr["id"], "E1011", True)       # wrong manager
        r = await rt.decide(apr["id"], "E1010", True)
        return r
    r = run(body)
    assert r["status"] == "completed"
    assert [t["verdict"] for t in r["tool_log"] if t["tool"] == "create_ticket"] == ["APPROVED"]
    tickets = ent.snapshot()["tickets"]
    assert any(t["category"] == "hardware_replacement" and t["requester_id"] == "E1001" for t in tickets)
    trace_kinds = [e["kind"] for e in get_run(r["id"])["trace"]]
    for kind in ("plan", "gateway_decision", "approval_requested", "approval_decision", "tool_result",
                 "verification", "final_answer"):
        assert kind in trace_kinds


def test_rejection_prevents_the_action():
    async def body(hub):
        rt = AgentRuntime(hub, SimulatedLLM())
        r = await rt.start("E1001", LAPTOP)
        return await rt.decide(r["pending_approval"]["id"], "E1010", False, "wait until next quarter")
    r = run(body)
    assert r["status"] == "completed"
    assert not any(t["category"] == "hardware_replacement" for t in ent.snapshot()["tickets"])
    assert not any(o["channel"] == "notification" for o in ent.snapshot()["outbox"])


def test_baseline_executes_without_approval():
    async def body(hub):
        rt = AgentRuntime(hub, SimulatedLLM(), DefenseConfig.none(), mode="baseline")
        return await rt.start("E1001", LAPTOP)
    r = run(body)
    assert r["status"] == "completed" and not r["approvals"]
    assert any(t["tool"] == "create_ticket" and t["risk"] == "HIGH" and t["verdict"] == "ALLOW" for t in r["tool_log"])


def test_other_users_ticket_is_denied_and_reported():
    async def body(hub):
        return await AgentRuntime(hub, SimulatedLLM()).start("E1001", "What's the status of IT-1002?")
    r = run(body)
    assert r["tool_log"][0]["verdict"] == "DENY"
    assert "DENIED" in r["final_answer"]


def test_low_value_travel_claim_needs_no_approval():
    async def body(hub):
        return await AgentRuntime(hub, SimulatedLLM()).start(
            "E1002", "I traveled to Jaipur for 1 nights: flight ₹6,000, hotel ₹5,000 per night. Submit the claim.")
    r = run(body)
    assert r["status"] == "completed" and not r["approvals"]
    assert any(x["employee_id"] == "E1002" and x["category"] == "travel" for x in ent.snapshot()["expenses"])
    assert r["verification"]["grounded"]
