# Aegis — Secure Agentic Enterprise Workflow Platform

An AI agent that takes an employee's request, plans a workflow, looks up company policy (RAG), calls
enterprise tools over **MCP**, asks a **human to approve** risky actions, and records an **auditable trace**
of everything it did. A **benchmark harness** measures it against a baseline agent.

> The LLM proposes. Deterministic software decides what actually runs.

```
 Employee ──► FastAPI ──► Planner (LLM) ──► plan: [search_policy, get_asset, create_ticket, …]
                               │
                               ▼
                     Executor loop (LLM tool calls)
                               │  every proposed call
                               ▼
               ┌──────── Aegis Gateway (security/) ────────┐
               │ RBAC · ABAC · schema · plan conformance   │
               │ egress/DLP · budgets · risk engine        │
               └───────┬──────────────┬──────────────┬─────┘
                     ALLOW     REQUIRE_APPROVAL     DENY
                       │              │               │
                       │     manager/finance approves │
                       ▼              ▼               ▼
        MCP client hub ──► employee · ticket · expense · notification MCP servers
                       │         + search_policy (hybrid RAG)
                       ▼
          Output redaction ─► Verifier ─► Answer      (every step ─► trace store)
```

## What's in the box

| Area | Implementation |
|---|---|
| Orchestration | `agents/` — planner (plan made **before** any tool runs), executor loop, verifier, durable pause/resume |
| LLMs | `core/llm/` — OpenAI-compatible (OpenAI, Azure, Groq, Ollama, vLLM…), Anthropic, and an offline `SimulatedLLM` |
| RAG | `rag/` — heading-aware chunking, BM25 + vector search fused with RRF, second-stage reranker, citations with doc version |
| MCP | `mcp_servers/` — 4 FastMCP servers (20 tools) over **stdio**; `tools/hub.py` MCP client hub |
| Governance | `security/` — `policy.toml` (risk levels, roles, thresholds), permission engine, risk engine, guardrails, gateway |
| Human-in-the-loop | Approval inbox, designated approver, separation of duties, approval bound to a hash of the exact arguments |
| Observability | `core/tracing.py` — ordered trace events (plan, LLM calls, gateway decisions, tool results, approvals, verification) with latency and tokens |
| Evaluation | `evaluation/` — 100 generated tasks, end-state checks against the systems of record, Aegis vs baseline, retrieval benchmark |
| App | `api/main.py` (FastAPI) + `frontend/` (no-build web console) |
| Ops | Dockerfile, docker-compose (API + PostgreSQL), GitHub Actions CI, 40 pytest tests |

## Quick start (offline, no API key)

```bash
pip install -r requirements-dev.txt
python -m scripts.seed                 # create the simulated enterprise systems
uvicorn api.main:app --reload          # open http://localhost:8000
```

In the console:
1. Signed in as **Priya Sharma** (engineer), run *"My laptop is failing and I need a replacement before next Monday."*
   The agent checks the hardware policy and her laptop's age, then pauses: a Performance-tier laptop
   (INR 92,000) is over the INR 75,000 threshold.
2. Switch to **Arjun Mehta** (her manager) → *Approvals* → approve. The run resumes, creates the ticket and notifies her.
3. Try the same request in **Baseline** mode: the ticket is created with no approval.
4. Open *Records* to see the tickets, expenses, outbox and audit log; *Evaluation* for benchmark results.

CLI alternative: `python -m scripts.demo "What's the status of IT-1003?" --user E1001`

### Use a real LLM

```bash
export AEGIS_LLM_PROVIDER=openai  OPENAI_API_KEY=sk-...  AEGIS_LLM_MODEL=gpt-4o-mini
# or any OpenAI-compatible server, e.g. Ollama:
export AEGIS_LLM_PROVIDER=openai  OPENAI_BASE_URL=http://localhost:11434/v1  OPENAI_API_KEY=ollama  AEGIS_LLM_MODEL=qwen2.5:14b
# or Anthropic:
export AEGIS_LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=...  AEGIS_LLM_MODEL=claude-sonnet-5
```

### Docker

```bash
cp .env.example .env
docker compose up --build              # API on :8000, PostgreSQL for runs/traces/approvals
```

## Benchmark

```bash
python -m evaluation.generate_datasets     # regenerate the 100 tasks (deterministic)
python -m evaluation.benchmark             # Aegis vs baseline, simulated LLM, stdio MCP
python -m evaluation.benchmark --llm openai --modes aegis baseline aegis-no-approvals
```

