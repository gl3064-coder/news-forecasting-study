r"""
EXPLORATORY. Sharpe ratio for stream A, retrospective, pre-open.

This is section 9 material, not a test. PRE_REGISTRATION_V2.md §7 names exactly
one primary statistic and this is not it. Nothing here may be used to change a
rule, size a position, or revise a verdict. It exists to answer "what does the
dollar series look like", with the error bar attached so the answer cannot be
read as more than it is.

Three reasons the number printed here is inflated:

  1. RETROSPECTIVE. The sample is spent — it has been looked at repeatedly.
  2. UNTRADEABLE LEGS. ~13% of calls resolve to indices and yields that carry
     no position at retail. They are included in the gross figure and broken
     out separately, because in the earlier run they carried 42% of the return.
  3. FRICTIONLESS unless --cost is passed. Break-even was measured at 0.21%
     per round trip.

The headline is not the Sharpe. It is the width of the interval around it.

Usage:
    python sharpe_exploratory.py
    python sharpe_exploratory.py --cost 0.15
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import markets_v2  # noqa: E402
from score_v2 import fetch  # noqa: E402

DB = HERE / "news_corpus.db"
SEED = 42
N_BOOT = 10_000
SESSIONS_PER_YEAR = 252

# Instruments with no straightforward retail position. Kept in the gross figure
# and reported separately rather than silently dropped.
UNTRADEABLE = {"^NDX", "^GSPC", "^TNX", "^TYX"}


def trade_return(px, date: str, direction: str) -> float | None:
    """Signed same-session open-to-close return, as a fraction."""
    idx = px.index.astype(str)
    pos = idx.get_loc(date) if date in idx else None
    if pos is None:
        return None
    o, c = float(px.iloc[pos]["Open"]), float(px.iloc[pos]["Close"])
    if not o:
        return None
    r = (c - o) / o
    return r if direction == "up" else -r


def sharpe(x: np.ndarray) -> float:
    """Annualised, zero risk-free. NaN when there is nothing to divide by."""
    if len(x) < 2:
        return float("nan")
    sd = x.std(ddof=1)
    return float("nan") if sd == 0 else float(x.mean() / sd * np.sqrt(SESSIONS_PER_YEAR))


def analytic_se(sr: float, n: int) -> float:
    """Lo (2002) standard error of an annualised Sharpe, iid assumption.

    Included because it is the part of this that does not depend on the data:
    at n=132 the error bar is ~1.4 whatever the point estimate turns out to be.
    """
    if n < 2 or sr != sr:
        return float("nan")
    per = sr / np.sqrt(SESSIONS_PER_YEAR)
    return float(np.sqrt((1 + per**2 / 2) / n) * np.sqrt(SESSIONS_PER_YEAR))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.0,
                    help="round-trip cost in percent, e.g. 0.15")
    args = ap.parse_args()
    cost = args.cost / 100.0

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT date_et, market, direction FROM calls_v2
           WHERE stream='A' AND prospective=0 AND input_rule='pre_open'
           ORDER BY date_et"""
    ).fetchall()
    conn.close()
    if not rows:
        sys.exit("no stream-A retrospective calls found")

    resolved = []
    unscoreable = 0
    for r in rows:
        tk = markets_v2.resolve(r["market"])
        if not tk or r["direction"] not in ("up", "down"):
            unscoreable += 1
            continue
        resolved.append((r["date_et"], tk, r["direction"]))

    dates = [d for d, _, _ in resolved]
    prices = fetch({tk for _, tk, _ in resolved}, min(dates), max(dates))

    rets, flags, days = [], [], []
    dropped = 0
    for date, tk, direction in resolved:
        px = prices.get(tk)
        if px is None:
            dropped += 1
            continue
        r = trade_return(px, date, direction)
        if r is None:
            dropped += 1
            continue
        rets.append(r - cost)
        flags.append(tk in UNTRADEABLE)
        days.append(date)

    x = np.asarray(rets)
    untr = np.asarray(flags)
    n = len(x)
    if n < 2:
        sys.exit("not enough scored trades")

    sr = sharpe(x)
    se = analytic_se(sr, n)

    # Bootstrap resamples DAYS, matching §7. Same seed, same idiom.
    by_day: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(days, x):
        by_day[d].append(v)
    uniq = sorted(by_day)
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(N_BOOT):
        pick = rng.choice(len(uniq), size=len(uniq), replace=True)
        s = sharpe(np.asarray([v for i in pick for v in by_day[uniq[i]]]))
        if s == s:
            draws.append(s)
    lo, hi = np.percentile(draws, [2.5, 97.5])

    w = "=" * 66
    print(w)
    print("EXPLORATORY SHARPE — stream A, retrospective, pre-open")
    print(w)
    print(f"  trades scored        {n}   ({len(uniq)} distinct days)")
    print(f"  unresolvable market  {unscoreable}")
    print(f"  no session price     {dropped}")
    print(f"  cost applied         {args.cost:.2f}% per round trip")
    print()
    print(f"  mean return / trade  {x.mean()*100:+.3f}%")
    print(f"  std dev / trade      {x.std(ddof=1)*100:.3f}%")
    print(f"  total (sum)          {x.sum()*100:+.1f}%")
    print()
    print(f"  SHARPE (annualised)  {sr:+.2f}")
    print(f"  95% CI (bootstrap)   [{lo:+.2f}, {hi:+.2f}]")
    print(f"  analytic SE          ±{se:.2f}")
    print(f"  CI straddles zero?   {'YES' if lo < 0 < hi else 'NO'}")

    if untr.any():
        tradeable = x[~untr]
        print()
        print(f"  --- excluding {int(untr.sum())} untradeable legs "
              f"({', '.join(sorted(UNTRADEABLE))}) ---")
        print(f"  trades               {len(tradeable)}")
        print(f"  share of total P&L   "
              f"{(1 - tradeable.sum()/x.sum())*100:.0f}% came from the excluded legs"
              if x.sum() else "  share of total P&L   n/a")
        print(f"  SHARPE (annualised)  {sharpe(tradeable):+.2f}")

    print()
    print(w)
    print("HOW TO READ THIS")
    print(w)
    print(f"  The interval is ~{hi-lo:.1f} Sharpe wide on {len(uniq)} days. At this")
    print("  sample size the error bar is roughly ±1.4 no matter what the point")
    print("  estimate is, so a Sharpe below about 2.8 cannot be distinguished")
    print("  from zero. That is a property of n, not of the strategy.")
    print()
    print("  This is EXPLORATORY (§9). It is not the primary test, it does not")
    print("  revise the verdict, and it is not a licence to size anything.")


if __name__ == "__main__":
    main()
