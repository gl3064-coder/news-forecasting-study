"""Shared pytest fixtures for Pulse tests.

Each test gets a fresh temp SQLite file and the env vars Pulse needs.
Anthropic calls are stubbed by default so tests never hit the network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

# Make `app` importable regardless of where pytest is invoked from
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Path to a temporary SQLite database file for one test."""
    return str(tmp_path / "pulse_test.db")


@pytest.fixture(autouse=True)
def stub_env(monkeypatch: pytest.MonkeyPatch, tmp_db_path: str) -> None:
    """Default environment for every test. Override per-test as needed.

    Critical: the Pulse service modules call `load_dotenv(override=True)` at
    import time, which would overwrite these env vars from the real .env file
    pointing at production paths. We neutralize load_dotenv FIRST so subsequent
    imports of summaries/gmail/etc. don't clobber our test env.
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: True)
    # Also patch the already-imported references inside Pulse modules if they
    # were imported before this fixture ran (e.g. cross-test caching).
    import importlib
    for mod_path in ("app.services.summaries", "app.services.gmail"):
        try:
            mod = importlib.import_module(mod_path)
            if hasattr(mod, "load_dotenv"):
                monkeypatch.setattr(f"{mod_path}.load_dotenv", lambda *a, **kw: True)
        except Exception:
            pass

    monkeypatch.setenv("PULSE_DB_FILE", tmp_db_path)
    # Keep app.main importable without it firing a real sync run 5s later,
    # which would race any test asserting on cron/sync health state.
    monkeypatch.setenv("PULSE_SKIP_STARTUP_REFRESH", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GMAIL_CREDENTIALS_FILE", str(ROOT / "tests" / "missing.json"))
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(ROOT / "tests" / "missing.json"))


@pytest.fixture
def db_initialized(stub_env: None, tmp_db_path: str) -> Iterator[str]:
    """Yields a db path that already had init_db() run on it.
    Explicitly depends on stub_env so PULSE_DB_FILE is patched before init_db reads it.
    """
    from app.db import init_db
    init_db()
    yield tmp_db_path


@pytest.fixture
def mock_anthropic(monkeypatch: pytest.MonkeyPatch):
    """Stubs _call_anthropic. Tests inject canned JSON responses via .returns(...)."""
    state: dict[str, Any] = {"response": "{}"}

    def fake_call(system: str, user: str, model: str, max_tokens: int = 0, timeout: int = 0, **kwargs) -> str:
        return state["response"]

    monkeypatch.setattr("app.services.summaries._call_anthropic", fake_call)
    # Also patch the local reference imported into dossiers.py.
    # Force-import dossiers so the attribute exists, then patch it.
    import app.services.dossiers as _dossiers_mod
    monkeypatch.setattr(_dossiers_mod, "_call_anthropic", fake_call)

    class Controller:
        def returns(self, text: str) -> None:
            state["response"] = text

    return Controller()
