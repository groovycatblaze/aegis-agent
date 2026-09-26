"""Aegis HTTP API + web UI.

Authentication is simulated: the caller identifies with the X-User-Id header
(an employee ID). In production this would come from SSO/OIDC; the point is
that identity comes from the session, never from the model.

Run:  uvicorn api.main:app --reload
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.orchestrator import AgentRuntime, RunError, approval_view, get_run
from core import enterprise as ent
from core.config import ROOT, get_settings
from core.db import Approval, Run, session_scope
from core.llm.factory import get_llm
from security.gateway import DefenseConfig
from security.permissions import load_policy, tools_for_role
from security.risk_engine import can_approve
from tools.hub import ToolHub

FRONTEND = ROOT / "frontend"
RESULTS = ROOT / "evaluation" / "results"


class State:
    hub: ToolHub
    runtimes: dict[str, AgentRuntime]


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not Path(s.enterprise_db).exists():
        ent.seed()
    state.hub = ToolHub(s.mcp_transport)
    await state.hub.start()
    llm = get_llm()
    state.runtimes = {"aegis": AgentRuntime(state.hub, llm, DefenseConfig.full()),
                      "baseline": AgentRuntime(state.hub, llm, DefenseConfig.none(), mode="baseline")}
    yield
    await state.hub.stop()


app = FastAPI(title="Aegis - Secure Agentic Enterprise Workflow Platform", version="1.0.0", lifespan=lifespan)


def _user(x_user_id: str | None) -> dict:
    if not x_user_id:
        raise HTTPException(401, "X-User-Id header required")
    emp = ent.get_employee(x_user_id)
    if not emp:
        raise HTTPException(401, f"unknown user {x_user_id}")
    return emp


class RunRequest(BaseModel):
    request: str = Field(min_length=3, max_length=4000)
    mode: str = Field(default="aegis", pattern="^(aegis|baseline)$")


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str = ""


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"status": "ok", "llm": getattr(state.runtimes["aegis"].llm, "name", "?"),
            "mcp_transport": s.mcp_transport, "mcp_servers": sorted(state.hub.sessions), "tools": len(state.hub.specs)}


@app.get("/api/personas")
async def personas():
    return ent.list_personas()


@app.get("/api/tools")
async def tools(x_user_id: str | None = Header(default=None)):
    policy = load_policy()
    allowed = tools_for_role(_user(x_user_id)["role"]) if x_user_id else None
    return [{**spec.to_dict(), **policy["tools"].get(name, {}),
             "allowed_for_user": None if allowed is None else name in allowed}
            for name, spec in sorted(state.hub.specs.items())]


@app.post("/api/runs")
async def create_run(body: RunRequest, x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    try:
        return await state.runtimes[body.mode].start(user["id"], body.request)
    except RunError as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs")
async def list_runs(limit: int = 30, x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    with session_scope() as s:
        q = s.query(Run).order_by(Run.created_at.desc())
        if user["role"] not in ("it_admin", "hr_admin"):
            q = q.filter(Run.principal_id == user["id"])
        return [{"id": r.id, "principal_id": r.principal_id, "request": r.request, "mode": r.mode,
                 "status": r.status, "created_at": r.created_at.isoformat()} for r in q.limit(limit)]


@app.get("/api/runs/{run_id}")
async def read_run(run_id: str, x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    try:
        run = get_run(run_id)
    except RunError as e:
        raise HTTPException(404, str(e))
    if run["principal_id"] != user["id"] and user["role"] not in ("it_admin", "hr_admin"):
        # Approvers may read the runs they were asked to review.
        with session_scope() as s:
            aprs = s.query(Approval).filter(Approval.run_id == run_id).all()
            reviewer = any(a.decided_by == user["id"] or
                           can_approve(user, a.requester_id, a.approver_id, a.approver_role)[0] for a in aprs)
        if not reviewer:
            raise HTTPException(403, "not your run")
    return run


@app.get("/api/approvals")
async def list_approvals(status: str = "pending", x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    with session_scope() as s:
        rows = s.query(Approval).filter(Approval.status == status).order_by(Approval.created_at.desc()).all()
        out = []
        for a in rows:
            ok, _ = can_approve(user, a.requester_id, a.approver_id, a.approver_role)
            if ok or a.requester_id == user["id"]:
                v = approval_view(a)
                v["can_decide"] = ok
                req = ent.get_employee(a.requester_id)
                v["requester_name"] = req["name"] if req else a.requester_id
                run = s.get(Run, a.run_id)
                v["request"] = run.request if run else ""
                out.append(v)
        return out


@app.post("/api/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: Decision, x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    with session_scope() as s:
        apr = s.get(Approval, approval_id)
        if not apr:
            raise HTTPException(404, "approval not found")
        mode = s.get(Run, apr.run_id).mode
    try:
        return await state.runtimes[mode].decide(approval_id, user["id"], body.decision == "approve", body.comment)
    except RunError as e:
        raise HTTPException(403, str(e))


@app.get("/api/records/{kind}")
async def records(kind: str, x_user_id: str | None = Header(default=None)):
    _user(x_user_id)
    if kind not in ("tickets", "expenses", "outbox", "audit_log"):
        raise HTTPException(404, "unknown record type")
    with ent.connect() as c:
        order = "id DESC"
        return [dict(r) for r in c.execute(f"SELECT * FROM {kind} ORDER BY {order} LIMIT 100")]


@app.post("/api/admin/reset")
async def reset(x_user_id: str | None = Header(default=None)):
    user = _user(x_user_id)
    if user["role"] != "it_admin":
        raise HTTPException(403, "IT admin only")
    ent.seed()
    return {"reset": True}


@app.get("/api/rag/search")
async def rag_search(q: str, k: int = 4, mode: str = "hybrid_rerank"):
    from rag.retriever import get_retriever
    r = get_retriever(get_settings().embedder)
    return [h.to_dict() for h in r.search(q, k, mode)]


@app.get("/api/eval/latest")
async def eval_latest():
    path = RESULTS / "summary.json"
    if not path.exists():
        raise HTTPException(404, "no evaluation results yet - run `python -m evaluation.benchmark`")
    return json.loads(path.read_text())


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")
