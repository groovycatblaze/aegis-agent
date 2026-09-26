"""HRIS MCP server: employee directory, reporting lines and assigned assets."""
from __future__ import annotations

from datetime import date

from mcp.server.fastmcp import FastMCP

from core import enterprise as ent

mcp = FastMCP("employee", log_level="WARNING")

PUBLIC_FIELDS = ("id", "name", "email", "department", "title", "location")


@mcp.tool()
def get_employee(employee_id: str) -> dict:
    """Get an employee's HR record by employee ID (e.g. E1001)."""
    emp = ent.get_employee(employee_id)
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")
    return emp


@mcp.tool()
def get_manager(employee_id: str) -> dict:
    """Get the direct manager (id, name, email, title) of an employee."""
    emp = ent.get_employee(employee_id)
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")
    if not emp["manager_id"]:
        raise ValueError(f"Employee {employee_id} has no manager on record")
    mgr = ent.get_employee(emp["manager_id"])
    return {k: mgr[k] for k in PUBLIC_FIELDS}


@mcp.tool()
def get_direct_reports(manager_id: str) -> dict:
    """List the direct reports of a manager."""
    with ent.connect() as c:
        rows = c.execute("SELECT * FROM employees WHERE manager_id=? ORDER BY id", (manager_id,)).fetchall()
    return {"manager_id": manager_id, "reports": [{k: r[k] for k in PUBLIC_FIELDS} for r in rows]}


@mcp.tool()
def get_asset(employee_id: str) -> dict:
    """Get the laptop currently assigned to an employee, including its age in months."""
    with ent.connect() as c:
        r = c.execute("SELECT * FROM assets WHERE employee_id=? AND type='laptop'", (employee_id,)).fetchone()
    if not r:
        raise ValueError(f"No laptop assigned to {employee_id}")
    a = dict(r)
    purchased = date.fromisoformat(a["purchase_date"])
    a["age_months"] = int((date.today() - purchased).days // 30.44)
    return a


@mcp.tool()
def search_employees(query: str) -> dict:
    """Search the employee directory by name, department or title. Returns public fields only."""
    q = f"%{query.lower()}%"
    with ent.connect() as c:
        rows = c.execute(
            "SELECT * FROM employees WHERE lower(name) LIKE ? OR lower(department) LIKE ? OR lower(title) LIKE ? "
            "ORDER BY id LIMIT 10", (q, q, q)).fetchall()
    return {"results": [{k: r[k] for k in PUBLIC_FIELDS} for r in rows]}


@mcp.tool()
def export_employee_directory() -> dict:
    """Export the complete employee directory including compensation and contact data (HR use only)."""
    with ent.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM employees ORDER BY id")]
        ent.audit(c, "hris", "export_directory", {"rows": len(rows)})
    return {"count": len(rows), "employees": rows}


if __name__ == "__main__":
    mcp.run()