Latest run (simulated LLM, stdio MCP, 100 tasks); full report in [`evaluation/results/report.md`](evaluation/results/report.md):

| Metric | Aegis | Baseline |
|---|---:|---:|
| Governed task success (correct end state **and** approval exactly when policy requires) | **99.0%** | 88.0% |
| End state correct in systems of record | 99.0% | 99.0% |
| High-risk actions executed without human approval | **0** | 11 |
| Approval precision / recall | 100% / 100% | – / 0% |
| Tool calls wrongly denied on legitimate tasks | 0 | 0 |
| Tool precision / recall | 99.3% / 100% | 99.3% / 100% |
| RAG Recall@4 / MRR (in-agent) | 100% / 1.00 | 100% / 1.00 |
| Tokens per task (estimated) | 9,000 | 10,034 |

Read these numbers honestly: the offline `SimulatedLLM` is a scripted stand-in (keyword intent rules and
regex slot filling), so high task success reflects the **platform** working end to end, not model
intelligence. The meaningful comparison is the governance column: same model, same tools, and the baseline
executes 11 high-risk actions (over-threshold hardware orders and travel claims) that policy says need a
manager. Aegis also uses fewer tokens because each user only sees the tools their role allows. Run with a
real model to get model-level task success, tool accuracy, latency and cost.

## Repository layout

```
agents/          orchestrator, planner, executor, verifier, prompts, run state
api/             FastAPI app (runs, approvals, records, tools, eval results)
core/            config, simulated enterprise DB + business rules, app DB, tracing, LLM adapters
docs/            architecture, security model, evaluation methodology
evaluation/      dataset generator, benchmark runner, metrics, datasets/, results/
frontend/        single-page console (vanilla JS, no build step)
mcp_servers/     employee, ticket, expense, notification MCP servers
rag/             corpus (12 synthetic policies), ingestion, embeddings, retriever, reranker
scripts/         seed + CLI demo
security/        policy.toml, permissions, risk engine, guardrails, gateway
tests/           unit + end-to-end tests (MCP in-memory transport)
tools/           MCP client hub
```

## Design decisions

- **Plan first, then act.** The plan is produced from the user's request before any tool output is seen.
  State-changing tools that are not in the plan are refused, so later content can't add new side effects.
- **Identity from the session, never from the model.** The principal comes from the authenticated request;
  resource checks compare tool arguments to it (you can only submit your own claims, read your own tickets…).
- **Least-privilege tool exposure.** The model is only shown the tools the user's role may use.
- **Thresholds live in `policy.toml`**, not in documents the model reads: the documents explain the rules,
  the gateway enforces them.
- **Approvals are bound to arguments.** The approver sees the exact call; a hash guarantees that is what runs.
- **MCP servers act as a broad service account**, as many real integrations do. That is exactly why
  enforcement sits in the agent platform.
- **Offline by default.** Hashing embeddings, feature reranker and the simulated LLM make the whole system,
  tests and benchmark run in CI without keys or model downloads. Swap in sentence-transformers
  (`AEGIS_EMBEDDER=sentence-transformers`, `AEGIS_RERANKER=cross-encoder`) and a real LLM for production.

## Limitations and next steps

- Authentication is simulated with an `X-User-Id` header; production needs SSO/OIDC.
- Adversarial evaluation (hostile documents and tool output) is not included in this version. The
  gateway's controls are designed to hold regardless of what the model is persuaded to propose, and an
  adversarial suite is the natural next milestone (see `docs/security-model.md`).
- The simulated LLM covers the benchmark's intent families; real-model runs are needed for model-quality claims.
- Runs execute inside the request; long workflows would move to a job queue (e.g. Redis + worker).
- Vector search is an in-memory numpy index (FAISS used if installed); pgvector is the obvious production store.

## Resume summary

> **Aegis — Secure Agentic Enterprise Workflow Platform.** Built an agentic workflow platform in Python
> (FastAPI, MCP, hybrid RAG) in which LLM agents plan tasks, call enterprise tools through four MCP servers,
> and execute policy-constrained workflows with human approval. Implemented role- and resource-based
> authorization, a deterministic risk engine, plan-conformance checks, data-egress rules, argument-bound
> approvals and full execution tracing; benchmarked on 100 workflows against an ungoverned baseline
> (0 vs 11 high-risk actions executed without approval).
