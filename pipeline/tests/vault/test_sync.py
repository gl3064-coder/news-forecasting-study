"""Tests for app.vault.sync — git clone/pull with graceful failure."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.vault import sync as vault_sync


@pytest.fixture
def isolated_vault(monkeypatch, tmp_path):
    """Point vault at a tmp_path that doesn't exist yet."""
    monkeypatch.setenv("PULSE_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("PULSE_VAULT_REPO_URL", "git@github.com:example/repo.git")
    vault_sync._last_result = None
    return tmp_path / "vault"


def test_enabled_false_when_repo_url_unset(monkeypatch):
    monkeypatch.delenv("PULSE_VAULT_REPO_URL", raising=False)
    assert vault_sync.enabled() is False


def test_enabled_true_when_repo_url_set(isolated_vault):
    assert vault_sync.enabled() is True


def test_pull_returns_disabled_result_when_url_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("PULSE_VAULT_REPO_URL", raising=False)
    result = vault_sync.pull()
    assert result.success is False
    assert "disabled" in (result.error or "").lower()


def test_pull_clones_when_directory_missing(isolated_vault, monkeypatch):
    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # Simulate successful clone: create the dir + a fake .git dir
        if "clone" in cmd:
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vault_sync.pull()
    assert result.success is True
    # First call was the clone
    assert calls[0][0] == "git"
    assert "clone" in calls[0]


def test_pull_uses_pull_when_directory_exists(isolated_vault, monkeypatch):
    isolated_vault.mkdir(parents=True)
    (isolated_vault / ".git").mkdir()
    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="up to date\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vault_sync.pull()
    assert result.success is True
    # No clone — first command should be a pull or fetch
    assert "clone" not in calls[0]


def test_pull_returns_failure_on_git_error(isolated_vault, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode=128, stdout="", stderr="fatal: repository not found"
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vault_sync.pull()
    assert result.success is False
    assert result.error is not None


def test_pull_does_not_raise_on_exception(isolated_vault, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("network unreachable")
    monkeypatch.setattr(subprocess, "run", fake_run)
    # Must not raise
    result = vault_sync.pull()
    assert result.success is False


def test_pull_concurrent_calls_are_serialized(isolated_vault, monkeypatch):
    """Two concurrent pulls should not interleave git commands."""
    import threading
    calls: list[str] = []
    def fake_run(cmd, **kwargs):
        calls.append("start")
        # Make the dir if cloning so subsequent calls see it
        if "clone" in cmd:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            (Path(cmd[-1]) / ".git").mkdir(exist_ok=True)
        calls.append("end")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    t1 = threading.Thread(target=vault_sync.pull)
    t2 = threading.Thread(target=vault_sync.pull)
    t1.start(); t2.start()
    t1.join(); t2.join()
    # If serialized, calls alternates start,end,start,end (no interleaved start,start)
    # Each pull may run multiple git commands (clone+rev-parse, or pull+rev-parse).
    # The invariant is: every "start" is immediately followed by its matching "end".
    for i in range(0, len(calls), 2):
        assert calls[i] == "start"
        assert calls[i + 1] == "end"


def test_last_result_persists_after_pull(isolated_vault, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="abc\n", stderr=""))
    # First, simulate dir existing
    isolated_vault.mkdir(parents=True)
    (isolated_vault / ".git").mkdir()
    vault_sync.pull()
    last = vault_sync.last_result()
    assert last is not None
    assert isinstance(last.pulled_at, datetime)
