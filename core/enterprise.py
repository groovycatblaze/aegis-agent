"""Simulated enterprise systems of record (HRIS, IT service desk, expense system,
messaging outbox) backed by a single SQLite file.

The MCP servers are thin wrappers over these functions. They run as a broad
"service account", exactly like many real integrations do - which is why the
agent-side gateway (security/) must enforce who may do what.
"""
from __future__ import annotations

import json
import random
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from core.config import get_settings

SCHEMA = """
CREATE TABLE employees (
    id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, department TEXT, title TEXT,
    role TEXT, manager_id TEXT, location TEXT, grade TEXT, salary_inr INTEGER,
    phone TEXT, join_date TEXT
);
CREATE TABLE assets (
    id TEXT PRIMARY KEY, employee_id TEXT, type TEXT, model TEXT,
    purchase_date TEXT, cost_inr INTEGER, status TEXT
);
CREATE TABLE tickets (
    id TEXT PRIMARY KEY, requester_id TEXT, category TEXT, title TEXT, description TEXT,
    priority TEXT, status TEXT, assignee TEXT, estimated_cost_inr INTEGER,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE ticket_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, author TEXT, body TEXT, created_at TEXT
);
CREATE TABLE expenses (
    id TEXT PRIMARY KEY, employee_id TEXT, category TEXT, amount_inr INTEGER,
    description TEXT, status TEXT, approved_by TEXT, created_at TEXT
);
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, recipient TEXT, subject TEXT,
    body TEXT, created_at TEXT
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, system TEXT, action TEXT, details TEXT
);
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER);
"""

DOMAIN = "veridian.example"

# id, name, department, title, role, manager_id, location, grade, laptop_age_months
PEOPLE: list[tuple] = [
    ("E1050", "Sanjay Kapoor", "Engineering", "Chief Technology Officer", "manager", None, "Bengaluru", "L8", 30),
    ("E1051", "Anita Desai", "Sales", "VP Sales", "manager", None, "Mumbai", "L7", 20),
    ("E1052", "Rajesh Iyer", "Finance", "Chief Financial Officer", "finance", None, "Mumbai", "L8", 25),
    ("E1053", "Nandini Bose", "HR", "Chief People Officer", "hr_admin", None, "Bengaluru", "L8", 16),
    ("E1010", "Arjun Mehta", "Engineering", "Engineering Manager", "manager", "E1050", "Bengaluru", "L6", 28),
    ("E1001", "Priya Sharma", "Engineering", "Software Engineer II", "employee", "E1010", "Bengaluru", "L3", 40),
    ("E1003", "Aditya Kulkarni", "Engineering", "Senior Software Engineer", "employee", "E1010", "Pune", "L4", 44),
    ("E1004", "Neha Gupta", "Engineering", "Software Engineer I", "employee", "E1010", "Bengaluru", "L2", 12),
    ("E1005", "Rohan Das", "Engineering", "Data Engineer", "employee", "E1010", "Kolkata", "L3", 38),
    ("E1006", "Sneha Pillai", "Engineering", "QA Engineer", "employee", "E1010", "Kochi", "L3", 26),
    ("E1007", "Karan Malhotra", "Engineering", "Site Reliability Engineer", "employee", "E1010", "Gurugram", "L4", 8),
    ("E1011", "Divya Menon", "Sales", "Regional Sales Manager", "manager", "E1051", "Mumbai", "L6", 39),
    ("E1002", "Rahul Verma", "Sales", "Account Executive", "employee", "E1011", "Mumbai", "L3", 18),
    ("E1012", "Ishaan Chopra", "Sales", "Senior Account Executive", "employee", "E1011", "Delhi", "L4", 50),
    ("E1013", "Pooja Reddy", "Sales", "Sales Development Rep", "employee", "E1011", "Hyderabad", "L2", 20),
    ("E1014", "Farhan Sheikh", "Sales", "Account Executive", "employee", "E1011", "Mumbai", "L3", 37),
    ("E1015", "Lakshmi Narayan", "Sales", "Solutions Consultant", "employee", "E1011", "Chennai", "L4", 30),
    ("E1016", "Aman Singh", "Marketing", "Marketing Manager", "manager", "E1051", "Delhi", "L6", 22),
    ("E1017", "Riya Kapoor", "Marketing", "Content Strategist", "employee", "E1016", "Delhi", "L3", 42),
    ("E1018", "Varun Bhat", "Marketing", "Performance Marketer", "employee", "E1016", "Jaipur", "L3", 15),
    ("E1019", "Zoya Khan", "Marketing", "Designer", "employee", "E1016", "Mumbai", "L3", 33),
    ("E1020", "Kavya Nair", "IT", "IT Manager", "it_admin", "E1050", "Bengaluru", "L6", 10),
    ("E1021", "Suresh Babu", "IT", "IT Support Engineer", "it_admin", "E1020", "Chennai", "L3", 14),
    ("E1030", "Meera Iyer", "HR", "HR Business Partner", "hr_admin", "E1053", "Bengaluru", "L5", 24),
    ("E1031", "Tanvi Shah", "HR", "Talent Acquisition Specialist", "employee", "E1030", "Ahmedabad", "L3", 46),
    ("E1040", "Vikram Rao", "Finance", "Finance Manager", "finance", "E1052", "Mumbai", "L6", 19),
    ("E1041", "Swati Joshi", "Finance", "Financial Analyst", "employee", "E1040", "Pune", "L3", 22),
]

