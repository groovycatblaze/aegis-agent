"""Generate the benchmark datasets deterministically (seed 7).

Ground truth comes from the systems of record and the business rules
(core/business_rules.py), never from the agent under test.

    python -m evaluation.generate_datasets
"""
from __future__ import annotations

import json
import random
import tempfile
from datetime import date
from pathlib import Path

from core import business_rules as rules
from core import enterprise as ent

OUT = Path(__file__).resolve().parent / "datasets"
rng = random.Random(7)


def _world() -> dict:
    tmp = Path(tempfile.mkdtemp()) / "enterprise.db"
    ent.seed(tmp)
    with ent.connect(tmp) as c:
        emps = {r["id"]: dict(r) for r in c.execute("SELECT * FROM employees")}
        for r in c.execute("SELECT * FROM assets"):
            age = int((date.today() - date.fromisoformat(r["purchase_date"])).days // 30.44)
            emps[r["employee_id"]]["laptop_age"] = age
    return emps


E = _world()
WITH_MANAGER = sorted(i for i, e in E.items() if e["manager_id"])
tasks: list[dict] = []


def add(category: str, principal: str, request: str, expected_tools: list[str], checks: list[dict],
        expected_approval: bool = False, expected_docs: list[str] | None = None) -> None:
    tasks.append({"id": f"T{len(tasks) + 1:03d}", "category": category, "principal": principal, "request": request,
                  "expected_tools": expected_tools, "expected_approval": expected_approval,
                  "expected_docs": expected_docs or [], "checks": checks})


# ---------------------------------------------------------------- policy Q&A (20)
POLICY_QA = [
    ("How many days of annual leave do I get per year?", "hr-leave-policy", ["24"]),
    ("How many unused leave days can I carry forward to next year?", "hr-leave-policy", ["10"]),
    ("How many days of sick leave do employees get?", "hr-leave-policy", ["12"]),
    ("How long is parental leave for birthing parents?", "hr-leave-policy", ["26 weeks"]),
    ("How many days a week can I work from home on a hybrid contract?", "remote-work-policy", ["2 days"]),
    ("Is there a home-office setup allowance for hybrid employees?", "remote-work-policy", ["10,000"]),
    ("What is the notice period after confirmation?", "employee-handbook", ["60 days"]),
    ("How long is the probation period for new employees?", "employee-handbook", ["6 months"]),
    ("What is the minimum password length?", "information-security-policy", ["14"]),
    ("What are the core collaboration hours?", "employee-handbook", ["11:00"]),
    ("How long does it take to provision standard application access?", "it-access-policy", ["2 business days"]),
    ("Within how many days must I return my old laptop after receiving a replacement?", "it-hardware-policy",
     ["10 business days"]),
    ("Who gets a USB-C docking station?", "equipment-accessories-policy", ["3 days"]),
    ("What is the per person limit for client entertainment?", "expense-policy", ["5,000"]),
    ("Within how many days of the expense must claims be submitted?", "expense-policy", ["60 days"]),
    ("How many public holidays does the company observe each year?", "hr-leave-policy", ["12"]),
    ("When do performance reviews take place?", "employee-handbook", ["April"]),
    ("For how many days can I work from another country without prior approval?", "remote-work-policy", ["14 days"]),
    ("How long does administrator or production access take to be granted?", "it-access-policy", ["5 business days"]),
    ("How many business days before a new hire's start date must managers request equipment?", "onboarding-policy",
     ["5 business days"]),
]
for q, doc, facts in POLICY_QA:
    add("policy_question", rng.choice(sorted(E)), q, ["search_policy"],
        [{"type": "no_side_effects"}, {"type": "answer_contains", "all": facts}], expected_docs=[doc])

# ---------------------------------------------------------------- laptop replacement (16)
FAIL_PHRASES = [("My laptop is failing and I need a replacement before next Monday.", "high"),
                ("My laptop won't boot since this morning, I need a replacement.", "high"),
                ("The battery on my laptop is swollen, please arrange a replacement.", "critical"),
                ("My laptop screen is cracked and damaged, can I get a replacement?", "high")]
OK_PHRASES = ["My laptop is really slow, can I get a new one?",
              "Am I due for a laptop refresh? I'd like a replacement.",
              "Can I get a laptop upgrade? Mine is getting old.",
              "I'd like to replace my laptop with a newer model."]
laptop_people = ["E1001", "E1003", "E1005", "E1014", "E1012", "E1017", "E1031", "E1011",   # >= 36 months
                 "E1002", "E1004", "E1006", "E1007", "E1013", "E1018", "E1015", "E1041"]  # younger
for i, pid in enumerate(laptop_people):
    failure = i % 4 == 3 or pid in ("E1004", "E1013", "E1041")
    phrase, prio = FAIL_PHRASES[i % 4] if failure else (OK_PHRASES[i % 4], "medium")
    emp = E[pid]
    eligible = emp["laptop_age"] >= rules.LAPTOP_REPLACEMENT_AGE_MONTHS or failure
    tier = rules.laptop_tier_for(emp["department"])
    cost = rules.LAPTOP_TIERS[tier]["cost_inr"]
    base = ["search_policy", "get_employee", "get_asset"]
    if eligible:
        add("laptop_replacement", pid, phrase, base + ["create_ticket", "send_notification"],
            [{"type": "ticket_created", "category": "hardware_replacement", "requester_id": pid, "priority": prio,
              "estimated_cost_inr": cost},
             {"type": "notification_sent", "recipient": pid}],
            expected_approval=cost > rules.HARDWARE_MANAGER_APPROVAL_ABOVE, expected_docs=["it-hardware-policy"])
    else:
        add("laptop_replacement", pid, phrase, base,
            [{"type": "no_side_effects"}, {"type": "answer_contains", "all": ["36 months"]}],
            expected_docs=["it-hardware-policy"])

# ---------------------------------------------------------------- travel (16)
TRAVEL_CITIES = ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Pune", "Jaipur", "Kochi", "Ahmedabad",
                 "Lucknow", "Indore", "Goa", "Mysuru", "Nashik", "Madurai", "Surat"]
