"""Central configuration. Everything is overridable through environment variables
(see .env.example). Defaults run fully offline with no API keys."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("AEGIS_DATA_DIR", str(ROOT / "data"))))
    database_url: str = ""
    enterprise_db: Path = field(default_factory=Path)

    # LLM: "simulated" (offline, deterministic), "openai" (any OpenAI-compatible
    # endpoint: OpenAI, Azure OpenAI, Groq, Together, Ollama, vLLM) or "anthropic".
    llm_provider: str = field(default_factory=lambda: _env("AEGIS_LLM_PROVIDER", "simulated"))
    llm_model: str = field(default_factory=lambda: _env("AEGIS_LLM_MODEL", ""))
    llm_temperature: float = field(default_factory=lambda: float(_env("AEGIS_LLM_TEMPERATURE", "0")))

    # MCP transport: "stdio" launches each MCP server as a subprocess (the real
    # deployment shape); "memory" connects over in-memory streams (fast tests).
    mcp_transport: str = field(default_factory=lambda: _env("AEGIS_MCP_TRANSPORT", "stdio"))

    embedder: str = field(default_factory=lambda: _env("AEGIS_EMBEDDER", "hashing"))
    retrieval_top_k: int = field(default_factory=lambda: int(_env("AEGIS_TOP_K", "4")))

    company_domain: str = field(default_factory=lambda: _env("AEGIS_COMPANY_DOMAIN", "veridian.example"))
    currency: str = "INR"

    # Agent budgets
    max_steps: int = field(default_factory=lambda: int(_env("AEGIS_MAX_STEPS", "12")))
    max_tool_calls: int = field(default_factory=lambda: int(_env("AEGIS_MAX_TOOL_CALLS", "16")))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = _env("DATABASE_URL", f"sqlite:///{self.data_dir / 'aegis.db'}")
        if not str(self.enterprise_db) or str(self.enterprise_db) == ".":
            self.enterprise_db = Path(_env("AEGIS_ENTERPRISE_DB", str(self.data_dir / "enterprise.db")))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
