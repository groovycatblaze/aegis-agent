"""Anthropic Messages API adapter. Configure with ANTHROPIC_API_KEY and AEGIS_LLM_MODEL."""
from __future__ import annotations

import os

from core.llm.base import LLMResponse, ToolCall, Usage


class AnthropicLLM:
    def __init__(self, model: str | None = None, temperature: float = 0.0, client=None, max_tokens: int = 2048):
        from anthropic import AsyncAnthropic
        self.model = model or os.environ.get("AEGIS_LLM_MODEL") or "claude-sonnet-5"
        self.name = f"anthropic:{self.model}"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = client or AsyncAnthropic()

    @staticmethod
    def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        out: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:
                out.append({"role": "user", "content": m.get("content") or ""})
        return system, out

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       json_mode: bool = False) -> LLMResponse:
        system, msgs = self.to_anthropic(messages)
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        kwargs: dict = {"model": self.model, "system": system, "messages": msgs,
                        "max_tokens": self.max_tokens, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                "input_schema": t["input_schema"]} for t in tools]
        resp = await self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text") or None
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return LLMResponse(text, calls, usage, self.model)
