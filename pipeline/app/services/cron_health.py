"""Sync-failure tracking for the scheduled Gmail chain.

Born from the 2026-07-14 incident: the Gmail token pointed at the wrong
account, sync_newsletters raised 'Gmail label "Pulse" was not found' on every
cron tick, and the bare `except: pass` blocks in _scheduled_sync hid it for
hours. This module gives that failure a persistent, queryable trace: a
consecutive-failure counter in app_state that /health surfaces as a warning.

Storage follows the spotlight pattern — one JSON blob in app_state, and every
DB touch is wrapped so a broken database can never take down the cron itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


_STATE_KEY = "sync_health"

_DEFAULT_STATE: dict[str, Any] = {
    "consecutive_failures": 0,
    "last_error": "",
    "last_failure_at": "",
    "last_success_at": "",
}


def sync_health() -> dict[str, Any]:
    """Current sync health, always with all keys present."""
    state = dict(_DEFAULT_STATE)
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE key = ?", (_STATE_KEY,)
            ).fetchone()
    except Exception as exc:
        print(f"[cron] sync_health db load failed: {exc}", flush=True)
        return state
    if not row:
        return state
    try:
        stored = json.loads(row[0])
    except Exception:
        return state
    if isinstance(stored, dict):
        state.update({k: stored[k] for k in _DEFAULT_STATE if k in stored})
    return state


def record_sync_failure(error: str) -> dict[str, Any]:
    """Increment the failure counter. Returns the new state so callers can
    decide whether to alert (e.g. exactly at a threshold crossing)."""
    state = sync_health()
    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    state["last_error"] = str(error)
    state["last_failure_at"] = datetime.now(timezone.utc).isoformat()
    _save(state)
    return state


def record_sync_success() -> None:
    # last_error/last_failure_at are kept for post-mortems; the counter
    # going back to 0 is what clears the /health warning.
    state = sync_health()
    state["consecutive_failures"] = 0
    state["last_success_at"] = datetime.now(timezone.utc).isoformat()
    _save(state)


def _save(state: dict[str, Any]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO app_state(key, value_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (_STATE_KEY, json.dumps(state), ts),
            )
    except Exception as exc:
        print(f"[cron] sync_health db save failed: {exc}", flush=True)