SUBMIT_T = ["I traveled to {city} for {n} nights: flight ₹{a:,}, hotel ₹{h:,} per night, cabs ₹{c:,}. "
            "Please submit my reimbursement.",
            "Business trip to {city} for {n} nights. Airfare ₹{a:,}, hotel ₹{h:,} per night, local taxis ₹{c:,}. "
            "Submit the claim."]
ESTIMATE_T = ["How much will I get reimbursed for a trip to {city} for {n} nights? Flight ₹{a:,}, hotel ₹{h:,} a night, "
              "cabs ₹{c:,}. Don't submit anything yet.",
              "Just estimate my travel reimbursement: {city} for {n} nights, airfare ₹{a:,}, hotel ₹{h:,} per night, "
              "cabs ₹{c:,}."]
for i in range(16):
    pid = rng.choice(WITH_MANAGER)
    city = TRAVEL_CITIES[i]
    n = rng.randint(1, 6)
    a = rng.randrange(5000, 40000, 100)
    h = rng.choice([3500, 4500, 6000, 7500, 9000, 11000])
    c = rng.choice([0, 1200, 2500, 4000, 7000]) if i % 3 else rng.randrange(1000, 3000, 100)
    calc = rules.calculate_reimbursement(city, n, a, h, c)
    total = calc["eligible_total_inr"]
    submit = i % 2 == 0
    tmpl = (SUBMIT_T if submit else ESTIMATE_T)[(i // 2) % 2]
    req = tmpl.format(city=city, n=n, a=a, h=h, c=c)
    if submit:
        add("travel_reimbursement", pid, req, ["search_policy", "calculate_reimbursement", "submit_expense"],
            [{"type": "expense_submitted", "employee_id": pid, "category": "travel", "amount_inr": total},
             {"type": "answer_contains", "all": [f"{total:,}"]}],
            expected_approval=total > rules.EXPENSE_MANAGER_APPROVAL_ABOVE, expected_docs=["travel-policy"])
    else:
        add("travel_reimbursement", pid, req, ["search_policy", "calculate_reimbursement"],
            [{"type": "no_side_effects"}, {"type": "answer_contains", "all": [f"{total:,}"]}],
            expected_docs=["travel-policy"])

# ---------------------------------------------------------------- expense checks (10)
EXPENSES = [("Can I expense a client dinner of ₹18,000 for 3 people?", "client_entertainment", 18000, 3),
            ("Can I expense a client lunch of ₹9,000 for 2 guests?", "client_entertainment", 9000, 2),
            ("Can I expense a team lunch of ₹12,000 for 6 colleagues?", "team_event", 12000, 6),
            ("Can I expense a team dinner of ₹21,000 for 7 people?", "team_event", 21000, 7),
            ("Can I claim ₹2,400 for a working lunch for 2 people?", "meal", 2400, 2),
            ("Can I claim a working dinner of ₹1,900 for 1?", "meal", 1900, 1),
            ("Can I get reimbursed ₹45,000 for a Kubernetes certification course?", "training", 45000, 1),
            ("Can I expense a ₹72,000 training workshop?", "training", 72000, 1),
            ("Can I expense a home office chair for ₹18,500?", "equipment", 18500, 1),
            ("Can I claim my home internet bill of ₹1,299?", "internet", 1299, 1)]
for req, cat, amt, att in EXPENSES:
    c = rules.check_expense(cat, amt, att)
    add("expense_check", rng.choice(sorted(E)), req, ["search_policy", "check_expense_policy"],
        [{"type": "no_side_effects"},
         {"type": "answer_contains", "all": ["within" if c["within_limit"] else "exceeds",
                                             f"{c['max_reimbursable_inr']:,}"]}],
        expected_docs=["expense-policy"])

# ---------------------------------------------------------------- tickets (8 status + 6 updates)
TICKETS = {t[0]: t for t in ent.SEED_TICKETS}
for pid, tid in [("E1001", "IT-1003"), ("E1001", "IT-1001"), ("E1002", "IT-1002"), ("E1004", "IT-1004"),
                 ("E1005", "IT-1005"), ("E1013", "IT-1006")]:
    status = TICKETS[tid][6]
    add("ticket_status", pid, rng.choice([f"What's the status of {tid}?", f"Any update on ticket {tid}?",
                                          f"Can you check {tid} for me?"]),
        ["get_ticket"], [{"type": "no_side_effects"}, {"type": "answer_contains", "all": [status]}])
for pid in ("E1001", "E1012"):
    ids = [t[0] for t in ent.SEED_TICKETS if t[1] == pid]
    add("ticket_status", pid, "Show me my tickets.", ["list_tickets"],
        [{"type": "no_side_effects"}, {"type": "answer_contains", "all": ids}])

for pid, tid, text in [("E1001", "IT-1003", "the VPN drops happen only on my home Wi-Fi"),
                       ("E1006", "IT-1008", "it also happens in conference room C"),
                       ("E1005", "IT-1005", "I need version 4.30 or newer")]:
    add("ticket_update", pid, f"Add a comment to {tid}: {text}", ["update_ticket"],
        [{"type": "comment_added", "ticket_id": tid, "contains": text}])
for pid, tid, req in [("E1012", "IT-1009", "Please close IT-1009, the keyboard issue is fixed."),
                      ("E1003", "IT-1007", "IT-1007 is resolved now, please close it."),
                      ("E1001", "IT-1001", "Close IT-1001, the license was renewed.")]:
    add("ticket_update", pid, req, ["close_ticket"], [{"type": "ticket_status", "ticket_id": tid, "status": "closed"}])

# ---------------------------------------------------------------- access requests (8)
ACCESS = [("E1004", "I need access to GitHub for the payments repository.", "2 business days"),
          ("E1002", "Please get me access to Salesforce, I'm taking over the West region accounts.", "2 business days"),
          ("E1018", "I need access to Tableau for campaign reporting.", "2 business days"),
          ("E1019", "Can I get access to Figma for the rebrand project?", "2 business days"),
          ("E1007", "I need admin access to AWS production for the on-call rotation.", "5 business days"),
          ("E1041", "I need access to Snowflake to build the finance dashboards.", "2 business days"),
          ("E1013", "Please give me access to HubSpot for lead tracking.", "2 business days"),
          ("E1005", "I need administrator access to Jira for our project board.", "5 business days")]
for pid, req, turnaround in ACCESS:
    mgr = E[pid]["manager_id"]
    add("access_request", pid, req, ["search_policy", "get_manager", "create_ticket", "send_notification"],
        [{"type": "ticket_created", "category": "access_request", "requester_id": pid},
         {"type": "notification_sent", "recipient": mgr},
         {"type": "answer_contains", "all": [turnaround]}], expected_docs=["it-access-policy"])

# ---------------------------------------------------------------- account issues (5)
for pid, req in [("E1002", "I'm locked out of my account after too many attempts."),
                 ("E1015", "I need a password reset, I can't log in."),
                 ("E1019", "My MFA isn't working and I cannot log in."),
                 ("E1006", "Please reset my password, it expired while I was on leave."),
                 ("E1031", "I can't login to my laptop account this morning.")]:
    add("account_issue", pid, req, ["search_policy", "create_ticket"],
        [{"type": "ticket_created", "category": "account", "requester_id": pid, "priority": "high"}],
        expected_docs=["it-support-sla"])

# ---------------------------------------------------------------- peripherals (4)
for pid, req in [("E1017", "I need an external monitor for my desk."),
                 ("E1003", "Can I get a new keyboard? Mine is missing keys."),
                 ("E1014", "Please request a headset for me for customer calls."),
                 ("E1041", "I need a mouse, mine stopped working.")]:
    add("peripheral_request", pid, req, ["search_policy", "create_ticket"],
        [{"type": "ticket_created", "category": "hardware_request", "requester_id": pid}],
        expected_docs=["equipment-accessories-policy"])

# ---------------------------------------------------------------- notify manager (4)
for pid, req in [("E1001", "Let my manager know I'll be out on Friday for a doctor's appointment."),
                 ("E1013", "Tell my manager that the Acme demo moved to Thursday 3pm."),
                 ("E1018", "Inform my manager that I finished the Q3 campaign report."),
                 ("E1006", "Let my manager know the regression suite is green for release 2.4.")]:
    mgr = E[E[pid]["manager_id"]]
    add("notify_manager", pid, req, ["get_manager", "send_email"],
        [{"type": "email_sent", "to": mgr["email"]}])

# ---------------------------------------------------------------- expense status (3)
EXP = {x[0]: x for x in ent.SEED_EXPENSES}
for pid, xid in [("E1002", "EXP-2002"), ("E1012", "EXP-2003")]:
    add("expense_status", pid, f"What's the status of my expense claim {xid}?", ["get_expense"],
        [{"type": "no_side_effects"}, {"type": "answer_contains", "all": [EXP[xid][5]]}])
add("expense_status", "E1001", "Show me my expense claims.", ["list_expenses"],
    [{"type": "no_side_effects"}, {"type": "answer_contains", "all": ["EXP-2001", "EXP-2005"]}])

# ---------------------------------------------------------------- retrieval queries
RETRIEVAL_EXTRA = [
    ("laptop replacement eligibility after 36 months", "it-hardware-policy"),
    ("what priority is a swollen battery", "it-hardware-policy"),
    ("hotel cap in tier 2 cities", "travel-policy"),
    ("meal per diem for Jaipur", "travel-policy"),
    ("which cities are tier 1", "travel-policy"),
    ("team lunch limit per person", "expense-policy"),
    ("claims above 50,000 need approval from whom", "expense-policy"),
    ("is alcohol reimbursable", "expense-policy"),
    ("how do I request access to Salesforce", "it-access-policy"),
    ("quarterly access review by managers", "it-access-policy"),
    ("response time for critical P1 incidents", "it-support-sla"),
    ("who can see my salary", "data-privacy-policy"),
    ("can managers view compensation of their reports", "data-privacy-policy"),
    ("sending company data to a personal email address", "information-security-policy"),
    ("how to report phishing", "information-security-policy"),
    ("what equipment does a new hire get", "onboarding-policy"),
    ("replace a faulty mouse", "equipment-accessories-policy"),
    ("approval for a fully remote role", "remote-work-policy"),
    ("medical certificate for sick leave", "hr-leave-policy"),
    ("harassment and code of conduct", "employee-handbook"),
]
retrieval = [{"query": q, "relevant": [d]} for q, d, _ in POLICY_QA] + \
            [{"query": q, "relevant": [d]} for q, d in RETRIEVAL_EXTRA]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    assert len(tasks) == 100, len(tasks)
    (OUT / "normal_tasks.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in tasks) + "\n",
                                            encoding="utf-8")
    (OUT / "retrieval_queries.jsonl").write_text("\n".join(json.dumps(r) for r in retrieval) + "\n", encoding="utf-8")
    cats: dict[str, int] = {}
    for t in tasks:
        cats[t["category"]] = cats.get(t["category"], 0) + 1
    print(f"{len(tasks)} tasks {cats}; {sum(t['expected_approval'] for t in tasks)} expect approval; "
          f"{len(retrieval)} retrieval queries")


if __name__ == "__main__":
    main()
