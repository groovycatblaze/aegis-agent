"""OpenAI-compatible chat-completions adapter (OpenAI, Azure OpenAI, Groq,
Together, Ollama, vLLM, LM Studio ... anything exposing /v1/chat/completions
with tool calling). Configure with OPENAI_API_KEY, OPENAI_BASE_URL and AEGIS_LLM_MODEL."""
from __future__ import annotations

import json
import os

from core.llm.base import LLMResponse, ToolCall, Usage


class OpenAILLM:
    def __init__(self, model: str | None = None, temperature: float = 0.0, client=None):
        from openai import AsyncOpenAI
        self.model = model or os.environ.get("AEGIS_LLM_MODEL") or "gpt-4o-mini"
        self.name = f"openai:{self.model}"
        self.temperature = temperature
        self.client = client or AsyncOpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)

    @staticmethod
    def to_openai_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content"),
                            "tool_calls": [{"id": tc["id"], "type": "function",
                                            "function": {"name": tc["name"],
                                                         "arguments": json.dumps(tc["arguments"])}}
                                           for tc in m["tool_calls"]]})
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       json_mode: bool = False) -> LLMResponse:
        kwargs: dict = {"model": self.model, "messages": self.to_openai_messages(messages),
                        "temperature": self.temperature}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                  "parameters": t["input_schema"]}} for t in tools]
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = Usage(getattr(resp.usage, "prompt_tokens", 0) or 0, getattr(resp.usage, "completion_tokens", 0) or 0)
        return LLMResponse(msg.content, calls, usage, self.model)
