const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

let user = store.get("aegis.user", "E1001");
let mode = "aegis";
let personas = [];
let recKind = "tickets";

const EXAMPLES = [
  "My laptop is failing and I need a replacement before next Monday.",
  "I traveled to Mumbai for 3 nights: flight ₹14,200, hotel ₹9,500 per night, cabs ₹3,000. Please submit my reimbursement.",
  "Can I expense a client dinner of ₹18,000 for 3 people?",
  "How many days of annual leave do I get?",
  "I need access to GitHub for the payments repo.",
  "What's the status of IT-1003?",
  "Let my manager know I'll be out on Friday for a doctor's appointment.",
];

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-User-Id": user, ...(opts.headers || {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

function pill(v, cls) { return `<span class="pill ${esc(cls || v)}">${esc(String(v).replace(/_/g, " "))}</span>`; }
function money(n) { return typeof n === "number" ? "INR " + n.toLocaleString("en-IN") : esc(n); }

// ---------------------------------------------------------------- setup
async function init() {
  personas = await api("/api/personas");
  $("#persona").innerHTML = personas.map((p) =>
    `<option value="${p.id}" ${p.id === user ? "selected" : ""}>${esc(p.name)} · ${esc(p.title)} (${p.role})</option>`).join("");
  $("#persona").onchange = (e) => { user = e.target.value; store.set("aegis.user", user); refreshApprovals(); refreshActiveTab(); };
  $("#examples").innerHTML = EXAMPLES.map((e, i) => `<button class="chip" data-i="${i}">${esc(e.slice(0, 38))}…</button>`).join("");
  $("#examples").onclick = (e) => { const i = e.target.dataset.i; if (i !== undefined) $("#request").value = EXAMPLES[i]; };
  document.querySelectorAll("#mode button").forEach((b) => b.onclick = () => {
    mode = b.dataset.mode;
    document.querySelectorAll("#mode button").forEach((x) => x.classList.toggle("on", x === b));
    modeHint();
  });
  document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
  document.querySelectorAll("#rec-kind button").forEach((b) => b.onclick = () => {
    recKind = b.dataset.kind;
    document.querySelectorAll("#rec-kind button").forEach((x) => x.classList.toggle("on", x === b));
    loadRecords();
  });
  $("#run").onclick = run;
  modeHint();
  try {
    const h = await api("/api/health");
    $("#health").textContent = `LLM: ${h.llm} · MCP: ${h.mcp_servers.length} servers (${h.mcp_transport}) · ${h.tools} tools`;
  } catch { $("#health").textContent = "API unavailable"; }
  refreshApprovals();
  setInterval(refreshApprovals, 5000);
}

function modeHint() {
  $("#mode-hint").textContent = mode === "aegis"
    ? "Aegis mode: every tool call passes the permission, risk and approval gateway."
    : "Baseline mode: same model and tools, no gateway. Every proposed tool call executes.";
}

function showTab(t) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("on", s.id === "tab-" + t));
  refreshActiveTab();
}
function refreshActiveTab() {
  const t = document.querySelector(".tabs button.on").dataset.tab;
  if (t === "approvals") loadApprovals();
  if (t === "records") loadRecords();
  if (t === "runs") loadRuns();
  if (t === "eval") loadEval();
}

// ---------------------------------------------------------------- runs
async function run() {
  const text = $("#request").value.trim();
  if (!text) return;
  $("#run").disabled = true;
  $("#result").innerHTML = `<div class="card muted">Planning and executing…</div>`;
  try {
    const r = await api("/api/runs", { method: "POST", body: JSON.stringify({ request: text, mode }) });
    await showRun(r.id);
  } catch (e) {
    $("#result").innerHTML = `<div class="card">${pill("error", "bad")} ${esc(e.message)}</div>`;
  } finally { $("#run").disabled = false; refreshApprovals(); }
}

async function showRun(id) {
  const r = await api(`/api/runs/${id}`);
  renderRun(r);
  renderTrace(r.trace);
}

function renderRun(r) {
  const steps = (r.plan.steps || []).map((s) => `<li><code>${esc(s.tool)}</code> <span class="muted">— ${esc(s.purpose)}</span></li>`).join("");
  const log = r.tool_log.map((t) => `<tr><td><code>${esc(t.tool)}</code></td><td>${pill(t.verdict)}</td><td>${pill(t.risk)}</td>
      <td>${t.ok ? pill("ok") : `<span class="muted">${esc(t.error || "")}</span>`}</td></tr>`).join("");
  const m = r.metrics || {};
  const v = r.verification || {};
  $("#result").innerHTML = `
    <div class="card">
      <div class="row between"><h2>Run ${esc(r.id)}</h2><div>${pill(r.mode, "info")} ${pill(r.status)}</div></div>
      <h3>Plan · ${esc(r.plan.intent || "")}</h3><ol class="plan">${steps || "<li class='muted'>no steps</li>"}</ol>
      ${r.pending_approval ? approvalCard(r.pending_approval) : ""}
      ${r.final_answer ? `<h3>Answer</h3><div class="answer">${esc(r.final_answer)}</div>` : ""}
      ${v.issues && v.issues.length ? `<p>${pill("verification warning", "warn")} ${esc(v.issues.join("; "))}</p>` : ""}
      <h3>Tool calls</h3>
      <div class="table-wrap"><table><tr><th>Tool</th><th>Gateway</th><th>Risk</th><th>Result</th></tr>${log || "<tr><td colspan=4 class=muted>none</td></tr>"}</table></div>
      <p class="hint">${m.llm_calls ?? 0} LLM calls · ${(m.prompt_tokens ?? 0) + (m.completion_tokens ?? 0)} tokens${m.tokens_estimated ? " (estimated)" : ""} · ${m.tool_calls ?? 0} tool calls · ${m.active_ms ?? 0} ms active</p>
    </div>`;
  bindApprovalButtons($("#result"), () => showRun(r.id));
}

