"""Messaging MCP server: e-mail and in-app notifications (written to an outbox)."""
from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from core import enterprise as ent

mcp = FastMCP("notification", log_level="WARNING")


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """Send an e-mail. `to` is one address or a comma-separated list of addresses."""
    recipients = [r.strip() for r in to.split(",") if r.strip()]
    if not recipients:
        raise ValueError("No recipients")
    now = datetime.now().isoformat(timespec="seconds")
    with ent.connect() as c:
        for r in recipients:
            c.execute("INSERT INTO outbox (channel, recipient, subject, body, created_at) VALUES (?,?,?,?,?)",
                      ("email", r, subject, body, now))
        ent.audit(c, "messaging", "send_email", {"to": recipients, "subject": subject})
    return {"sent": True, "recipients": recipients, "subject": subject}


@mcp.tool()
def send_notification(employee_id: str, message: str) -> dict:
    """Send an in-app notification to an employee."""
    if not ent.get_employee(employee_id):
        raise ValueError(f"Employee {employee_id} not found")
    now = datetime.now().isoformat(timespec="seconds")
    with ent.connect() as c:
        c.execute("INSERT INTO outbox (channel, recipient, subject, body, created_at) VALUES (?,?,?,?,?)",
                  ("notification", employee_id, "", message, now))
        ent.audit(c, "messaging", "send_notification", {"to": employee_id})
    return {"sent": True, "employee_id": employee_id}


if __name__ == "__main__":
    mcp.run()
