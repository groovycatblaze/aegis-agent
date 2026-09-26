"""Serializable run state: everything needed to pause for approval and resume later."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class RunState:
    run_id: str
    principal: dict
    request: str
    mode: str
    llm: str
    plan: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    step: int = 0
    tool_calls: int = 0
    side_effects: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: bool = False
    retries: int = 0
    pending: dict | None = None
    approvals: list[dict] = field(default_factory=list)
    status: str = "running"
    final_answer: str = ""
    error: str = ""
    active_ms: float = 0.0
    trace_seq: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "RunState":
        return cls(**json.loads(raw))

    def metrics(self) -> dict:
        return {"llm_calls": self.llm_calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "tokens_estimated": self.tokens_estimated,
                "tool_calls": self.tool_calls, "side_effects": self.side_effects, "retries": self.retries,
                "steps": self.step, "active_ms": round(self.active_ms, 1)}
