"""Provider-neutral LLM interface.

Internal message format (OpenAI-like):
  {"role": "system"|"user", "content": str}
  {"role": "assistant", "content": str|None, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class LLM(Protocol):
    name: str

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       json_mode: bool = False) -> LLMResponse: ...


def tool_schema(spec) -> dict:
    """ToolSpec -> neutral tool definition."""
    return {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model response (tolerates code fences / prose)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
