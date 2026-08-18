"""Negative-path tests: vault failures must not break the LLM chain."""

from __future__ import annotations

import json
import subprocess

import pytest

from app.vault import index as vault_index, sync as vault_sync


def test_pull_failure_leaves_chain_runnable(monkeypatch, db_initialized):
    """When git pull raises, the chain continues with whatever was already
    in the index (empty in this test). LLM calls must still run."""
    monkeypatch.setenv("PULSE_VAULT_REPO_URL", "git@example.com:no/such.git")
    monkeypatch.setenv("PULSE_VAULT_PATH", "/nonexistent/vault")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("network down")))

    vault_index._titles.clear()
    vault_index._full.clear()

    # Vault pull should return failure without raising
    pull_res = vault_sync.pull()
    assert pull_res.success is False
    assert pull_res.error is not None

    # LLM call should still work and have no vault block in the system prompt
    captured: list[str] = []
    def fake_call(system, user, model, max_tokens=0, timeout=0, **kwargs):
        captured.append(system)
        return json.dumps({
            "summary": "x", "main_points": ["a"], "why_it_matters": "x",
            "market_impact": "x", "framing": "x",
        })
    monkeypatch.setattr("app.services.summaries._call_anthropic", fake_call)
    from app.services.summaries import maybe_model_story_summary_anthropic
    llm_res = maybe_model_story_summary_anthropic({
        "title": "x", "fullContent": "x", "source": "x", "category": "x",
        "emailId": "x", "isNewsletter": True,
    })
    assert llm_res is not None
    assert "USER'S WATCHLIST" not in captured[-1]


def test_rebuild_on_missing_dir_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_VAULT_PATH", str(tmp_path / "nothing"))
    monkeypatch.setenv("PULSE_VAULT_SUBPATH", "no_subdir")
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = False
    vault_index.rebuild()  # must not raise
    assert vault_index.titles() == []
