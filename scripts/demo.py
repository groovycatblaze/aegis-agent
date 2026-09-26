"""Command-line demo: runs a request end to end and prints the trace.

    python -m scripts.demo "My laptop is failing and I need a replacement" --user E1001 [--baseline]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from agents.orchestrator import AgentRuntime, get_run
from core import enterprise as ent
from core.config import get_settings
from core.llm.factory import get_llm
from security.gateway import DefenseConfig
from tools.hub import ToolHub


async def main(args) -> None:
    ent.seed()
    async with ToolHub(get_settings().mcp_transport) as hub:
        defenses = DefenseConfig.none() if args.baseline else DefenseConfig.full()
        rt = AgentRuntime(hub, get_llm(), defenses)
        run = await rt.start(args.user, args.request)
        while run["status"] == "awaiting_approval":
            a = run["pending_approval"]
            print(f"\n*** APPROVAL REQUIRED: {a['tool']} {json.dumps(a['arguments'])}\n    risk {a['risk_level']}: "
                  f"{'; '.join(a['reasons'])}")
            approver = a["approver_id"] or input(f"approver id (role {a['approver_role']}): ").strip()
            ok = input(f"Approve as {approver}? [y/N] ").strip().lower() == "y"
            run = await rt.decide(a["id"], approver, ok)
        for e in get_run(run["id"])["trace"]:
            print(f"{e['seq']:>3} {e['kind']:<18} {e['name']:<24} {json.dumps(e['data'], default=str)[:110]}")
        print("\nFINAL:", run["final_answer"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("request")
    p.add_argument("--user", default="E1001")
    p.add_argument("--baseline", action="store_true")
    asyncio.run(main(p.parse_args()))
