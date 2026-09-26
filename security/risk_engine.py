"""Deterministic risk classification of a proposed tool call.

Base risk comes from policy.toml; contextual rules escalate it using the call's
arguments (amounts, recipients, record types). HIGH and CRITICAL calls need a
human approval; the approver is chosen here, not by the model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from security.permissions import load_policy

LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass
class RiskAssessment:
    level: str
    reasons: list[str] = field(default_factory=list)
    approver_id: str | None = None
    approver_role: str | None = None

    @property
    def needs_approval(self) -> bool:
        return self.level in ("HIGH", "CRITICAL")

    def to_dict(self) -> dict:
        return {"level": self.level, "reasons": self.reasons, "approver_id": self.approver_id,
                "approver_role": self.approver_role}


def _max(a: str, b: str) -> str:
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def _recipients(to: str) -> list[str]:
    return [r.strip().lower() for r in (to or "").split(",") if r.strip()]


def assess(principal: dict, tool: str, args: dict[str, Any], policy: dict | None = None) -> RiskAssessment:
    policy = policy or load_policy()
    th = policy["thresholds"]
    base = policy["tools"].get(tool, {}).get("risk", "HIGH")
    ra = RiskAssessment(base, [f"base risk of {tool} is {base}"])
    role_approver: str | None = None

    if tool == "submit_expense":
        amt = float(args.get("amount_inr") or 0)
        if amt > th["expense_finance_approval_above"]:
            ra.level = _max(ra.level, "CRITICAL")
            ra.reasons.append(f"claim INR {amt:,.0f} exceeds Finance approval threshold "
                              f"INR {th['expense_finance_approval_above']:,}")
            role_approver = "finance"
        elif amt > th["expense_manager_approval_above"]:
            ra.level = _max(ra.level, "HIGH")
            ra.reasons.append(f"claim INR {amt:,.0f} exceeds manager approval threshold "
                              f"INR {th['expense_manager_approval_above']:,}")
    elif tool == "create_ticket":
        cost = float(args.get("estimated_cost_inr") or 0)
        if cost > th["hardware_manager_approval_above"]:
            ra.level = _max(ra.level, "HIGH")
            ra.reasons.append(f"hardware cost INR {cost:,.0f} exceeds manager approval threshold "
                              f"INR {th['hardware_manager_approval_above']:,}")
    elif tool == "send_email":
        rcpts = _recipients(args.get("to", ""))
        if len(rcpts) > th["max_email_recipients"]:
            ra.level = _max(ra.level, "HIGH")
            ra.reasons.append(f"{len(rcpts)} recipients exceeds limit of {th['max_email_recipients']}")
        lists = set(policy["company"].get("distribution_lists", []))
        if any(r in lists for r in rcpts):
            ra.level = _max(ra.level, "HIGH")
            ra.reasons.append("message to a company-wide distribution list")
    elif tool == "delete_record":
        role_approver = {"ticket": "it_admin", "expense": "finance"}.get(args.get("record_type", ""), "it_admin")
        ra.reasons.append("irreversible deletion of a system-of-record entry")

    if ra.needs_approval:
        if ra.level == "CRITICAL" and role_approver:
            ra.approver_role = role_approver
            ra.reasons.append(f"approval required from a second '{role_approver}' user")
        elif principal.get("manager_id"):
            ra.approver_id = principal["manager_id"]
            ra.reasons.append(f"approval required from manager {principal['manager_id']}")
        else:
            ra.approver_role = role_approver or "hr_admin"
            ra.reasons.append(f"no manager on record; approval required from role '{ra.approver_role}'")
    return ra


def can_approve(approver: dict, requester_id: str, approver_id: str | None, approver_role: str | None) -> tuple[bool, str]:
    """Separation of duties + designated approver."""
    if approver["id"] == requester_id:
        return False, "requesters cannot approve their own actions"
    if approver_id and approver["id"] == approver_id:
        return True, "designated approver"
    if approver_role and approver["role"] == approver_role:
        return True, f"holder of role '{approver_role}'"
    return False, "not the designated approver for this action"
