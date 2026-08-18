r"""
Stage 2 of v2: pull prices and score the frozen rule.

This is the unblinding step. It applies PRE_REGISTRATION_V2.md exactly as
frozen. There is one primary number (stream A, same-session, direction-aware
baseline, day-clustered bootstrap) and everything else is labelled secondary or
exploratory.

Usage: python score_v2.py
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from markets_v2 import resolve, resolve_exact

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
SEED = 42
N_BOOT = 10_000
PRIMARY_STREAM = "A"

HORIZON_SESSIONS = {"today": 1, "week": 5, "month": 21}


# ------------------------------------------------------------------ statistic
def corrected_hit(direction: str, actual: str, up_rate: float) -> float:
    """hit - E[hit | no skill], where the expectation is direction-aware:
    an `up` call is scored against the market's up-rate, a `down` call against
    its down-rate. This neutralises both the market's drift and the
    forecaster's tilt, per section 7."""
    hit = 1.0 if direction == actual else 0.0
    expected = up_rate if direction == "up" else 1.0 - up_rate
    return hit - expected


def statistic(rows: list[tuple[str, str, str, str, float]]) -> float:
    """Mean corrected hit. rows = (day, ticker, direction, actual, up_rate)."""
    if not rows:
        return float("nan")
    return sum(corrected_hit(d, a, u) for _, _, d, a, u in rows) / len(rows)


def bootstrap(days: list[str], by_day: dict, fn, seed: int = SEED):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(days))
    vals, skipped = [], 0
    for _ in range(N_BOOT):
        draw = rng.choice(idx, size=len(idx), replace=True)
        rows = [r for i in draw for r in by_day[days[i]]]
        v = fn(rows)
        if v != v:
            skipped += 1
            continue
        vals.append(v)
    return np.array(vals), skipped


def verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "CI excludes zero, positive -> evidence of directional information"
    if hi < 0:
        return "CI excludes zero, negative -> ANTI-predictive"
    return "CI includes zero -> no detectable directional information at this sample size"


