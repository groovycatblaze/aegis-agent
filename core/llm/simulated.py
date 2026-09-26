"""SimulatedLLM - a deterministic, offline stand-in for a tool-calling model.

It lets the whole platform (planning, MCP tool calls, gateway, approvals,
tracing, evaluation) run in CI and on a laptop with no API key. It is a
scripted policy, not a language model: it classifies the request with
keyword rules, extracts slots with regexes, and follows a fixed workflow per
intent, reading facts (asset age, policy text, calculated amounts) from real
tool results. Benchmark numbers produced with it measure the *system*, not
model intelligence - use AEGIS_LLM_PROVIDER=openai|anthropic for that.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.prompts import EXECUTOR_MARKER, PLANNER_MARKER
from core.llm.base import LLMResponse, ToolCall, Usage, estimate_tokens
from rag.embeddings import tokenize

AMOUNT = r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)"
TICKET_RE = re.compile(r"\bIT-\d{4}\b", re.I)
EXPENSE_RE = re.compile(r"\bEXP-\d{4}\b", re.I)
CITIES = ["mumbai", "delhi", "new delhi", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune",
          "gurugram", "noida", "jaipur", "kochi", "ahmedabad", "lucknow", "chandigarh", "indore", "coimbatore",
          "nagpur", "bhubaneswar", "visakhapatnam", "goa", "mysuru", "nashik", "vadodara", "surat", "madurai"]
FAILURE_WORDS = r"(fail|failing|failed|broken|dead|won'?t boot|not booting|doesn'?t boot|stopped working|" \
                r"swollen|swelling|cracked|damaged|won'?t turn on)"
APPS = ["github", "jira", "salesforce", "figma", "tableau", "aws", "vpn", "confluence", "slack", "snowflake",
        "datadog", "hubspot", "notion", "postman"]
PERIPHERALS = ["monitor", "keyboard", "mouse", "headset", "docking station", "dock", "webcam"]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _amount_after(text: str, keywords: str) -> float | None:
    m = re.search(rf"(?:{keywords})[^₹\d]{{0,25}}(?:₹|rs\.?|inr)?\s*([\d,]{{3,}})", text, re.I)
    return _num(m.group(1)) if m else None


# ---------------------------------------------------------------- intents
def classify(text: str) -> str:
    t = text.lower()
    if EXPENSE_RE.search(text):
        return "expense_status"
    if TICKET_RE.search(text):
        if re.search(r"\b(close|resolved|fixed|no longer)\b", t):
            return "ticket_close"
        if re.search(r"\b(comment|note|add)\b", t):
            return "ticket_comment"
        return "ticket_status"
    if re.search(r"\bmy (open )?tickets\b|tickets (have i|did i)|tickets i (raised|opened)", t):
        return "my_tickets"
    if re.search(r"\bmy (expenses|expense claims|claims)\b", t):
        return "my_expenses"
    if re.search(r"locked out|password|reset my|\bmfa\b|can'?t log ?in|cannot log ?in|login", t) and re.search(
            r"locked|reset|can'?t|cannot|expired|not working|isn'?t working|forgot", t):
        return "account_issue"
    if re.search(r"\b(laptop|computer|notebook)\b", t) and re.search(
            rf"replace|replacement|new laptop|upgrade|slow|refresh|{FAILURE_WORDS}", t):
        return "laptop_replacement"
    if any(p in t for p in PERIPHERALS) and re.search(r"\b(need|request|get|want|order)\b", t):
        return "peripheral_request"
    if re.search(r"\baccess (to|for)\b|need access|grant me|permission to use", t):
        return "access_request"
    if re.search(r"\b(trip|travel|travelled|traveled|flight|airfare)\b", t) and re.search(
            r"reimburs|claim|expense|submit|estimate|how much", t):
        return "travel_reimbursement"
    if re.search(r"(expense|reimburs|claim)", t) and re.search(AMOUNT, t):
        return "expense_check"
    if re.search(r"my team|direct reports|who reports to me", t):
        return "team_lookup"
    if re.search(r"(let|tell|inform|notify|email|message) my manager", t):
        return "notify_manager"
    return "policy_question"


PLANS: dict[str, list[tuple[str, str]]] = {
    "policy_question": [("search_policy", "find the governing policy")],
    "laptop_replacement": [("search_policy", "laptop replacement eligibility and tiers"),
                           ("get_employee", "requester's department"), ("get_asset", "current laptop age"),
                           ("create_ticket", "raise hardware_replacement ticket if eligible"),
                           ("send_notification", "confirm to the employee")],
    "travel_reimbursement": [("search_policy", "travel policy caps"),
                             ("calculate_reimbursement", "compute eligible amount"),
                             ("submit_expense", "submit the claim if asked")],
    "expense_check": [("search_policy", "expense limits"), ("check_expense_policy", "check the amount")],
    "ticket_status": [("get_ticket", "read ticket")],
    "my_tickets": [("list_tickets", "list the user's tickets")],
    "ticket_comment": [("update_ticket", "add a comment")],
    "ticket_close": [("close_ticket", "close the ticket")],
    "access_request": [("search_policy", "access policy"), ("get_manager", "manager to notify"),
                       ("create_ticket", "raise access_request ticket"),
                       ("send_notification", "notify manager")],
    "account_issue": [("search_policy", "SLA for lockouts"), ("create_ticket", "raise account ticket")],
    "peripheral_request": [("search_policy", "peripherals policy"), ("create_ticket", "raise hardware_request")],
    "notify_manager": [("get_manager", "manager's address"), ("send_email", "e-mail the manager")],
    "expense_status": [("get_expense", "read claim")],
    "my_expenses": [("list_expenses", "list claims")],
    "team_lookup": [("get_direct_reports", "list reports")],
}

SEARCH_QUERIES = {
    "laptop_replacement": "laptop replacement eligibility 36 months hardware tiers cost approval",
    "travel_reimbursement": "travel reimbursement hotel limit per night meal per diem city tier",
    "access_request": "software access request manager notified turnaround",
    "account_issue": "account lockout password reset priority",
    "peripheral_request": "peripherals monitor keyboard headset request ticket",
}


def expense_category(t: str) -> str:
    t = t.lower()
    if "client" in t or "customer" in t:
        return "client_entertainment"
    if re.search(r"team (lunch|dinner|outing|event)|offsite|team-building", t):
        return "team_event"
    if re.search(r"training|course|certification|conference ticket|workshop", t):
        return "training"
    if re.search(r"internet|broadband|wifi", t):
        return "internet"
    if re.search(r"chair|desk|monitor|equipment", t):
        return "equipment"
    if re.search(r"lunch|dinner|meal|breakfast|food", t):
        return "meal"
    return "other"


def best_sentence(query: str, results: list[dict]) -> tuple[str, dict | None]:
    q = set(tokenize(query))
    best, best_score, src = "", -1.0, None
    for rank, r in enumerate(results[:3]):
        for s in re.split(r"(?<=[.!?])\s+|\n", r["text"]):
            if len(s.split()) < 4:
                continue
            ov = len(q & set(tokenize(s))) - 0.15 * rank + (0.3 if re.search(r"\d", s) else 0)
            if ov > best_score:
                best, best_score, src = s.strip(), ov, r
    return best, src


def cite(r: dict | None) -> str:
    return f"({r['title']} v{r['version']}, {r['section']})" if r else ""


# ---------------------------------------------------------------- the model
class SimulatedLLM:
    name = "simulated"

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       json_mode: bool = False) -> LLMResponse:
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        request = next((m["content"] for m in messages if m["role"] == "user"), "")
        pid = (re.search(r"principal_id=(E\d{4})", system) or [None, "E0000"])[1]
        prompt_tokens = sum(estimate_tokens(json.dumps(m)) for m in messages) + \
            sum(estimate_tokens(json.dumps(t)) for t in tools or [])

        if system.startswith(PLANNER_MARKER):
            intent = classify(request)
            plan = {"intent": intent, "summary": f"Handle a {intent.replace('_', ' ')} request.",
                    "steps": [{"tool": t, "purpose": p} for t, p in PLANS[intent]]}
            text = json.dumps(plan)
            return LLMResponse(text, [], Usage(prompt_tokens, estimate_tokens(text), True), self.name)

        if not system.startswith(EXECUTOR_MARKER):
            text = "I can help with IT, HR, expense and travel requests."
            return LLMResponse(text, [], Usage(prompt_tokens, estimate_tokens(text), True), self.name)

        results = self._results(messages)
        allowed = {t["name"] for t in tools or []}
        action = self._next(classify(request), request, pid, results)
        if isinstance(action, ToolCall) and action.name in allowed:
            return LLMResponse(None, [action], Usage(prompt_tokens, estimate_tokens(json.dumps(action.arguments)), True),
                               self.name)
        if isinstance(action, ToolCall):  # tool not exposed to this user
            action = f"I don't have access to the {action.name} tool for your role, so I can't complete this."
        return LLMResponse(action, [], Usage(prompt_tokens, estimate_tokens(action), True), self.name)

    @staticmethod
    def _results(messages: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for m in messages:
            if m["role"] == "tool" and m["tool_call_id"].startswith("sim_"):
                try:
                    out[m["tool_call_id"][4:]] = json.loads(m["content"])
                except json.JSONDecodeError:
                    out[m["tool_call_id"][4:]] = {"ok": False, "error": m["content"]}
        return out

    # Each workflow returns the next ToolCall, or the final answer string.
    def _next(self, intent: str, req: str, pid: str, R: dict[str, dict]) -> ToolCall | str:
        def call(key: str, name: str, **args: Any) -> ToolCall:
            return ToolCall(f"sim_{key}", name, args)

        def ok(key: str) -> dict | None:
            r = R.get(key)
            return r["result"] if r and r.get("ok") else None

        def failed(key: str) -> str | None:
            r = R.get(key)
            return r.get("error") if r and not r.get("ok") else None

        t = req.lower()
        fn = getattr(self, f"_wf_{intent}")
        return fn(req, t, pid, R, call, ok, failed)

    # ---- workflows ---------------------------------------------------
    def _wf_policy_question(self, req, t, pid, R, call, ok, failed):
        if "search" not in R:
            return call("search", "search_policy", query=req, k=4)
        res = ok("search")
        if not res or not res["results"]:
            return "I couldn't find a policy that answers this. Please contact hr@veridian.example."
        sentence, src = best_sentence(req, res["results"])
        return f"{sentence} {cite(src)}"

    def _wf_laptop_replacement(self, req, t, pid, R, call, ok, failed):
        if "search" not in R:
            return call("search", "search_policy", query=SEARCH_QUERIES["laptop_replacement"], k=4)
        if "emp" not in R:
            return call("emp", "get_employee", employee_id=pid)
        if "asset" not in R:
            return call("asset", "get_asset", employee_id=pid)
        emp, asset = ok("emp"), ok("asset")
        if not emp or not asset:
            return f"I couldn't read your employee or asset record: {failed('emp') or failed('asset')}"
        policy = (ok("search") or {}).get("results", [])
        policy_text = " ".join(r["text"] for r in policy)
        src = next((r for r in policy if r["doc_id"] == "it-hardware-policy"), policy[0] if policy else None)
        m = re.search(r"(\d+) months old", policy_text)
        min_age = int(m.group(1)) if m else 36
        failure = re.search(FAILURE_WORDS, t)
        age = asset["age_months"]
        if age < min_age and not failure:
            return (f"Your laptop {asset['id']} ({asset['model']}) is {age} months old. Replacement is available "
                    f"from {min_age} months, or earlier only for a verified hardware failure {cite(src)}. "
                    f"You will be eligible in about {min_age - age} months. I have not raised a ticket; "
                    f"IT can try an upgrade or re-image if performance is the issue.")
        tier = "Performance" if emp["department"] == "Engineering" else "Standard"
        cm = re.search(rf"{tier} tier:.*?INR ([\d,]+)", policy_text)
        cost = int(_num(cm.group(1))) if cm else (92000 if tier == "Performance" else 68000)
        if "swollen" in t or "swelling" in t:
            priority = "critical"
        elif failure:
            priority = "high"
        else:
            priority = "medium"
        if "ticket" not in R:
            reason = "hardware failure reported" if failure else f"laptop is {age} months old"
            return call("ticket", "create_ticket", requester_id=pid, category="hardware_replacement",
                        title=f"Laptop replacement for {emp['name']}",
                        description=f"Asset {asset['id']} ({asset['model']}), {age} months old; {reason}. "
                                    f"Requested {tier} tier. User said: {req}",
                        priority=priority, estimated_cost_inr=cost)
        ticket = ok("ticket")
        if not ticket:
            return f"You are eligible for a replacement {cite(src)}, but the ticket was not created: {failed('ticket')}"
        if "notify" not in R:
            return call("notify", "send_notification", employee_id=pid,
                        message=f"Your laptop replacement ticket {ticket['id']} has been created ({tier} tier).")
        return (f"You are eligible for a replacement {cite(src)}. I created ticket {ticket['id']} "
                f"(hardware_replacement, priority {priority}) for a {tier} tier laptop, estimated INR {cost:,}, "
                f"and sent you a notification. Please return the old laptop within 10 business days of "
                f"receiving the new one.")

    def _wf_travel_reimbursement(self, req, t, pid, R, call, ok, failed):
        city = next((c for c in sorted(CITIES, key=len, reverse=True) if c in t), None)
        nights_m = re.search(r"(\d+)\s*nights?", t)
        days_m = re.search(r"(\d+)\s*days?", t)
        nights = int(nights_m.group(1)) if nights_m else (max(int(days_m.group(1)) - 1, 0) if days_m else 1)
        airfare = _amount_after(req, r"flights?|airfare|air tickets?|plane tickets?") or 0
        hotel = _amount_after(req, r"hotel|stay|accommodation") or 0
        local = _amount_after(req, r"cabs?|taxis?|local transport|local travel|uber|ola") or 0
        submit = bool(re.search(r"\b(submit|file|raise|claim it|prepare the (reimbursement )?(request|claim))\b", t)) \
            and not re.search(r"(don'?t|do not|not) submit|just (estimate|check)|only (estimate|check)", t)
        if not city:
            return "Which city did you travel to? I need it to apply the right hotel and per-diem limits."
        if "search" not in R:
            return call("search", "search_policy", query=SEARCH_QUERIES["travel_reimbursement"], k=4)
        if "calc" not in R:
            return call("calc", "calculate_reimbursement", city=city.title(), nights=nights, airfare_inr=airfare,
                        hotel_per_night_inr=hotel, local_transport_inr=local)
        calc = ok("calc")
        src = next((r for r in (ok("search") or {}).get("results", []) if r["doc_id"] == "travel-policy"), None)
        if not calc:
            return f"I couldn't calculate the reimbursement: {failed('calc')}"
        b = calc["breakdown"]
        summary = (f"For {calc['city']} (tier {calc['city_tier']}, {calc['nights']} nights) the reimbursable total is "
                   f"INR {calc['eligible_total_inr']:,}: airfare {b['airfare']:,}, hotel {b['hotel']:,}, "
                   f"meal per diem {b['meals_per_diem']:,}, local transport {b['local_transport']:,} {cite(src)}.")
        if calc["notes"]:
            summary += " " + " ".join(calc["notes"])
        if not submit:
            return summary + " I have not submitted anything; ask me to submit it when you're ready."
        if "submit" not in R:
            return call("submit", "submit_expense", employee_id=pid, category="travel",
                        amount_inr=calc["eligible_total_inr"],
                        description=f"Business trip to {calc['city']}, {calc['nights']} nights")
        exp = ok("submit")
        if not exp:
            return summary + f" The claim was not submitted: {failed('submit')}"
        return summary + f" I submitted claim {exp['id']} for INR {exp['amount_inr']:,}."

    def _wf_expense_check(self, req, t, pid, R, call, ok, failed):
        m = re.search(AMOUNT, req, re.I)
        amount = _num(m.group(1)) if m else 0
        att = re.search(r"(\d+)\s*(people|persons|of us|colleagues|guests|attendees|members)", t) or \
            re.search(r"for (\d+)\b", t)
        attendees = int(att.group(1)) if att else 1
        category = expense_category(t)
        if "search" not in R:
            return call("search", "search_policy", query=f"{category.replace('_', ' ')} expense limit per person", k=4)
        if "check" not in R:
            return call("check", "check_expense_policy", category=category, amount_inr=amount, attendees=attendees)
        c = ok("check")
        src = next((r for r in (ok("search") or {}).get("results", []) if r["doc_id"] == "expense-policy"), None)
        if not c:
            return f"I couldn't check that expense: {failed('check')}"
        verdict = "is within" if c["within_limit"] else "exceeds"
        cap = c.get("cap_per_person_inr") or c.get("cap_inr")
        cap_txt = f" (limit INR {cap:,}{' per person' if 'cap_per_person_inr' in c else ''})" if cap else ""
        text = (f"A {category.replace('_', ' ')} expense of INR {c['amount_inr']:,} for {c['attendees']} "
                f"{verdict} policy{cap_txt} {cite(src)}. Maximum reimbursable: INR {c['max_reimbursable_inr']:,}.")
        if c["requires_manager_approval"]:
            text += " It needs manager approval because it is above INR 50,000."
        return text

    def _wf_ticket_status(self, req, t, pid, R, call, ok, failed):
        tid = TICKET_RE.search(req).group(0).upper()
        if "get" not in R:
            return call("get", "get_ticket", ticket_id=tid)
        tk = ok("get")
        if not tk:
            return f"I couldn't read {tid}: {failed('get')}"
        return (f"{tk['id']} '{tk['title']}' is {tk['status']} with priority {tk['priority']} "
                f"(last updated {tk['updated_at']}).")

    def _wf_my_tickets(self, req, t, pid, R, call, ok, failed):
        if "list" not in R:
            return call("list", "list_tickets", requester_id=pid)
        res = ok("list")
        if not res:
            return f"I couldn't list your tickets: {failed('list')}"
        if not res["tickets"]:
            return "You have no tickets."
        return "Your tickets: " + "; ".join(f"{x['id']} {x['title']} ({x['status']}, {x['priority']})"
                                            for x in res["tickets"])

    def _wf_ticket_comment(self, req, t, pid, R, call, ok, failed):
        tid = TICKET_RE.search(req).group(0).upper()
        m = re.search(r"[:\"“](.+?)[\"”]?\s*$", req[TICKET_RE.search(req).end():].strip(), re.S)
        comment = (m.group(1) if m else req).strip().strip('"“”')
        if "upd" not in R:
            return call("upd", "update_ticket", ticket_id=tid, comment=comment, author_id=pid)
        tk = ok("upd")
        return f"I added your comment to {tid}." if tk else f"I couldn't update {tid}: {failed('upd')}"

    def _wf_ticket_close(self, req, t, pid, R, call, ok, failed):
        tid = TICKET_RE.search(req).group(0).upper()
        if "close" not in R:
            return call("close", "close_ticket", ticket_id=tid, resolution=f"Closed at requester's request: {req}")
        tk = ok("close")
        return f"{tid} is now closed." if tk else f"I couldn't close {tid}: {failed('close')}"

    def _wf_access_request(self, req, t, pid, R, call, ok, failed):
        app = next((a for a in APPS if a in t), None)
        app_name = {"aws": "AWS", "vpn": "VPN", "github": "GitHub", "hubspot": "HubSpot"}.get(app, (app or "the application").title())
        admin = bool(re.search(r"\b(admin|administrator|production)\b", t))
        if "search" not in R:
            return call("search", "search_policy", query=SEARCH_QUERIES["access_request"], k=4)
        if "mgr" not in R:
            return call("mgr", "get_manager", employee_id=pid)
        mgr = ok("mgr")
        if "ticket" not in R:
            return call("ticket", "create_ticket", requester_id=pid, category="access_request",
                        title=f"Access request: {app_name}{' (admin)' if admin else ''}",
                        description=f"Access requested for {app_name}. Business reason from user: {req}",
                        priority="medium")
        tk = ok("ticket")
        if not tk:
            return f"I couldn't raise the access request: {failed('ticket')}"
        if mgr and "notify" not in R:
            return call("notify", "send_notification", employee_id=mgr["id"],
                        message=f"Access request {tk['id']} for {app_name} was raised by your report {pid}.")
        src = next((r for r in (ok("search") or {}).get("results", []) if r["doc_id"] == "it-access-policy"), None)
        turnaround = "up to 5 business days (IT Security review)" if admin else "within 2 business days"
        return (f"I raised {tk['id']} for {app_name} access and notified your manager "
                f"{mgr['name'] if mgr else ''}. Expected turnaround: {turnaround} {cite(src)}.")

    def _wf_account_issue(self, req, t, pid, R, call, ok, failed):
        if "search" not in R:
            return call("search", "search_policy", query=SEARCH_QUERIES["account_issue"], k=4)
        if "ticket" not in R:
            return call("ticket", "create_ticket", requester_id=pid, category="account",
                        title="Account access / password reset", description=req, priority="high")
        tk = ok("ticket")
        src = next((r for r in (ok("search") or {}).get("results", []) if r["doc_id"] == "it-support-sla"), None)
        if not tk:
            return f"I couldn't raise the ticket: {failed('ticket')}"
        return (f"I raised {tk['id']} (category account, priority high). The service desk responds within 2 hours and "
                f"will verify your identity by phone before resetting credentials {cite(src)}. Never share your "
                f"password or one-time codes.")

    def _wf_peripheral_request(self, req, t, pid, R, call, ok, failed):
        item = next((p for p in PERIPHERALS if p in t), "peripheral")
        if "search" not in R:
            return call("search", "search_policy", query=SEARCH_QUERIES["peripheral_request"], k=4)
        if "ticket" not in R:
            return call("ticket", "create_ticket", requester_id=pid, category="hardware_request",
                        title=f"Peripheral request: {item}", description=req, priority="low")
        tk = ok("ticket")
        src = next((r for r in (ok("search") or {}).get("results", []) if r["doc_id"] == "equipment-accessories-policy"), None)
        return (f"I raised {tk['id']} for a {item} from IT stock {cite(src)}." if tk
                else f"I couldn't raise the request: {failed('ticket')}")

    def _wf_notify_manager(self, req, t, pid, R, call, ok, failed):
        if "mgr" not in R:
            return call("mgr", "get_manager", employee_id=pid)
        mgr = ok("mgr")
        if not mgr:
            return f"I couldn't find your manager: {failed('mgr')}"
        m = re.search(r"(?:know|tell|inform|notify|email|message) my manager(?: that)?[:,]?\s*(.+)$", req, re.I | re.S)
        body = (m.group(1) if m else req).strip()
        if "mail" not in R:
            return call("mail", "send_email", to=mgr["email"], subject="Update from your team member",
                        body=f"Hi {mgr['name'].split()[0]},\n\n{body[0].upper() + body[1:]}\n\n(sent via Aegis on behalf of {pid})")
        sent = ok("mail")
        return f"I e-mailed {mgr['name']} ({mgr['email']})." if sent else f"The e-mail was not sent: {failed('mail')}"

    def _wf_expense_status(self, req, t, pid, R, call, ok, failed):
        xid = EXPENSE_RE.search(req).group(0).upper()
        if "get" not in R:
            return call("get", "get_expense", expense_id=xid)
        x = ok("get")
        return (f"{x['id']} ({x['category']}, INR {x['amount_inr']:,}) is {x['status']}." if x
                else f"I couldn't read {xid}: {failed('get')}")

    def _wf_my_expenses(self, req, t, pid, R, call, ok, failed):
        if "list" not in R:
            return call("list", "list_expenses", employee_id=pid)
        res = ok("list")
        if not res:
            return f"I couldn't list your claims: {failed('list')}"
        return "Your claims: " + "; ".join(f"{x['id']} INR {x['amount_inr']:,} ({x['status']})" for x in res["expenses"])

    def _wf_team_lookup(self, req, t, pid, R, call, ok, failed):
        if "team" not in R:
            return call("team", "get_direct_reports", manager_id=pid)
        res = ok("team")
        if not res:
            return f"I couldn't list your team: {failed('team')}"
        return "Your direct reports: " + ", ".join(f"{r['name']} ({r['title']})" for r in res["reports"])
