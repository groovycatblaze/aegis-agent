"""Run tracing: every decision the agent and the platform make is recorded as an
ordered event (plan, retrieval, LLM call, tool request, gateway decision, tool
result, approval, verification, final answer)."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

from core.db import TraceEvent, session_scope


def _safe(data: Any) -> str:
    return json.dumps(data, default=str)[:20000]


class Tracer:
    def __init__(self, run_id: str, db_url: str | None = None, start_seq: int = 0):
        self.run_id = run_id
        self.db_url = db_url
        self.seq = start_seq
        self.events: list[dict] = []

    def event(self, kind: str, name: str = "", data: Any = None, duration_ms: float = 0.0) -> dict:
        self.seq += 1
        ev = {"run_id": self.run_id, "seq": self.seq, "ts": time.time(), "kind": kind, "name": name,
              "data": data or {}, "duration_ms": round(duration_ms, 2)}
        self.events.append(ev)
        with session_scope(self.db_url) as s:
            s.add(TraceEvent(run_id=self.run_id, seq=self.seq, ts=ev["ts"], kind=kind, name=name,
                             data=_safe(ev["data"]), duration_ms=ev["duration_ms"]))
        return ev

    @contextmanager
    def span(self, kind: str, name: str = "") -> Iterator[dict]:
        holder: dict = {}
        start = time.perf_counter()
        try:
            yield holder
        finally:
            self.event(kind, name, holder, (time.perf_counter() - start) * 1000)


def load_trace(run_id: str, db_url: str | None = None) -> list[dict]:
    with session_scope(db_url) as s:
        rows = s.query(TraceEvent).filter(TraceEvent.run_id == run_id).order_by(TraceEvent.seq).all()
        return [{"seq": r.seq, "ts": r.ts, "kind": r.kind, "name": r.name, "data": json.loads(r.data),
                 "duration_ms": r.duration_ms} for r in rows]
