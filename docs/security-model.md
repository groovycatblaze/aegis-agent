# Security model

## Principle

The language model is treated as an untrusted planner. It can propose any tool call; whether the call runs is
decided by deterministic code using the authenticated identity, a reviewed policy file and the systems of
record. Controls are therefore independent of how well the model behaves.

## Assets

Employee records (compensation and contact data are Restricted), IT tickets, expense claims and approvals,
outbound messages, the audit trail.

## Controls (`security/`)

| Control | Where | What it enforces |
|---|---|---|
| Least-privilege exposure | `Gateway.exposed_tools` | The model only sees tools the user's role can use |
| RBAC | `permissions.rbac_check`, `policy.toml [roles]` | Role → allowed tools, with inheritance |
| Resource authorization (ABAC) | `permissions.abac_check` | Own records only; managers see direct reports' work data; admins by record type; approver must be the session user; no self-approval |
| Schema validation | `guardrails.validate_args` | Required arguments, types, no unknown arguments |
| Plan conformance | `guardrails.plan_conformance` | State-changing tools must be in the plan made from the user's request |
| Egress rules | `guardrails.egress_check` | Company-domain recipients only; restricted data not allowed in outbound messages |
| Budgets | `guardrails.budget_check` | Caps on tool calls and side effects per run |
| Risk engine | `risk_engine.assess` | Base risk per tool plus contextual escalation (amount thresholds, recipients, deletions) |
| Human approval | `executor`, `orchestrator.decide` | HIGH/CRITICAL calls pause; designated approver; separation of duties; approval bound to an argument hash |
| Output redaction | `guardrails.redact_output` | Compensation/contact fields hidden unless HR or self |
| Audit | `core/tracing.py`, enterprise `audit_log` | Every proposal, decision, approval and result is recorded |

## Risk levels

| Level | Examples | Outcome |
|---|---|---|
| LOW | reads, calculators, policy search, notifications | allowed if authorized |
| MEDIUM | create/update tickets, claims ≤ INR 50,000, internal e-mail | allowed if authorized |
| HIGH | claims > INR 50,000, hardware > INR 75,000, mass e-mail, directory export, expense approval | manager approval |
| CRITICAL | claims > INR 2,00,000, record deletion | approval by a second holder of the owning role |

## Residual risk

- An action that is authorized, planned and below every threshold runs without review. Its content (a
  ticket comment, say) is only as good as the model's output.
- Approval quality depends on the reviewer. The approval card shows the exact arguments and the reasons,
  but a reviewer who approves everything removes that layer.
- The MCP servers trust the platform (service account). Direct access to them bypasses the gateway, so
  they must not be reachable from anywhere else.
- Header-based identity is a demo stand-in for SSO.

## Next milestone: adversarial evaluation

Extend the benchmark with tasks where content returned by tools or retrieval is hostile. Measure how often
an attacker's goal is reached in the systems of record, and which control stopped each attempt, for the
baseline and for each single-control ablation (`DefenseConfig.without(...)` already supports this).
