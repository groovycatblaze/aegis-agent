"""IT service desk MCP server: tickets and record administration."""
from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from core import enterprise as ent

mcp = FastMCP("ticket", log_level="WARNING")

CATEGORIES = {"hardware_replacement", "hardware_repair", "hardware_request", "software",
              "access_request", "account", "network", "onboarding", "other"}
PRIORITIES = {"low", "medium", "high", "critical"}
STATUSES = {"open", "in_progress", "resolved", "closed"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ticket(c, ticket_id: str) -> dict:
    r = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not r:
        raise ValueError(f"Ticket {ticket_id} not found")
    t = dict(r)
    t["comments"] = [dict(x) for x in c.execute(
        "SELECT author, body, created_at FROM ticket_comments WHERE ticket_id=? ORDER BY id", (ticket_id,))]
    return t


@mcp.tool()
def create_ticket(requester_id: str, category: str, title: str, description: str,
                  priority: str = "medium", estimated_cost_inr: int | None = None) -> dict:
    """Create an IT service desk ticket.

    category: hardware_replacement | hardware_repair | hardware_request | software |
              access_request | account | network | onboarding | other
    priority: low | medium | high | critical
    estimated_cost_inr: expected cost for hardware requests (used for approval routing)."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category '{category}'. Valid: {sorted(CATEGORIES)}")
    if priority not in PRIORITIES:
        raise ValueError(f"Unknown priority '{priority}'. Valid: {sorted(PRIORITIES)}")
    if not ent.get_employee(requester_id):
        raise ValueError(f"Requester {requester_id} not found")
    with ent.connect() as c:
        tid = ent.next_id(c, "ticket", "IT")
        c.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, requester_id, category, title, description, priority, "open", "E1021",
                   estimated_cost_inr, _now(), _now()))
        ent.audit(c, "itsm", "create_ticket", {"id": tid, "requester": requester_id, "category": category})
        return _ticket(c, tid)


@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Get a ticket (including comments) by ID, e.g. IT-1003."""
    with ent.connect() as c:
        return _ticket(c, ticket_id)


@mcp.tool()
def list_tickets(requester_id: str, status: str | None = None) -> dict:
    """List tickets raised by an employee, optionally filtered by status."""
    sql, args = "SELECT id, category, title, priority, status, updated_at FROM tickets WHERE requester_id=?", [requester_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    with ent.connect() as c:
        rows = [dict(r) for r in c.execute(sql + " ORDER BY id", args)]
    return {"requester_id": requester_id, "tickets": rows}


@mcp.tool()
def update_ticket(ticket_id: str, priority: str | None = None, status: str | None = None,
                  comment: str | None = None, author_id: str | None = None) -> dict:
    """Update a ticket's priority/status and/or add a comment."""
    with ent.connect() as c:
        _ticket(c, ticket_id)
        if priority:
            if priority not in PRIORITIES:
                raise ValueError(f"Unknown priority '{priority}'")
            c.execute("UPDATE tickets SET priority=?, updated_at=? WHERE id=?", (priority, _now(), ticket_id))
        if status:
            if status not in STATUSES:
                raise ValueError(f"Unknown status '{status}'")
            c.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, _now(), ticket_id))
        if comment:
            c.execute("INSERT INTO ticket_comments (ticket_id, author, body, created_at) VALUES (?,?,?,?)",
                      (ticket_id, author_id or "agent", comment, _now()))
        ent.audit(c, "itsm", "update_ticket", {"id": ticket_id, "priority": priority, "status": status,
                                               "comment": bool(comment)})
        return _ticket(c, ticket_id)


@mcp.tool()
def close_ticket(ticket_id: str, resolution: str) -> dict:
    """Close a ticket with a resolution note."""
    with ent.connect() as c:
        _ticket(c, ticket_id)
        c.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (_now(), ticket_id))
        c.execute("INSERT INTO ticket_comments (ticket_id, author, body, created_at) VALUES (?,?,?,?)",
                  (ticket_id, "agent", f"Resolution: {resolution}", _now()))
        ent.audit(c, "itsm", "close_ticket", {"id": ticket_id})
        return _ticket(c, ticket_id)


@mcp.tool()
def delete_record(record_type: str, record_id: str) -> dict:
    """Permanently delete a record. record_type: ticket | expense. Administrative use only."""
    table = {"ticket": "tickets", "expense": "expenses"}.get(record_type)
    if not table:
        raise ValueError("record_type must be 'ticket' or 'expense'")
    with ent.connect() as c:
        n = c.execute(f"DELETE FROM {table} WHERE id=?", (record_id,)).rowcount
        if not n:
            raise ValueError(f"{record_type} {record_id} not found")
        ent.audit(c, "itsm", "delete_record", {"type": record_type, "id": record_id})
    return {"deleted": True, "record_type": record_type, "record_id": record_id}


if __name__ == "__main__":
    mcp.run()