# ------------------------------------------------------------------ prices
def fetch(tickers: set[str], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for tk in sorted(tickers):
        df = yf.download(
            tk,
            start=(pd.Timestamp(lo) - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            end=(pd.Timestamp(hi) + pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=False, progress=False, threads=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            print(f"  {tk:<10} NO DATA")
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out[tk] = df[["Open", "Close"]].dropna()
        print(f"  {tk:<10} {len(out[tk]):>4} sessions")
    return out


def outcome(px: pd.DataFrame, date: str, sessions: int = 1) -> str | None:
    """Sign of the move from the open on `date` to the close `sessions`
    trading days later. sessions=1 is the same-session open-to-close horizon
    that section 7 fixes as primary."""
    ts = pd.Timestamp(date)
    if ts not in px.index:
        return None
    pos = px.index.get_loc(ts)
    exit_pos = pos + sessions - 1
    if exit_pos >= len(px.index):
        return None            # horizon has not resolved yet
    diff = float(px.iloc[exit_pos]["Close"]) - float(px.iloc[pos]["Open"])
    if diff == 0:
        return None
    return "up" if diff > 0 else "down"


def up_rate(px: pd.DataFrame, days: list[str]) -> float:
    """Fraction of sessions in the window where the market closed above its
    open. Counted over every session in the window, whether or not the market
    was called on it -- the base rate describes the market, not the sample."""
    window = px.loc[(px.index >= pd.Timestamp(min(days)))
                    & (px.index <= pd.Timestamp(max(days)))]
    if window.empty:
        return float("nan")
    return float((window["Close"] > window["Open"]).mean())


# ------------------------------------------------------------------ report
def score_stream(conn, stream: str, prices, all_days, label: str,
                 prospective: int = 0, resolver=resolve,
                 input_rule: str = 'pre_open') -> None:
    """Section 8 requires retrospective and prospective calls to be reported
    separately, so the flag is a filter, never a pooled default."""
    calls = conn.execute(
        """SELECT date_et, market, direction, conviction, horizon
           FROM calls_v2 WHERE stream=? AND prospective=? AND input_rule=?
           ORDER BY date_et""",
        (stream, prospective, input_rule),
    ).fetchall()
    if not calls:
        return
    passed = [c for c in calls if c["direction"] == "none"]
    live = [c for c in calls if c["direction"] != "none"]

    recs, unscoreable, no_session = [], [], 0
    for c in live:
        tk = resolver(c["market"], forward=bool(prospective))
        if tk is None or prices.get(tk) is None:
            unscoreable.append(c["market"])
            continue
        px = prices[tk]
        act = outcome(px, c["date_et"], 1)
        if act is None:
            no_session += 1
            continue
        recs.append({
            "day": c["date_et"], "ticker": tk, "direction": c["direction"],
            "actual": act, "up_rate": up_rate(px, all_days),
            "conviction": c["conviction"], "horizon": c["horizon"],
        })

    def tup(r: dict) -> tuple[str, str, str, str, float]:
        return (r["day"], r["ticker"], r["direction"], r["actual"], r["up_rate"])

    rows = [tup(r) for r in recs]

    print("\n" + "=" * 66)
    print(f"{label}  (stream {stream}, same session 09:30-16:00)")
    print("=" * 66)
    print(f"  calls emitted            {len(calls)}")
    print(f"  passed                   {len(passed)}")
    print(f"  unscoreable (not in the frozen table)  {len(unscoreable)}"
          f"  = {len(unscoreable)/max(len(live),1)*100:.0f}% of live calls")
    print(f"  dropped (no session)     {no_session}")
    print(f"  scored                   {len(rows)}")
    if not rows:
        print("  nothing to score")
        return

    days = sorted({d for d, _, _, _, _ in rows})
    by_day = defaultdict(list)
    for r in rows:
        by_day[r[0]].append(r)

    point = statistic(rows)
    arr, skipped = bootstrap(days, by_day, statistic)
    lo, hi = np.percentile(arr, [2.5, 97.5])
    hits = sum(d == a for _, _, d, a, _ in rows)

    print(f"\n  raw hit rate             {hits}/{len(rows)}"
          f" = {hits/len(rows)*100:.0f}%")
    print(f"  corrected skill          {point*100:+.1f}pp")
    print(f"  95% CI                   [{lo*100:+.1f}pp, {hi*100:+.1f}pp]"
          f"   ({len(arr)} draws, {skipped} discarded)")
    print(f"  {verdict(lo, hi)}")

    # ---- SECONDARY: each call scored at the horizon it actually stated
    h_rows, h_pending = [], 0
    for r in recs:
        px = prices[r["ticker"]]
        n = HORIZON_SESSIONS[r["horizon"]]
        act = outcome(px, r["day"], n)
        if act is None:
            h_pending += 1
            continue
        h_rows.append((r["day"], r["ticker"], r["direction"], act, r["up_rate"]))
    print(f"\n  SECONDARY (each call at its own stated horizon)")
    print(f"    scored {len(h_rows)}   unresolved {h_pending}")
    if h_rows:
        h_days = sorted({d for d, _, _, _, _ in h_rows})
        h_by_day = defaultdict(list)
        for r in h_rows:
            h_by_day[r[0]].append(r)
        h_pt = statistic(h_rows)
        h_arr, _ = bootstrap(h_days, h_by_day, statistic)
        h_lo, h_hi = np.percentile(h_arr, [2.5, 97.5])
        print(f"    skill {h_pt*100:+.1f}pp   95% CI"
              f" [{h_lo*100:+.1f}pp, {h_hi*100:+.1f}pp]")
        print("    (overlapping multi-day windows make these observations")
        print("     non-independent — which is why they are not the primary)")

    print("\n  EXPLORATORY (not a test)")
    for conv in ("high", "low"):
        sub = [tup(r) for r in recs if r["conviction"] == conv]
        if sub:
            print(f"    conviction {conv:<5} n={len(sub):<4}"
                  f" skill {statistic(sub)*100:+.1f}pp")
    for hz in ("today", "week", "month"):
        n = sum(1 for r in recs if r["horizon"] == hz)
        if n:
            print(f"    stated horizon {hz:<6} n={n}")
    top = defaultdict(int)
    for r in recs:
        top[r["ticker"]] += 1
    print("    most-traded:", ", ".join(
        f"{t}({n})" for t, n in sorted(top.items(), key=lambda kv: -kv[1])[:8]))


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    all_days = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT date_et FROM calls_v2 "
        "WHERE input_rule='pre_open'")})
    markets = {r[0] for r in conn.execute(
        "SELECT DISTINCT market FROM calls_v2 WHERE direction != 'none'")}
    tickers = {t for t in (resolve(m) for m in markets) if t}
    tickers |= {t for t in (resolve(m, forward=True) for m in markets) if t}

    print(f"days:    {len(all_days)}  ({all_days[0]} .. {all_days[-1]})")
    print(f"markets named: {len(markets)}   resolving to {len(tickers)} tickers")
    print("\ndownloading prices")
    prices = fetch(tickers, all_days[0], all_days[-1])

    for flag, group in ((0, "RETROSPECTIVE"), (1, "PROSPECTIVE")):
        score_stream(conn, PRIMARY_STREAM, prices, all_days,
                     f"PRIMARY [{group}]", flag)
        for st in ("B", "C", "D"):
            score_stream(conn, st, prices, all_days,
                         f"SECONDARY {st} [{group}]", flag)

    # Amendment 1 robustness check: the same primary under the ORIGINAL
    # exact-match rule, so the amendment's effect on the answer is visible.
    score_stream(conn, PRIMARY_STREAM, prices, all_days,
                 "CHECK: PRIMARY under the pre-Amendment-1 exact-match rule",
                 0, resolve_exact)

    print("\n" + "=" * 66)
    print("Per section 8: this run is RETROSPECTIVE. Leakage biases it upward,")
    print("so a positive result here is suggestive only. Re-run this identical")
    print("script as the forward sample grows. Do not change the rule now that")
    print("the number is visible.")
    conn.close()


if __name__ == "__main__":
    main()