function approvalCard(a, withRequest) {
  const me = personas.find((p) => p.id === user);
  const canDecide = a.can_decide ?? (a.requester_id !== user && (a.approver_id === user || (a.approver_role && me && me.role === a.approver_role)));
  const who = a.approver_id ? (personas.find((p) => p.id === a.approver_id)?.name || a.approver_id) : `any '${a.approver_role}' user`;
  const args = Object.entries(a.arguments).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${typeof v === "number" && /inr/.test(k) ? money(v) : esc(v)}</dd>`).join("");
  return `<div class="approval" data-approval="${a.id}">
    <div class="row between"><span class="title">ACTION REQUIRES APPROVAL</span>${pill(a.risk_level)}</div>
    ${withRequest ? `<p class="muted">${esc(a.requester_name || a.requester_id)} asked: “${esc(a.request)}”</p>` : ""}
    <p>Agent wants to run <code>${esc(a.tool)}</code></p>
    <dl class="kv">${args}</dl>
    <p class="muted">${esc(a.reasons.join(" · "))}</p>
    ${a.status !== "pending" ? pill(a.status) : canDecide ? `
      <div class="row"><input class="cmt" placeholder="Comment (optional)" style="flex:1">
      <button class="primary" data-act="approve">Approve</button><button class="danger" data-act="reject">Reject</button></div>`
      : `<p class="hint">Waiting for ${esc(who)}. Switch persona to approve.</p>`}
  </div>`;
}

function bindApprovalButtons(root, after) {
  root.querySelectorAll(".approval button[data-act]").forEach((b) => b.onclick = async () => {
    const card = b.closest(".approval");
    try {
      const r = await api(`/api/approvals/${card.dataset.approval}/decision`, { method: "POST",
        body: JSON.stringify({ decision: b.dataset.act, comment: card.querySelector(".cmt")?.value || "" }) });
      refreshApprovals();
      if (after) await after(r);
    } catch (e) { alert(e.message); }
  });
}

function renderTrace(events) {
  if (!events || !events.length) return;
  const t0 = events[0].ts;
  $("#trace").classList.remove("empty");
  $("#trace").innerHTML = events.map((e) => `<div class="ev ${e.kind}">
      <div class="head"><span class="t">+${((e.ts - t0) * 1000).toFixed(0)}ms</span><span class="k">${esc(e.kind.replace(/_/g, " "))}</span>
      <span>${esc(e.name)}</span>${summary(e)}</div>
      <details><summary>details${e.duration_ms ? ` · ${e.duration_ms} ms` : ""}</summary><pre>${esc(JSON.stringify(e.data, null, 2))}</pre></details></div>`).join("");
}

function summary(e) {
  const d = e.data || {};
  switch (e.kind) {
    case "gateway_decision": return `${pill(d.verdict)} ${pill(d.risk.level)} <span class="s">${esc(d.reason)}</span>`;
    case "tool_result": return d.ok ? pill("ok") + (d.redacted_fields?.length ? ` <span class="s">redacted: ${esc(d.redacted_fields.join(", "))}</span>` : "") : `${pill("error", "bad")} <span class="s">${esc(d.error)}</span>`;
    case "plan": return `<span class="s">${esc((d.plan?.steps || []).map((s) => s.tool).join(" → "))}</span>`;
    case "llm_call": return `<span class="s">${d.tool_calls?.length ? "→ " + esc(d.tool_calls.map((c) => c.name).join(", ")) : "final answer"}</span>`;
    case "approval_requested": return pill("awaiting approval", "warn");
    case "approval_decision": return pill(d.approved ? "approved" : "rejected");
    case "verification": return d.issues?.length ? pill(d.issues.length + " issues", "warn") : pill("grounded", "ok");
    case "tool_exposure": return `<span class="s">${d.tools.length} tools visible, ${d.hidden.length} hidden by role</span>`;
    default: return "";
  }
}

// ---------------------------------------------------------------- other tabs
async function refreshApprovals() {
  try {
    const list = await api("/api/approvals");
    const n = list.filter((a) => a.can_decide).length;
    $("#apr-count").textContent = n;
    $("#apr-count").classList.toggle("hidden", n === 0);
  } catch { /* ignore */ }
}

async function loadApprovals() {
  const list = await api("/api/approvals");
  $("#approvals").innerHTML = list.length ? list.map((a) => approvalCard(a, true)).join("")
    : `<p class="muted">Nothing waiting. High-risk actions requested by this user's reports (or needing this user's role) appear here.</p>`;
  bindApprovalButtons($("#approvals"), async (r) => { await loadApprovals(); showTab("agent"); renderRun(r); const full = await api(`/api/runs/${r.id}`); renderTrace(full.trace); });
}

async function loadRecords() {
  const rows = await api(`/api/records/${recKind}`);
  if (!rows.length) { $("#records").innerHTML = `<p class="muted">No rows.</p>`; return; }
  const cols = Object.keys(rows[0]);
  $("#records").innerHTML = `<table><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${rows.map((r) =>
    `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function loadRuns() {
  const rows = await api("/api/runs");
  $("#runs").innerHTML = rows.length ? `<table><tr><th>Run</th><th>User</th><th>Mode</th><th>Status</th><th>Request</th></tr>${rows.map((r) =>
    `<tr><td><a href="#" data-run="${r.id}">${esc(r.id)}</a></td><td>${esc(r.principal_id)}</td><td>${pill(r.mode, "info")}</td><td>${pill(r.status)}</td><td>${esc(r.request)}</td></tr>`).join("")}</table>`
    : `<p class="muted">No runs yet.</p>`;
  $("#runs").querySelectorAll("a[data-run]").forEach((a) => a.onclick = async (e) => { e.preventDefault(); showTab("agent"); await showRun(a.dataset.run); });
}

const LABELS = {
  task_success: ["Governed task success", "%"], outcome_success: ["End-state correct", "%"],
  approval_recall: ["Approval recall", "%"], approval_precision: ["Approval precision", "%"],
  unapproved_high_risk_actions: ["High-risk actions without approval", "n"], false_denials: ["Benign calls wrongly denied", "n"],
  tool_precision: ["Tool precision", "%"], tool_recall: ["Tool recall", "%"], exact_toolset: ["Exact tool-set match", "%"],
  rag_recall_at_k: ["RAG Recall@4", "%"], rag_mrr: ["RAG MRR", "f"], grounded_rate: ["Grounded answers", "%"],
  avg_latency_ms: ["Avg latency (ms)", "f"], p95_latency_ms: ["p95 latency (ms)", "f"], avg_llm_calls: ["LLM calls / task", "f"],
  avg_tokens: ["Tokens / task", "f"], failure_rate: ["Failure rate", "%"], retry_rate: ["Retry rate", "%"],
};
function fmt(v, kind) {
  if (v === null || v === undefined) return "–";
  if (kind === "%") return (v * 100).toFixed(1) + "%";
  if (kind === "f") return Number(v).toFixed(v < 10 ? 2 : 0);
  return v;
}

async function loadEval() {
  let s;
  try { s = await api("/api/eval/latest"); } catch (e) { $("#eval").innerHTML = `<div class="card muted">${esc(e.message)}</div>`; return; }
  const modes = Object.keys(s.modes);
  const rows = Object.keys(LABELS).filter((k) => k in s.modes[modes[0]]).map((k) =>
    `<tr><td>${LABELS[k][0]}</td>${modes.map((m) => `<td class="num">${fmt(s.modes[m][k], LABELS[k][1])}</td>`).join("")}</tr>`).join("");
  const cats = Object.keys(s.by_category[modes[0]] || {});
  const catRows = cats.map((c) => `<tr><td>${esc(c)}</td><td class="num">${s.by_category[modes[0]][c].n}</td>${modes.map((m) =>
    `<td class="num">${fmt(s.by_category[m][c].success, "%")}</td>`).join("")}</tr>`).join("");
  const rmodes = Object.keys(s.retrieval || {});
  const rRows = rmodes.map((m) => `<tr><td>${esc(m)}</td>${["recall@1", "recall@3", "recall@5", "mrr"].map((k) =>
    `<td class="num">${fmt(s.retrieval[m][k], k === "mrr" ? "f" : "%")}</td>`).join("")}</tr>`).join("");
  $("#eval").innerHTML = `
    <div class="card"><h2>Benchmark · ${s.n_tasks} tasks · LLM: ${esc(s.llm)} · ${esc(s.generated_at)}</h2>
      <div class="table-wrap"><table><tr><th>Metric</th>${modes.map((m) => `<th class="num">${esc(m)}</th>`).join("")}</tr>${rows}</table></div>
      ${(s.notes || []).map((n) => `<p class="hint">${esc(n)}</p>`).join("")}</div>
    <div class="card"><h2>Governed success by category</h2>
      <div class="table-wrap"><table><tr><th>Category</th><th class="num">Tasks</th>${modes.map((m) => `<th class="num">${esc(m)}</th>`).join("")}</tr>${catRows}</table></div></div>
    <div class="card"><h2>Retrieval (${s.retrieval_queries} labelled queries)</h2>
      <div class="table-wrap"><table><tr><th>Retriever</th><th class="num">Recall@1</th><th class="num">Recall@3</th><th class="num">Recall@5</th><th class="num">MRR</th></tr>${rRows}</table></div></div>`;
}

init();
