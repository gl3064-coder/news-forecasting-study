"""Vault git operations. Wraps `git clone` / `git pull` with graceful
degradation — failures return a sentinel result, never raise."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class VaultSyncResult:
    success: bool
    sha: str
    pulled_at: datetime
    error: str | None


_last_result: VaultSyncResult | None = None
_lock = threading.Lock()


def vault_root() -> Path:
    return Path(os.getenv("PULSE_VAULT_PATH", "state/vault")).resolve()


def vault_repo_url() -> str:
    return os.getenv("PULSE_VAULT_REPO_URL", "").strip()


def enabled() -> bool:
    return bool(vault_repo_url())


def last_result() -> VaultSyncResult | None:
    return _last_result


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run git with a 60s timeout. Returns the CompletedProcess so callers
    can inspect stdout/stderr/returncode."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _record_result(success: bool, sha: str = "", error: str | None = None) -> VaultSyncResult:
    global _last_result
    result = VaultSyncResult(
        success=success,
        sha=sha,
        pulled_at=datetime.now(timezone.utc),
        error=error,
    )
    _last_result = result
    return result


def pull() -> VaultSyncResult:
    """Clone the repo if missing, otherwise pull. Returns a result struct.
    Never raises — exceptions become VaultSyncResult(success=False, ...).

    Concurrent callers are serialized via a module-level lock so we never
    end up with two git operations interleaved on the same directory.
    """
    if not enabled():
        return _record_result(False, error="vault disabled (PULSE_VAULT_REPO_URL unset)")

    with _lock:
        root = vault_root()
        url = vault_repo_url()
        try:
            if not (root / ".git").is_dir():
                # Clone fresh. Parent must exist.
                root.parent.mkdir(parents=True, exist_ok=True)
                proc = _run_git(["clone", "--depth", "1", url, str(root)])
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or "git clone failed").strip()
                    print(f"[vault] clone failed: {err}", flush=True)
                    return _record_result(False, error=err)
                sha = _head_sha(root)
                print(f"[vault] cloned {url} → {root} @ {sha[:7]}", flush=True)
                return _record_result(True, sha=sha)
            # Existing checkout — fast-forward only so we can't get into a
            # merge-conflict state on a read-only mirror.
            proc = _run_git(["pull", "--ff-only"], cwd=root)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "git pull failed").strip()
                print(f"[vault] pull failed: {err}", flush=True)
                return _record_result(False, error=err)
            sha = _head_sha(root)
            print(f"[vault] pulled @ {sha[:7]}", flush=True)
            return _record_result(True, sha=sha)
        except subprocess.TimeoutExpired as exc:
            print(f"[vault] git timeout: {exc}", flush=True)
            return _record_result(False, error="git operation timed out")
        except Exception as exc:
            print(f"[vault] unexpected error: {exc}", flush=True)
            return _record_result(False, error=str(exc))


def _head_sha(root: Path) -> str:
    try:
        proc = _run_git(["rev-parse", "HEAD"], cwd=root)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""
