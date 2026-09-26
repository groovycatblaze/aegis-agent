from core import enterprise as ent
from security.gateway import DefenseConfig, Gateway
from security.permissions import tools_for_role
from security.risk_engine import assess, can_approve

SCHEMAS = {
    "get_ticket": {"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
    "submit_expense": {"type": "object", "properties": {"employee_id": {"type": "string"},
                                                        "category": {"type": "string"},
                                                        "amount_inr": {"type": "number"},
                                                        "description": {"type": "string"}},
                       "required": ["employee_id", "category", "amount_inr", "description"]},
    "send_email": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                    "body": {"type": "string"}}, "required": ["to", "subject", "body"]},
    "delete_record": {"type": "object", "properties": {"record_type": {"type": "string"},
                                                       "record_id": {"type": "string"}},
                      "required": ["record_type", "record_id"]},
}


def emp(eid):
    return ent.get_employee(eid)


def gw(defenses=None):
    return Gateway(SCHEMAS, defenses or DefenseConfig.full())


def test_role_inheritance():
    assert "get_direct_reports" in tools_for_role("manager")
    assert "create_ticket" in tools_for_role("manager")
    assert "delete_record" not in tools_for_role("employee")


def test_own_ticket_allowed_other_ticket_denied():
    g = gw()
    assert g.evaluate("get_ticket", {"ticket_id": "IT-1003"}, emp("E1001"), set()).verdict == "ALLOW"
    d = g.evaluate("get_ticket", {"ticket_id": "IT-1002"}, emp("E1001"), set())
    assert d.verdict == "DENY" and "belongs to E1002" in d.reason


def test_expense_thresholds_route_to_right_approver():
    ra = assess(emp("E1001"), "submit_expense", {"amount_inr": 60000})
    assert ra.level == "HIGH" and ra.approver_id == "E1010"
    ra = assess(emp("E1001"), "submit_expense", {"amount_inr": 250000})
    assert ra.level == "CRITICAL" and ra.approver_role == "finance"
    assert assess(emp("E1001"), "submit_expense", {"amount_inr": 20000}).level == "MEDIUM"


def test_high_risk_call_requires_approval_only_when_enabled():
    args = {"employee_id": "E1001", "category": "travel", "amount_inr": 60000, "description": "trip"}
    assert gw().evaluate("submit_expense", args, emp("E1001"), {"submit_expense"}).verdict == "REQUIRE_APPROVAL"
    no_hitl = gw(DefenseConfig.full().without("approvals"))
    assert no_hitl.evaluate("submit_expense", args, emp("E1001"), {"submit_expense"}).verdict == "ALLOW"


def test_state_change_must_be_planned():
    args = {"employee_id": "E1001", "category": "meal", "amount_inr": 800, "description": "lunch"}
    d = gw().evaluate("submit_expense", args, emp("E1001"), plan_tools=set())
    assert d.verdict == "DENY" and "plan" in d.reason


def test_cannot_submit_for_someone_else():
    args = {"employee_id": "E1002", "category": "meal", "amount_inr": 800, "description": "lunch"}
    assert gw().evaluate("submit_expense", args, emp("E1001"), {"submit_expense"}).verdict == "DENY"


def test_external_email_denied_internal_allowed():
    g = gw()
    ok = {"to": "arjun.mehta@veridian.example", "subject": "Out Friday", "body": "I'll be out on Friday."}
    assert g.evaluate("send_email", ok, emp("E1001"), {"send_email"}).verdict == "ALLOW"
    ext = {**ok, "to": "someone@gmail.com"}
    assert g.evaluate("send_email", ext, emp("E1001"), {"send_email"}).verdict == "DENY"


def test_schema_rejects_unknown_arguments():
    d = gw().evaluate("get_ticket", {"ticket_id": "IT-1003", "extra": 1}, emp("E1001"), set())
    assert d.verdict == "DENY"


def test_baseline_allows_everything():
    d = gw(DefenseConfig.none()).evaluate("delete_record", {"record_type": "ticket", "record_id": "IT-1002"},
                                          emp("E1001"), set())
    assert d.verdict == "ALLOW" and d.risk["level"] == "CRITICAL"


def test_separation_of_duties():
    assert can_approve(emp("E1010"), "E1001", "E1010", None)[0]
    assert not can_approve(emp("E1001"), "E1001", "E1001", None)[0]
    assert not can_approve(emp("E1002"), "E1001", "E1010", None)[0]
    assert can_approve(emp("E1040"), "E1001", None, "finance")[0]


def test_redaction_hides_salary_from_manager_but_not_hr():
    g = gw()
    rec = emp("E1001")
    data, red = g.filter_output(emp("E1010"), "get_employee", {"employee_id": "E1001"}, rec)
    assert data["salary_inr"] == "[REDACTED]" and red
    data, red = g.filter_output(emp("E1030"), "get_employee", {"employee_id": "E1001"}, rec)
    assert data["salary_inr"] == rec["salary_inr"] and not red
