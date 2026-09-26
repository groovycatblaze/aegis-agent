"""Deterministic guardrails applied around every tool call:

* schema validation     - arguments must match the tool's declared schema
* plan conformance      - state-changing tools must appear in the plan that was
                          made from the user's request before any tool ran
* egress / DLP          - no messages to external domains, no restricted data
                          (compensation, phone numbers, IDs) in outbound text
* budgets               - caps on tool calls and side effects per run
* output redaction      - sensitive HR fields are stripped from tool output
                          unless the principal may see them
"""
from __future__ import annotations

import copy
import re
from typing import Any

from security.permissions import Check, load_policy

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}

PHONE_RE = re.compile(r"(\+91[-\s]?)?\b[6-9]\d{9}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
COMP_WORDS_RE = re.compile(r"\b(salary|salaries|ctc|compensation|payroll|pay\s?scale)\b", re.I)
BIG_NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{2,3})+\b|\b\d{6,}\b")


def validate_args(schema: dict, args: dict[str, Any]) -> Check:
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args or args[req] in (None, ""):
            return Check("schema", False, f"missing required argument '{req}'")
    for k, v in args.items():
        if k not in props:
            return Check("schema", False, f"unexpected argument '{k}'")
        if v is None:
            continue
        spec = props[k]
        types = [spec["type"]] if "type" in spec else [t.get("type") for t in spec.get("anyOf", []) if t.get("type")]
        types = [t for t in types if t != "null"]
        if types and not any(isinstance(v, _TYPES.get(t, object)) and not (t in ("integer", "number") and isinstance(v, bool))
                             for t in types):
            return Check("schema", False, f"argument '{k}' should be {'/'.join(types)}")
    return Check("schema", True, "arguments match schema")


def plan_conformance(tool: str, plan_tools: set[str], policy: dict | None = None) -> Check:
    policy = policy or load_policy()
    if not policy["tools"].get(tool, {}).get("side_effect", True):
        return Check("plan_conformance", True, "read-only tool")
    if tool in plan_tools:
        return Check("plan_conformance", True, "state-changing tool was in the approved plan")
    return Check("plan_conformance", False,
                 f"{tool} changes state but was not in the plan derived from the user's request")


def _sensitive_content(text: str) -> list[str]:
    found = []
    if PHONE_RE.search(text):
        found.append("phone number")
    if PAN_RE.search(text):
        found.append("PAN")
    if COMP_WORDS_RE.search(text) and BIG_NUMBER_RE.search(text):
        found.append("compensation data")
    if len(set(EMAIL_RE.findall(text))) > 5:
        found.append("bulk contact list")
    return found


def egress_check(tool: str, args: dict[str, Any], policy: dict | None = None) -> Check:
    policy = policy or load_policy()
    domain = policy["company"]["domain"].lower()
    if tool == "send_email":
        rcpts = [r.strip().lower() for r in str(args.get("to", "")).split(",") if r.strip()]
        external = [r for r in rcpts if not r.endswith("@" + domain)]
        if external:
            return Check("egress", False, f"external recipients not allowed: {', '.join(external)}")
        found = _sensitive_content(f"{args.get('subject', '')}\n{args.get('body', '')}")
        if found:
            return Check("egress", False, f"outbound message contains restricted data: {', '.join(found)}")
        return Check("egress", True, "internal recipients, no restricted data")
    if tool == "send_notification":
        found = _sensitive_content(str(args.get("message", "")))
        if found:
            return Check("egress", False, f"notification contains restricted data: {', '.join(found)}")
        return Check("egress", True, "no restricted data")
    if tool == "export_employee_directory":
        return Check("egress", True, "bulk export gated by RBAC and approval")
    return Check("egress", True, "not an egress tool")


def budget_check(tool: str, tool_calls: int, side_effects: int, policy: dict | None = None) -> Check:
    policy = policy or load_policy()
    b = policy["budgets"]
    if tool_calls >= b["max_tool_calls"]:
        return Check("budget", False, f"tool-call budget of {b['max_tool_calls']} exhausted")
    if policy["tools"].get(tool, {}).get("side_effect") and side_effects >= b["max_side_effects"]:
        return Check("budget", False, f"side-effect budget of {b['max_side_effects']} exhausted")
    return Check("budget", True, "within budget")


def redact_output(principal: dict, tool: str, args: dict, data: Any, policy: dict | None = None) -> tuple[Any, list[str]]:
    """Strip compensation/contact fields for anyone other than HR or the employee themself."""
    policy = policy or load_policy()
    fields = policy["redaction"]["sensitive_employee_fields"]
    if principal["role"] == "hr_admin":
        return data, []
    redacted: list[str] = []

    def scrub(rec: dict) -> dict:
        if rec.get("id") == principal["id"]:
            return rec
        for f in fields:
            if f in rec:
                rec[f] = "[REDACTED]"
                redacted.append(f"{rec.get('id')}.{f}")
        return rec

    data = copy.deepcopy(data)
    if isinstance(data, dict):
        if tool in ("get_employee",):
            data = scrub(data)
        for key in ("employees", "reports", "results"):
            if isinstance(data.get(key), list):
                data[key] = [scrub(r) if isinstance(r, dict) else r for r in data[key]]
    return data, redacted
