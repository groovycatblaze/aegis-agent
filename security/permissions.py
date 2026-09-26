"""Permission engine: role-based (RBAC) and attribute/resource-based (ABAC)
authorisation of tool calls. The principal always comes from the authenticated
session, never from the model."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from core import enterprise as ent

POLICY_PATH = Path(__file__).resolve().parent / "policy.toml"


@lru_cache(maxsize=4)
def load_policy(path: str | None = None) -> dict:
    with open(path or POLICY_PATH, "rb") as f:
        return tomllib.load(f)


@dataclass
class Check:
    name: str
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "reason": self.reason}


def tools_for_role(role: str, policy: dict | None = None, _seen: frozenset = frozenset()) -> set[str]:
    policy = policy or load_policy()
    out: set[str] = set()
    for entry in policy["roles"].get(role, []):
        if entry.startswith("@"):
            parent = entry[1:]
            if parent not in _seen:
                out |= tools_for_role(parent, policy, _seen | {role})
        else:
            out.add(entry)
    return out


def rbac_check(principal: dict, tool: str) -> Check:
    allowed = tools_for_role(principal["role"])
    if tool in allowed:
        return Check("rbac", True, f"role '{principal['role']}' may use {tool}")
    return Check("rbac", False, f"role '{principal['role']}' is not permitted to use {tool}")


# ------------------------------------------------------------------ ABAC
def _is_report(principal: dict, employee_id: str) -> bool:
    emp = ent.get_employee(employee_id)
    return bool(emp and emp["manager_id"] == principal["id"])


def _employee_scope(principal: dict, target: str | None, extra_roles: tuple[str, ...] = ()) -> Check:
    if not target:
        return Check("abac", True, "no target employee")
    if target == principal["id"]:
        return Check("abac", True, "own record")
    if principal["role"] in extra_roles:
        return Check("abac", True, f"role '{principal['role']}' may access other employees")
    if _is_report(principal, target):
        return Check("abac", True, f"{target} is a direct report")
    return Check("abac", False, f"{principal['id']} may not access records of {target}")


def abac_check(principal: dict, tool: str, args: dict[str, Any]) -> Check:
    pid, role = principal["id"], principal["role"]

    if tool == "get_employee":
        return _employee_scope(principal, args.get("employee_id"), ("hr_admin",))
    if tool == "get_manager":
        return _employee_scope(principal, args.get("employee_id"), ("hr_admin",))
    if tool == "get_asset":
        return _employee_scope(principal, args.get("employee_id"), ("hr_admin", "it_admin"))
    if tool == "list_tickets":
        return _employee_scope(principal, args.get("requester_id"), ("it_admin",))
    if tool == "list_expenses":
        return _employee_scope(principal, args.get("employee_id"), ("finance",))
    if tool == "get_direct_reports":
        if args.get("manager_id") == pid or role == "hr_admin":
            return Check("abac", True, "own team")
        return Check("abac", False, "may only list your own direct reports")
    if tool == "create_ticket":
        return _employee_scope(principal, args.get("requester_id"), ("it_admin",))
    if tool == "submit_expense":
        if args.get("employee_id") == pid:
            return Check("abac", True, "own claim")
        return Check("abac", False, "expense claims can only be submitted for yourself")

    if tool in ("get_ticket", "update_ticket", "close_ticket"):
        tid = args.get("ticket_id", "")
        owner = ent.get_ticket_owner(tid)
        if owner is None:
            return Check("abac", True, f"ticket {tid} not found (system will reject)")
        if role == "it_admin":
            return Check("abac", True, "IT admin")
        if owner == pid:
            if tool == "update_ticket" and args.get("status") in ("in_progress", "resolved"):
                return Check("abac", False, "only the IT service desk can set a ticket to in_progress/resolved")
            return Check("abac", True, "own ticket")
        if tool == "get_ticket" and _is_report(principal, owner):
            return Check("abac", True, "ticket of a direct report")
        return Check("abac", False, f"ticket {tid} belongs to {owner}")

    if tool == "get_expense":
        xid = args.get("expense_id", "")
        owner = ent.get_expense_owner(xid)
        if owner is None:
            return Check("abac", True, f"expense {xid} not found (system will reject)")
        if owner == pid or role == "finance" or _is_report(principal, owner):
            return Check("abac", True, "authorised viewer")
        return Check("abac", False, f"expense {xid} belongs to {owner}")

    if tool == "approve_expense":
        xid = args.get("expense_id", "")
        owner = ent.get_expense_owner(xid)
        if args.get("approver_id") != pid:
            return Check("abac", False, "approver_id must be the authenticated user")
        if owner == pid:
            return Check("abac", False, "separation of duties: you cannot approve your own claim")
        return Check("abac", True, "finance approver, not the claimant")

    if tool == "delete_record":
        needed = {"ticket": "it_admin", "expense": "finance"}.get(args.get("record_type", ""))
        if needed and role == needed:
            return Check("abac", True, f"{role} may delete {args.get('record_type')} records")
        return Check("abac", False, f"deleting {args.get('record_type')} records requires role {needed}")

    if tool == "send_notification":
        if ent.get_employee(args.get("employee_id", "")):
            return Check("abac", True, "internal recipient")
        return Check("abac", False, "recipient is not an employee")

    return Check("abac", True, "no resource-level rule")
