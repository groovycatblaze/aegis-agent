"""Tool hub: one MCP client session per MCP server, plus native tools
(search_policy, backed by the RAG retriever), exposed to the agent as a single
catalogue.

transport="stdio"  -> each server runs as its own subprocess (deployment shape)
transport="memory" -> real MCP protocol over in-memory streams (fast tests)
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from core.config import ROOT, get_settings

MCP_SERVERS = {
    "employee": "mcp_servers.employee_server",
    "ticket": "mcp_servers.ticket_server",
    "expense": "mcp_servers.expense_server",
    "notification": "mcp_servers.notification_server",
}


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    server: str

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "server": self.server}


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    retries: int = 0

    def to_message(self) -> dict:
        return {"ok": True, "result": self.data} if self.ok else {"ok": False, "error": self.error}


@dataclass
class NativeTool:
    spec: ToolSpec
    fn: Callable[..., Awaitable[Any]]


class ToolHub:
    def __init__(self, transport: str | None = None, max_retries: int = 2):
        self.transport = transport or get_settings().mcp_transport
        self.max_retries = max_retries
        self.sessions: dict[str, ClientSession] = {}
        self.specs: dict[str, ToolSpec] = {}
        self.native: dict[str, NativeTool] = {}
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "ToolHub":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> None:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for server, module in MCP_SERVERS.items():
            session = await self._connect(server, module)
            self.sessions[server] = session
            listed = await session.list_tools()
            for t in listed.tools:
                self.specs[t.name] = ToolSpec(t.name, t.description or "", t.inputSchema, server)
        self._register_native()

    async def _connect(self, server: str, module: str) -> ClientSession:
        assert self._stack is not None
        if self.transport == "stdio":
            env = {**os.environ, "PYTHONPATH": str(ROOT),
                   "AEGIS_ENTERPRISE_DB": str(get_settings().enterprise_db)}
            params = StdioServerParameters(command=sys.executable, args=["-m", module], env=env, cwd=str(ROOT))
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session
        mod = importlib.import_module(module)
        return await self._stack.enter_async_context(create_connected_server_and_client_session(mod.mcp))

    def _register_native(self) -> None:
        from rag.retriever import get_retriever

        async def search_policy(query: str, k: int = 4) -> dict:
            s = get_settings()
            retriever = get_retriever(s.embedder)
            hits = retriever.search(query, k=int(k) or s.retrieval_top_k)
            return {"query": query, "results": [h.to_dict() for h in hits]}

        spec = ToolSpec(
            "search_policy",
            "Search the company policy knowledge base (IT, HR, expense, travel, security policies). "
            "Returns the most relevant policy passages with document id, title, section and version.",
            {"type": "object", "properties": {"query": {"type": "string"},
                                              "k": {"type": "integer", "default": 4}},
             "required": ["query"]},
            "knowledge")
        self.native["search_policy"] = NativeTool(spec, search_policy)
        self.specs["search_policy"] = spec

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(None, None, None)
            self._stack = None
        self.sessions.clear()

    def catalog(self, names: set[str] | None = None) -> list[ToolSpec]:
        return [s for n, s in sorted(self.specs.items()) if names is None or n in names]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        if name not in self.specs:
            return ToolResult(False, error=f"Unknown tool '{name}'")
        if name in self.native:
            try:
                data = await self.native[name].fn(**arguments)
                return ToolResult(True, data, latency_ms=(time.perf_counter() - start) * 1000)
            except Exception as e:  # noqa: BLE001
                return ToolResult(False, error=str(e), latency_ms=(time.perf_counter() - start) * 1000)

        session = self.sessions[self.specs[name].server]
        attempt = 0
        while True:
            try:
                res = await session.call_tool(name, arguments)
                break
            except Exception as e:  # transport-level failure -> retry with backoff
                if attempt >= self.max_retries:
                    return ToolResult(False, error=f"transport error: {e}", retries=attempt,
                                      latency_ms=(time.perf_counter() - start) * 1000)
                attempt += 1
                await asyncio.sleep(0.05 * 2 ** attempt)
        elapsed = (time.perf_counter() - start) * 1000
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        if res.isError:
            return ToolResult(False, error=text or "tool error", latency_ms=elapsed, retries=attempt)
        data: Any = res.structuredContent
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if data is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
        return ToolResult(True, data, latency_ms=elapsed, retries=attempt)