SALARY_BY_GRADE = {"L2": 900_000, "L3": 1_450_000, "L4": 2_200_000, "L5": 2_900_000,
                   "L6": 3_800_000, "L7": 5_200_000, "L8": 7_500_000}

SEED_TICKETS = [
    ("IT-1001", "E1001", "software", "VS Code enterprise license renewal",
     "My VS Code enterprise extension license expires next week.", "medium", "open"),
    ("IT-1002", "E1002", "hardware", "Docking station not detected",
     "USB-C dock stopped detecting external monitors after the last update.", "medium", "in_progress"),
    ("IT-1003", "E1001", "network", "VPN disconnects frequently",
     "GlobalProtect VPN drops every 20-30 minutes when working from home.", "high", "open"),
    ("IT-1004", "E1004", "account", "MFA device replacement",
     "Lost my phone, need to re-enrol the authenticator app.", "medium", "resolved"),
    ("IT-1005", "E1005", "software", "Install Docker Desktop",
     "Need Docker Desktop for the data pipeline project.", "low", "open"),
    ("IT-1006", "E1013", "hardware", "Laptop battery swelling",
     "Battery is visibly swollen and the trackpad is lifting.", "critical", "in_progress"),
    ("IT-1007", "E1003", "software", "Printer driver issue",
     "Printer on floor 3 is not printing from macOS.", "medium", "open"),
    ("IT-1008", "E1006", "network", "Wi-Fi drops in conference room B",
     "Wi-Fi in conference room B drops during video calls.", "high", "open"),
    ("IT-1009", "E1012", "hardware", "Keyboard keys sticking",
     "Several keys on the laptop keyboard stick intermittently.", "low", "open"),
]

SEED_EXPENSES = [
    ("EXP-2001", "E1001", "meal", 1200, "Working lunch during release weekend", "approved", "E1010"),
    ("EXP-2002", "E1002", "travel", 18450, "Client visit, Pune (1 night)", "submitted", None),
    ("EXP-2003", "E1012", "client_entertainment", 14000, "Dinner with Acme Retail (3 guests)", "submitted", None),
    ("EXP-2004", "E1014", "travel", 185000, "Customer summit, Singapore", "pending_approval", None),
    ("EXP-2005", "E1001", "training", 25000, "Kubernetes certification exam", "submitted", None),
    ("EXP-2006", "E1017", "travel", 42300, "Marketing conference, Goa", "approved", "E1016"),
]


