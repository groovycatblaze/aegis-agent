import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_work = Path(tempfile.mkdtemp(prefix="aegis-tests-"))
os.environ["AEGIS_DATA_DIR"] = str(_work)
os.environ["AEGIS_ENTERPRISE_DB"] = str(_work / "enterprise.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_work / 'app.db'}"
os.environ["AEGIS_LLM_PROVIDER"] = "simulated"
os.environ["AEGIS_MCP_TRANSPORT"] = "memory"

from core.config import reset_settings  # noqa: E402

reset_settings()


@pytest.fixture(autouse=True)
def fresh_enterprise():
    from core import enterprise
    enterprise.seed()
    yield
