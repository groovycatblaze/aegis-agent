"""Application database (runs, trace events, approvals). SQLite by default;
set DATABASE_URL=postgresql+psycopg://... for PostgreSQL."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from core.config import get_settings


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(16), index=True)
    request: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32))
    llm: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    plan: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(Text, default="{}")
    final_answer: Mapped[str] = mapped_column(Text, default="")
    verification: Mapped[str] = mapped_column(Text, default="{}")
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class TraceEvent(Base):
    __tablename__ = "trace_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[str] = mapped_column(Text, default="{}")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    requester_id: Mapped[str] = mapped_column(String(16))
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[str] = mapped_column(Text)
    args_hash: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16))
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    approver_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)


@lru_cache(maxsize=8)
def get_engine(url: str | None = None):
    url = url or get_settings().database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