def _email(name: str) -> str:
    first, last = name.lower().split(" ", 1)
    return f"{first}.{last.replace(' ', '')}@{DOMAIN}"


def _months_ago(months: int, today: date) -> date:
    return today - timedelta(days=int(months * 30.44))


def db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path else get_settings().enterprise_db


@contextmanager
def connect(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def seed(path: str | Path | None = None, today: date | None = None) -> Path:
    """(Re)create the enterprise database with deterministic seed data."""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    today = today or date.today()
    rng = random.Random(42)
    now = datetime.now().isoformat(timespec="seconds")
    with connect(p) as c:
        c.executescript(SCHEMA)
        for i, (eid, name, dept, title, role, mgr, loc, grade, age) in enumerate(PEOPLE):
            salary = SALARY_BY_GRADE[grade] + rng.randrange(0, 200_000, 5_000)
            phone = f"+91-9{rng.randrange(100000000, 999999999)}"
            join = today - timedelta(days=rng.randrange(200, 3000))
            c.execute("INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, name, _email(name), dept, title, role, mgr, loc, grade, salary, phone,
                       join.isoformat()))
            tier = "performance" if dept == "Engineering" else "standard"
            model = "ThinkPad P1 Gen 5" if tier == "performance" else "Latitude 5420"
            cost = 88_000 if tier == "performance" else 64_000
            c.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)",
                      (f"A-{5001 + i}", eid, "laptop", model, _months_ago(age, today).isoformat(), cost, "in_use"))
        for tid, req, cat, title, desc, prio, status in SEED_TICKETS:
            c.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, req, cat, title, desc, prio, status, "E1021", None, now, now))
        c.execute("INSERT INTO ticket_comments (ticket_id, author, body, created_at) VALUES (?,?,?,?)",
                  ("IT-1002", "E1021", "Firmware update scheduled for Thursday.", now))
        for xid, emp, cat, amt, desc, status, appr in SEED_EXPENSES:
            c.execute("INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?)",
                      (xid, emp, cat, amt, desc, status, appr, now))
        c.execute("INSERT INTO counters VALUES ('ticket', 1100), ('expense', 2100)")
    return p


# ---------------------------------------------------------------- helpers
def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def next_id(c: sqlite3.Connection, name: str, prefix: str) -> str:
    val = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0] + 1
    c.execute("UPDATE counters SET value=? WHERE name=?", (val, name))
    return f"{prefix}-{val}"


def audit(c: sqlite3.Connection, system: str, action: str, details: dict[str, Any]) -> None:
    c.execute("INSERT INTO audit_log (ts, system, action, details) VALUES (?,?,?,?)",
              (datetime.now().isoformat(timespec="seconds"), system, action, json.dumps(details)))


def get_employee(employee_id: str, path=None) -> dict | None:
    with connect(path) as c:
        return row(c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone())


def get_ticket_owner(ticket_id: str, path=None) -> str | None:
    with connect(path) as c:
        r = c.execute("SELECT requester_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return r[0] if r else None


def get_expense_owner(expense_id: str, path=None) -> str | None:
    with connect(path) as c:
        r = c.execute("SELECT employee_id FROM expenses WHERE id=?", (expense_id,)).fetchone()
        return r[0] if r else None


def employee_by_email(email: str, path=None) -> dict | None:
    with connect(path) as c:
        return row(c.execute("SELECT * FROM employees WHERE lower(email)=lower(?)", (email,)).fetchone())


def snapshot(path=None) -> dict[str, list[dict]]:
    """Full dump of mutable tables - used by the evaluator to check end state."""
    with connect(path) as c:
        out = {}
        for t in ("tickets", "ticket_comments", "expenses", "outbox", "audit_log"):
            out[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t}")]
        return out


def list_personas(path=None) -> list[dict]:
    with connect(path) as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, title, department, role, manager_id FROM employees ORDER BY id")]
