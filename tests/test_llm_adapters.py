"""Adapter tests with fake SDK clients (no network, no API keys)."""
import asyncio
import json
from types import SimpleNamespace as NS

from core.llm.anthropic_llm import AnthropicLLM
from core.llm.openai_llm import OpenAILLM

HISTORY = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "name": "get_ticket",
                                                           "arguments": {"ticket_id": "IT-1003"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "get_ticket", "content": "{\"ok\": true}"},
]
TOOLS = [{"name": "get_ticket", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


class FakeOpenAI:
    def __init__(self):
        self.kwargs = None
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.kwargs = kw
        tc = NS(id="c2", function=NS(name="get_ticket", arguments=json.dumps({"ticket_id": "IT-1001"})))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))], usage=NS(prompt_tokens=10, completion_tokens=5))


class FakeAnthropic:
    def __init__(self):
        self.kwargs = None
        self.messages = NS(create=self.create)

    async def create(self, **kw):
        self.kwargs = kw
        return NS(content=[NS(type="text", text="ok"), NS(type="tool_use", id="t1", name="get_ticket",
                                                            input={"ticket_id": "IT-1001"})],
                  usage=NS(input_tokens=12, output_tokens=3))


def test_openai_adapter_roundtrip():
    fake = FakeOpenAI()
    llm = OpenAILLM("test-model", client=fake)
    r = asyncio.run(llm.complete(HISTORY, TOOLS))
    msgs = fake.kwargs["messages"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "get_ticket"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"}
    assert fake.kwargs["tools"][0]["type"] == "function"
    assert r.tool_calls[0].arguments == {"ticket_id": "IT-1001"} and r.usage.prompt_tokens == 10


def test_anthropic_adapter_roundtrip():
    fake = FakeAnthropic()
    llm = AnthropicLLM("test-model", client=fake)
    r = asyncio.run(llm.complete(HISTORY, TOOLS))
    assert fake.kwargs["system"] == "sys"
    msgs = fake.kwargs["messages"]
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[2]["content"][0]["type"] == "tool_result" and msgs[2]["content"][0]["tool_use_id"] == "c1"
    assert r.content == "ok" and r.tool_calls[0].name == "get_ticket" and r.usage.completion_tokens == 3
