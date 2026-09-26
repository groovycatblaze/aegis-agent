"""Expense system MCP server: policy calculators, claims and approvals."""
from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from core import business_rules as rules
from core import enterprise as ent

mcp = FastMCP("expense", log_level="WARNING")

CATEGORIES = {"travel", "meal", "team_event", "client_entertainment", "training", "equipment", "internet", "other"}


@mcp.tool()
def calculate_reimbursement(city: str, nights: int, airfare_inr: float = 0, hotel_per_night_inr: float = 0,
                            local_transport_inr: float = 0) -> dict:
    """Calculate the reimbursable amount for a domestic business trip under the travel policy
    (hotel caps and meal per-diem by city tier, local transport cap)."""
    return rules.calculate_reimbursement(city, int(nights), airfare_inr, hotel_per_night_inr, local_transport_inr)


@mcp.tool()
def check_expense_policy(category: str, amount_inr: float, attendees: int = 1) -> dict:
    """Check a proposed expense against policy limits.
    category: meal | team_event | client_entertainment | training | equipment | internet | travel | other"""
    return rules.check_expense(category, amount_inr, attendees)


@mcp.tool()
def submit_expense(employee_id: str, category: str, amount_inr: float, description: str) -> dict:
    """Submit an expense claim for reimbursement on behalf of an employee."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category '{category}'. Valid: {sorted(CATEGORIES)}")
    if amount_inr <= 0:
        raise ValueError("amount_inr must be positive")
    if not ent.get_employee(employee_id):
        raise ValueError(f"Employee {employee_id} not found")
    with ent.connect() as c:
        xid = ent.next_id(c, "expense", "EXP")
        c.execute("INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?)",
                  (xid, employee_id, category, round(amount_inr), description, "submitted", None,
                   datetime.now().isoformat(timespec="seconds")))
        ent.audit(c, "expense", "submit", {"id": xid, "employee": employee_id, "amount": round(amount_inr)})
        return dict(c.execute("SELECT * FROM expenses WHERE id=?", (xid,)).fetchone())


@mcp.tool()
def get_expense(expense_id: str) -> dict:
    """Get an expense claim by ID, e.g. EXP-2003."""
    with ent.connect() as c:
        r = c.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    if not r:
        raise ValueError(f"Expense {expense_id} not found")
    return dict(r)


@mcp.tool()
def list_expenses(employee_id: str) -> dict:
    """List expense claims submitted by an employee."""
    with ent.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM expenses WHERE employee_id=? ORDER BY id", (employee_id,))]
    return {"employee_id": employee_id, "expenses": rows}


@mcp.tool()
def approve_expense(expense_id: str, approver_id: str) -> dict:
    """Approve a pending expense claim (Finance only)."""
    with ent.connect() as c:
        r = c.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if not r:
            raise ValueError(f"Expense {expense_id} not found")
        c.execute("UPDATE expenses SET status='approved', approved_by=? WHERE id=?", (approver_id, expense_id))
        ent.audit(c, "expense", "approve", {"id": expense_id, "approver": approver_id})
        return dict(c.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone())


if __name__ == "__main__":
    mcp.run()
