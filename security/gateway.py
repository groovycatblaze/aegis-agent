"""The policy enforcement point. Every tool call the model proposes passes
through Gateway.evaluate() before it can reach an MCP server.

Verdicts: ALLOW, REQUIRE_APPROVAL (pause for a human), DENY.
Each defence can be switched off individually for ablation studies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from security import guardrails
from security.permissions import Check, abac_check, load_policy, rbac_check, tools_for_role
from security.risk_engine import assess


@dataclass
class DefenseConfig:
    rbac: bool = True
    abac: bool = True
    schema: bool = True
    plan_conformance: bool = True
    egress: bool = True
    budgets: bool = True
    approvals: bool = True
    redaction: bool = True

    @classmethod
    def full(cls) -> "DefenseConfig":
        return cls()

    @classmethod
    def none(cls) -> "DefenseConfig":
        return cls(**{f.name: False for f in fields(cls)})

    def without(self, name: str) -> "DefenseConfig":
        d = asdict(self)
        d[name] = False
        return DefenseConfig(**d)

    @property
    def enabled(self) -> bool:
        return any(asdict(self).values())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    verdict: str
    risk: dict
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def reason(self) -> str:
        if self.failed:
            return "; ".join(f"[{c.name}] {c.reason}" for c in self.failed)
        return "; ".join(self.risk.get("reasons", []))

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "risk": self.risk, "reason": self.reason,
                "checks": [c.to_dict() for c in self.checks]}


class Gateway:
    def __init__(self, tool_schemas: dict[str, dict], defenses: DefenseConfig | None = None, policy: dict | None = None):
        self.schemas = tool_schemas
        self.defenses = defenses or DefenseConfig.full()
        self.policy = policy or load_policy()

    def exposed_tools(self, principal: dict) -> set[str]:
        """Least-privilege tool exposure: the model only sees tools the user's role may use."""
        if not self.defenses.rbac:
            return set(self.schemas)
        return tools_for_role(principal["role"], self.policy) & set(self.schemas)

    def evaluate(self, tool: str, args: dict[str, Any], principal: dict, plan_tools: set[str],
                 tool_calls: int = 0, side_effects: int = 0) -> Decision:
        d, checks = self.defenses, []
        if tool not in self.schemas:
            return Decision("DENY", {"level": "CRITICAL", "reasons": ["unknown tool"]},
                            [Check("registry", False, f"unknown tool {tool}")])
        if d.rbac:
            checks.append(rbac_check(principal, tool))
        if d.schema:
            checks.append(guardrails.validate_args(self.schemas[tool], args))
        if d.abac:
            checks.append(abac_check(principal, tool, args))
        if d.plan_conformance:
            checks.append(guardrails.plan_conformance(tool, plan_tools, self.policy))
        if d.egress:
            checks.append(guardrails.egress_check(tool, args, self.policy))
        if d.budgets:
            checks.append(guardrails.budget_check(tool, tool_calls, side_effects, self.policy))

        risk = assess(principal, tool, args, self.policy)
        if any(not c.passed for c in checks):
            return Decision("DENY", risk.to_dict(), checks)
        if d.approvals and risk.needs_approval:
            return Decision("REQUIRE_APPROVAL", risk.to_dict(), checks)
        return Decision("ALLOW", risk.to_dict(), checks)

    def filter_output(self, principal: dict, tool: str, args: dict, data: Any) -> tuple[Any, list[str]]:
        if not self.defenses.redaction:
            return data, []
        return guardrails.redact_output(principal, tool, args, data, self.policy)

    def is_side_effect(self, tool: str) -> bool:
        return bool(self.policy["tools"].get(tool, {}).get("side_effect", True))
