r"""
Score the v2 FORWARD record and report its health. Imported by
build_scorecard.py; runs on the droplet.

This closes the loop. daily_call_v2.py writes prospective calls into
calls_v2.db; without this the record would accumulate correctly and never
appear anywhere. Retrospective figures keep travelling as v2_result.json —
they are fixed — while the forward block is recomputed on every build.

Two things it deliberately will not do:

  * score a sample too small to mean anything. Below MIN_SCORED it reports
    counts and progress only. A skill figure on nine calls is not a number,
    it is an invitation to misread one.
  * silently keep quiet when the job stops. `status()` returns staleness in
    weekdays so the page can say so, and build_scorecard writes it to
    status.json for anything watching from outside.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from markets_v2 import NOT_TRADEABLE, resolve

HERE = Path(__file__).resolve().parent
CALLS_DB = HERE / "calls_v2.db"
ET = ZoneInfo("America/New_York")

MIN_SCORED = 20          # below this, counts only — no skill figure
STALE_WEEKDAYS = 3       # missing this many weekdays in a row is a fault
SEED = 42
N_BOOT = 10_000


def weekdays_between(a: date, b: date) -> int:
    return sum(1 for i in range((b - a).days)
               if (a + timedelta(days=i + 1)).weekday() < 5)


# --------------------------------------------------------------- statistic
def corrected_hit(direction: str, actual: str, up_rate: float) -> float:
    hit = 1.0 if direction == actual else 0.0
    return hit - (up_rate if direction == "up" else 1.0 - up_rate)


def statistic(rows) -> float:
    if not rows:
        return float("nan")
    return sum(corrected_hit(d, a, u) for _, _, d, a, u in rows) / len(rows)


def _prices(tickers: set[str], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    out = {}
    for tk in sorted(tickers):
        try:
            df = yf.download(
                tk, start=(pd.Timestamp(lo) - pd.Timedelta(days=8)).strftime("%Y-%m-%d"),
                end=(pd.Timestamp(hi) + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
                interval="1d", auto_adjust=False, progress=False, threads=False)
        except Exception:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "Close"]].dropna()
        if not df.empty:
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            out[tk] = df
    return out


def _outcome(px: pd.DataFrame, d: str) -> str | None:
    ts = pd.Timestamp(d)
    if ts not in px.index:
        return None
    diff = float(px.loc[ts, "Close"]) - float(px.loc[ts, "Open"])
    return None if diff == 0 else ("up" if diff > 0 else "down")


def _up_rate(px: pd.DataFrame, lo: str, hi: str) -> float:
    w = px.loc[(px.index >= pd.Timestamp(lo)) & (px.index <= pd.Timestamp(hi))]
    return float("nan") if w.empty else float((w["Close"] > w["Open"]).mean())


# ------------------------------------------------------------------ public
def status() -> dict:
    """Health of the forward job, independent of whether it has enough data
    to score. Returns even when the database does not exist yet."""
    now = datetime.now(ET)
    if not CALLS_DB.exists():
        return {"exists": False, "days": 0, "last_date": None,
                "stale_weekdays": None, "healthy": None,
                "checked_at": now.isoformat(timespec="seconds")}

    conn = sqlite3.connect(f"file:{CALLS_DB}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT COUNT(DISTINCT date_et), MAX(date_et), COUNT(*) FROM calls_v2"
    ).fetchone()
    conn.close()
    days, last, calls = row[0] or 0, row[1], row[2] or 0

    stale = None
    if last:
        stale = weekdays_between(date.fromisoformat(last), now.date())
    return {
        "exists": True, "days": days, "calls": calls, "last_date": last,
        "stale_weekdays": stale,
        # Unhealthy once a full STALE_WEEKDAYS have passed with nothing written.
        # None until the first call lands, because "never run" and "stopped
        # running" are different conditions and should not look alike.
        "healthy": None if not last else stale < STALE_WEEKDAYS,
        "checked_at": now.isoformat(timespec="seconds"),
    }


def compute() -> dict | None:
    """Score the forward record. None when there is nothing recorded yet."""
    st = status()
    if not st["exists"] or not st["days"]:
        return {"status": st, "scored": 0, "streams": {}} if st["exists"] else None

    conn = sqlite3.connect(f"file:{CALLS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    calls = conn.execute(
        """SELECT date_et, stream, market, direction, conviction
           FROM calls_v2 WHERE prospective=1 ORDER BY date_et"""
    ).fetchall()
    conn.close()
    if not calls:
        return {"status": st, "scored": 0, "streams": {}}

    live = [c for c in calls if c["direction"] != "none"]
    tickers = {t for t in (resolve(c["market"], forward=True) for c in live) if t}
    days = sorted({c["date_et"] for c in calls})
    px = _prices(tickers, days[0], days[-1]) if tickers else {}

    streams: dict[str, dict] = {}
    for s in sorted({c["stream"] for c in calls}):
        mine = [c for c in calls if c["stream"] == s]
        mine_live = [c for c in mine if c["direction"] != "none"]
        rows, unscoreable = [], 0
        for c in mine_live:
            tk = resolve(c["market"], forward=True)
            if tk is None or tk not in px:
                unscoreable += 1
                continue
            act = _outcome(px[tk], c["date_et"])
            if act is None:
                continue
            rows.append((c["date_et"], tk, c["direction"], act,
                         _up_rate(px[tk], days[0], days[-1])))

        blk = {
            "calls": len(mine), "passed": len(mine) - len(mine_live),
            "unscoreable": unscoreable, "scored": len(rows),
            "hits": sum(d == a for _, _, d, a, _ in rows),
            "untradeable": sum(1 for r in rows if r[1] in NOT_TRADEABLE),
        }
        if len(rows) >= MIN_SCORED:
            by_day = defaultdict(list)
            for r in rows:
                by_day[r[0]].append(r)
            dd = sorted(by_day)
            rng = np.random.default_rng(SEED)
            idx = np.arange(len(dd))
            vals = []
            for _ in range(N_BOOT):
                draw = rng.choice(idx, size=len(idx), replace=True)
                v = statistic([r for i in draw for r in by_day[dd[i]]])
                if v == v:
                    vals.append(v)
            lo, hi = np.percentile(vals, [2.5, 97.5])
            blk |= {"skill": statistic(rows), "lo": float(lo), "hi": float(hi)}
        streams[s] = blk

    return {"status": st, "days": len(days), "window": [days[0], days[-1]],
            "min_scored": MIN_SCORED,
            "scored": max((b["scored"] for b in streams.values()), default=0),
            "streams": streams}
