r"""
EXPLORATORY: what would this have paid, in dollars?

Section 8 of PRE_REGISTRATION.md lists a returns simulation as EXPLORATORY and
says plainly: "Not claimed by any result here: that this is tradeable. A hit rate
says nothing about magnitude." This script does not change that. It exists
because a dollar figure is easier to hold in your head than a percentage.

Read the confidence interval, not the total. The primary test found no
detectable signal, so whatever number comes out is a draw from noise. The CI
shows you how wide that noise is.

Trading rule simulated (fixed, not optimised):
  - forecast says up   -> long at the 09:30 open, flat at the 16:00 close
  - forecast says down -> short at the 09:30 open, flat at the 16:00 close
  - no_call            -> no position
  - one contract, every signal, no sizing, no stops, no discretion

Instrument mapping and why:
  NQ  -> ^NDX point change x $20/pt. The NQ contract is $20/pt, and cash-index
         open-to-close change tracks the futures' RTH change closely. Using the
         cash index is required by Amendment 2: a NQ=F daily bar opens at the
         Globex session start, not 09:30 ET.
  CL  -> skipped for the dollar view. Only ~5 usable calls, and USO is an ETF
         whose dollar value does not map cleanly to a crude contract.
  TNX -> skipped. It is a yield index, not a tradeable instrument; "long TNX"
         means short bonds, which needs a bond instrument and a duration
         assumption this study never fixed.

So the money view is NQ only, which is also the instrument the user actually
trades and has intuition for.

Usage: python pnl_exploratory.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
SEED = 42
N_BOOT = 10_000
DOLLARS_PER_POINT = 20.0     # one NQ contract
COST_POINTS = 0.75           # round-trip slippage + fees, his own estimate


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    calls = conn.execute(
        """SELECT date_et, label FROM labels
           WHERE instrument='NQ' AND label IN ('up','down')
           ORDER BY date_et"""
    ).fetchall()
    conn.close()
    if not calls:
        raise SystemExit("no NQ directional calls found")

    lo, hi = calls[0]["date_et"], calls[-1]["date_et"]
    px = yf.download(
        "^NDX",
        start=(pd.Timestamp(lo) - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
        end=(pd.Timestamp(hi) + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=False, progress=False, threads=False,
    )
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    px = px[["Open", "Close"]].dropna()

    rows: list[tuple[str, str, float, float]] = []   # date, side, gross$, net$
    skipped = 0
    for c in calls:
        ts = pd.Timestamp(c["date_et"])
        if ts not in px.index:
            skipped += 1
            continue
        move = float(px.loc[ts, "Close"]) - float(px.loc[ts, "Open"])
        pts = move if c["label"] == "up" else -move
        gross = pts * DOLLARS_PER_POINT
        net = gross - COST_POINTS * DOLLARS_PER_POINT
        rows.append((c["date_et"], c["label"], gross, net))

    n = len(rows)
    gross = np.array([r[2] for r in rows])
    net = np.array([r[3] for r in rows])
    wins = int((net > 0).sum())

    print("=" * 64)
    print("EXPLORATORY DOLLAR VIEW — 1 NQ contract, open to close")
    print("=" * 64)
    print(f"  window                {lo} .. {hi}")
    print(f"  signals traded        {n}   (skipped {skipped}, no session)")
    print(f"  long / short          {sum(r[1]=='up' for r in rows)}"
          f" / {sum(r[1]=='down' for r in rows)}")
    print(f"  win rate (net)        {wins}/{n} = {wins/n*100:.0f}%")
    print()
    print(f"  GROSS total           ${gross.sum():>12,.0f}")
    print(f"  costs ({COST_POINTS}pt x {n})     ${-COST_POINTS*DOLLARS_PER_POINT*n:>12,.0f}")
    print(f"  NET total             ${net.sum():>12,.0f}")
    print(f"  net per trade         ${net.mean():>12,.0f}")
    print(f"  std dev per trade     ${net.std(ddof=1):>12,.0f}")

    # bootstrap the total, resampling days (same convention as score.py)
    rng = np.random.default_rng(SEED)
    idx = np.arange(n)
    totals = np.array([net[rng.choice(idx, size=n, replace=True)].sum()
                       for _ in range(N_BOOT)])
    p_lo, p_hi = np.percentile(totals, [2.5, 97.5])

    print()
    print(f"  95% CI on NET total   ${p_lo:,.0f}  to  ${p_hi:,.0f}")
    straddles = p_lo < 0 < p_hi
    print(f"  CI straddles zero?    {'YES' if straddles else 'no'}")

    best = max(rows, key=lambda r: r[3])
    worst = min(rows, key=lambda r: r[3])
    print(f"\n  best day              {best[0]}  {best[1]:<5} ${best[3]:>+9,.0f}")
    print(f"  worst day             {worst[0]}  {worst[1]:<5} ${worst[3]:>+9,.0f}")
    top3 = sorted(rows, key=lambda r: abs(r[3]), reverse=True)[:3]
    share = sum(abs(r[3]) for r in top3) / sum(abs(r[3]) for r in rows)
    print(f"  top 3 days = {share*100:.0f}% of all movement (by absolute size)")

    print("\n" + "=" * 64)
    print("HOW TO READ THIS")
    print("=" * 64)
    print(f"""  The total is ${net.sum():,.0f}. The 95% interval runs from
  ${p_lo:,.0f} to ${p_hi:,.0f}. That interval is roughly
  ${(p_hi - p_lo):,.0f} wide, which is {abs((p_hi-p_lo)/net.sum()):.0f}x the total itself.

  So the honest statement is not "this made or lost money." It is: over {n}
  trades this is indistinguishable from flipping a coin and paying costs. The
  primary test already said as much; this is the same finding in dollars.

  Do not size up, size down, or change the rule off this number. It is the
  EXPLORATORY view, and section 8 says explicitly that tradeability is not
  claimed by anything in this study.""")


if __name__ == "__main__":
    main()
