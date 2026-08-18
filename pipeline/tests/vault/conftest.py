"""Shared fixtures for vault tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_NOTES = Path(__file__).parent / "fixtures" / "notes"


@pytest.fixture
def fixture_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point PULSE_VAULT_PATH at a copy of the fixture notes. Returns the
    notes directory (vault_root/02 Notes equivalent — flat for testing)."""
    # We point PULSE_VAULT_SUBPATH at the bare fixture dir so the layout is
    # vault_root / notes / *.md instead of vault_root / "02 Notes" / *.md.
    monkeypatch.setenv("PULSE_VAULT_PATH", str(FIXTURE_NOTES.parent))
    monkeypatch.setenv("PULSE_VAULT_SUBPATH", "notes")
    # Reset the module-level caches so each test starts clean.
    from app.vault import index as vault_index
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = False
    return FIXTURE_NOTES
