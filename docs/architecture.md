# Architecture

## Request lifecycle

1. **API** (`api/main.py`) authenticates the caller (`X-User-Id`, standing in for SSO) and starts a run.
2. **Tool exposure**: the gateway computes the tools the user's role may use; only those are shown to the model.
3. **Planner** (`agents/planner.py`) asks the LLM for a JSON plan (`intent`, `steps[{tool, purpose}]`).
   If planning fails the plan is empty, which permits no state changes (fail closed).
4. **Executor loop** (`agents/orchestrator.py`, `agents/executor.py`): the LLM proposes tool calls; each one
   goes to `Gateway.evaluate()`:
   - `DENY`: the model receives a structured refusal and must explain it to the user.
   - `REQUIRE_APPROVAL`: an `Approval` row is created, the full `RunState` is persisted, and the run pauses.
   - `ALLOW`: the MCP hub calls the tool; the output is redacted according to the user's clearance.
5. **Resume**: `POST /api/approvals/{id}/decision` checks that the decider is the designated approver (and
   not the requester), checks that the stored arguments still hash to what was approved, then executes or
   rejects the call and continues the loop.
6. **Verifier** (`agents/verifier.py`): IDs and INR amounts in the answer must appear in tool results or the
   request; policy answers must cite a retrieved policy.
7. **Trace**: every step is written to `trace_events` as it happens.

## Components

| Component | Files | Notes |
|---|---|---|
| LLM adapters | `core/llm/` | Neutral message format; adapters for OpenAI-compatible and Anthropic tool calling; `SimulatedLLM` for offline use |
| MCP servers | `mcp_servers/*.py` | FastMCP, stdio transport, one process each; backed by `core/enterprise.py` (SQLite) |
| MCP hub | `tools/hub.py` | One `ClientSession` per server, tool discovery via `list_tools`, retries with backoff for transport errors |
| RAG | `rag/` | Markdown → sections → 120-word windows (30 overlap); BM25 + vector index; RRF fusion (k=60); reranker |
| Gateway | `security/` | See `security-model.md` |
| Persistence | `core/db.py` | SQLAlchemy: `runs`, `trace_events`, `approvals`; SQLite or PostgreSQL via `DATABASE_URL` |
| Enterprise simulation | `core/enterprise.py`, `core/business_rules.py` | 27 employees, laptops, tickets, expenses, outbox, audit log |

## Data model (app DB)

- `runs(id, principal_id, request, mode, llm, status, plan, state, final_answer, verification, metrics)`
- `trace_events(run_id, seq, ts, kind, name, data, duration_ms)`: kinds are `run_started`, `tool_exposure`, `plan`,
  `llm_call`, `tool_request`, `gateway_decision`, `tool_result`, `approval_requested`, `approval_decision`,
  `verification`, `final_answer`, `error`
- `approvals(id, run_id, requester_id, tool, args, args_hash, risk_level, reasons, approver_id, approver_role,
  status, decided_by, comment)`

## Why these choices

- **Own orchestration rather than a framework.** The loop is about 150 lines and every decision point is
  visible, which matters for a governance-focused system. LangGraph could replace `_loop` without touching
  the gateway, MCP hub or evaluation.
- **stdio MCP servers.** Mirrors how MCP servers are deployed next to agent hosts; the in-memory transport
  speaks the same protocol for fast tests.
- **Deterministic enforcement.** Nothing the model outputs can change a role, a threshold or an approver.

## Scaling path

Job queue (Redis + worker) for long runs · pgvector for retrieval · OpenTelemetry export of trace events ·
OIDC in front of the API · per-tenant `policy.toml` · streaming (SSE) of trace events to the console.
