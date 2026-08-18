r"""Stage 2 of v4: pull prices and score the frozen rule. This is the unblinding step.

Applies PRE_REGISTRATION_V4.md as frozen, including Amendment 1. There is one
primary number (mean net return per trade, 09:30->16:00, 10 bps, day-clustered
bootstrap) and everything else is labelled secondary or exploratory.

Three decisions this file makes that are worth stating out loud:

1. **Raw, not residual, is primary.** Residual return (the name minus SPY) is
   undefined for Brent, gold, or a bond yield, and Amendment 1 restored those
   to the universe. Residual survives as a secondary on the equity subset.
   Because raw return rewards being long in a rising tape for free, the
   market's own return over the identical windows and the up/down split are
   printed next to the primary and must be read with it.

2. **Yields are excluded from the return primary, not merely flagged.** v2
   could score a yield directionally and report it as "not directly tradeable".
   A return study cannot: a 10-year yield moving 4.00 -> 4.10 is not a 2.5%
   return anyone can capture. Anything resolving into markets_v2.NOT_TRADEABLE
   is counted, reported, and left out of every return figure.

3. **The v4 universe applies to the whole v4 sample, including retrospective
   mornings.** v2's Amendments 2 and 3 were forward-only because re-mapping
   instruments after seeing which ones produced the return is indistinguishable
   from picking the mapping that flatters the answer. That constraint protects
   v2's already-scored numbers; it does not bind v4, which froze
   `universe_v4.json` before a single v4 call existed. Stated so the difference
   is a decision on the record rather than an oversight.

Usage: python score_v4.py
"""

from __future__ import annotations

import json
import sqlite3
import warnings
from collections import Counter, defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

from markets_v2 import NOT_TRADEABLE, resolve

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
UNIVERSE = HERE / "universe_v4.json"
SOURCE_CSV = HERE / "universe_v4_source.csv"

SEED = 42
N_BOOT = 10_000
COST = 0.0010                      # 10 bps per round trip, section 7
BENCH = "SPY"
HORIZONS = {"same_session": 1, "two_day": 2, "one_week": 5}
PRIMARY_HORIZON = "same_session"


# ------------------------------------------------------------------ resolution
def load_universe() -> tuple[dict[str, str], set[str]]:
    """Aliases, plus the set of tickers that are actually single equities.

    The equity set is the S&P 500 source list, NOT every ticker in
    universe_v4.json. The universe also carries commodity and index ETFs
    inherited from markets_v2 (GLD, QQQ, BNO, UGA, ...), and a residual-vs-SPY
    figure computed over those is exactly the meaningless number Amendment 1
    moved the primary away from. Caught by the synthetic-data test run before
    any real extraction: every scored trade was landing in the "equity subset".
    """
    u = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    equities = {
        str(s).strip().upper().replace(".", "-")
        for s in pd.read_csv(SOURCE_CSV)["Symbol"]
    } & set(u["aliases"].values())
    return u["aliases"], equities


def resolve_market(name: str, aliases: dict[str, str], forward: bool) -> str | None:
    """Exact alias match against the frozen equity universe, then the frozen
    markets_v2 cascade for oil, indices, metals, rates and FX. Section 5."""
    key = (name or "").strip().lower()
    if key in aliases:
        return aliases[key]
    return resolve(name, forward=forward)


# ------------------------------------------------------------------ prices
def fetch(tickers: set[str], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    px = yf.download(sorted(tickers), start=lo, end=hi, progress=False,
                     auto_adjust=True, group_by="ticker")
    out = {}
    for t in tickers:
        try:
            df = px[t] if len(tickers) > 1 else px
            if df["Close"].notna().sum() > 0:
                out[t] = df
        except (KeyError, TypeError):
            continue
    return out


def ret(px: pd.DataFrame, date: str, sessions: int) -> float | None:
    """Open on `date` to Close `sessions` sessions later. The frozen horizon is
    09:30->16:00 same session, which is sessions=1 (open and close of one bar)."""
    idx = px.index.normalize()
    hits = np.flatnonzero(idx == pd.Timestamp(date))
    if len(hits) == 0:
        return None
    i = hits[0]
    j = i + sessions - 1
    if j >= len(px):
        return None
    o, c = px["Open"].iloc[i], px["Close"].iloc[j]
    if not np.isfinite(o) or not np.isfinite(c) or o == 0:
        return None
    return float(c / o - 1.0)


# ------------------------------------------------------------------ statistics
def bootstrap(days: list[str], by_day: dict, fn, seed: int = SEED):
    """Day-clustered: resample whole mornings, not individual trades. Two calls
    made from the same morning's news are not independent observations."""
    rng = np.random.default_rng(seed)
    days = np.array(days)
    out = []
    for _ in range(N_BOOT):
        pick = rng.choice(days, size=len(days), replace=True)
        rows = [r for d in pick for r in by_day[d]]
        v = fn(rows)
        if v is not None:
            out.append(v)
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))) \
        if out else (float("nan"), float("nan"))


def mean_net(rows: list[dict]) -> float | None:
    """Direction-signed return, cost deducted. The primary statistic."""
    v = [(r["ret"] if r["direction"] == "up" else -r["ret"]) - COST for r in rows]
    return float(np.mean(v)) if v else None


def verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "positive, interval excludes zero"
    if hi < 0:
        return "negative, interval excludes zero"
    return "interval contains zero -> no detectable edge at this sample size"


# ------------------------------------------------------------------ main
def main() -> None:
    aliases, equity_tickers = load_universe()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT date_et, seq, market, direction, conviction, prospective
           FROM calls_v4 WHERE stream='E' AND input_rule='pre_open'
           ORDER BY date_et, seq"""
    ).fetchall()
    if not rows:
        raise SystemExit("calls_v4 is empty. Run extract_v4.py --submit/--collect.")

    mornings = {r["date_et"] for r in rows}
    passes = sum(1 for r in rows if r["market"] is None)
    named = [r for r in rows if r["market"] is not None]

    # ---- resolve
    resolved, unscoreable, untradeable = [], Counter(), Counter()
    for r in named:
        tic = resolve_market(r["market"], aliases, forward=bool(r["prospective"]))
        if tic is None:
            unscoreable[r["market"].strip().lower()] += 1
            continue
        if tic in NOT_TRADEABLE:
            untradeable[tic] += 1
            continue
        resolved.append({"date": r["date_et"], "ticker": tic,
                         "direction": r["direction"], "conviction": r["conviction"],
                         "market": r["market"], "prospective": r["prospective"]})

    print("=" * 72)
    print("v4 — routed news calls, stream E")
    print("=" * 72)
    print(f"mornings            {len(mornings)}")
    print(f"passes              {passes}  ({100*passes/len(mornings):.0f}% of mornings)")
    print(f"calls named         {len(named)}")
    print(f"calls per morning   {len(named)/len(mornings):.2f}", end="")
    print("   <- section 7: under ~2 means the floor rises above 0.3%"
          if len(named)/len(mornings) < 2 else "")
    print(f"unscoreable         {sum(unscoreable.values())} "
          f"({100*sum(unscoreable.values())/max(len(named),1):.0f}%)")
    print(f"resolved but untradeable (yields etc, excluded)  "
          f"{sum(untradeable.values())}")
    print(f"scoreable           {len(resolved)}")
    if unscoreable:
        print("  most common unresolved: " +
              ", ".join(f"{k} x{v}" for k, v in unscoreable.most_common(8)))

    # ---- prices
    tickers = {r["ticker"] for r in resolved} | {BENCH}
    lo = min(r["date"] for r in resolved)
    hi = (pd.Timestamp(max(r["date"] for r in resolved))
          + pd.Timedelta(days=20)).date().isoformat()
    print(f"\nfetching {len(tickers)} tickers, {lo} .. {hi}")
    px = fetch(tickers, lo, hi)
    missing = tickers - set(px)
    if missing:
        print(f"  no price history: {', '.join(sorted(missing))}")

    # ---- score every horizon
    results = {}
    for hname, sessions in HORIZONS.items():
        scored = []
        for r in resolved:
            if r["ticker"] not in px:
                continue
            v = ret(px[r["ticker"]], r["date"], sessions)
            b = ret(px[BENCH], r["date"], sessions) if BENCH in px else None
            if v is None:
                continue
            scored.append({**r, "ret": v, "bench": b})
        results[hname] = scored

    scored = results[PRIMARY_HORIZON]
    if not scored:
        raise SystemExit("nothing scoreable at the primary horizon")

    by_day = defaultdict(list)
    for r in scored:
        by_day[r["date"]].append(r)
    days = sorted(by_day)

    point = mean_net(scored)
    ci = bootstrap(days, by_day, mean_net)
    ups = sum(1 for r in scored if r["direction"] == "up")
    bench_mean = float(np.mean([r["bench"] for r in scored
                                if r["bench"] is not None]))

    print("\n" + "=" * 72)
    print("PRIMARY — mean net return per trade, 09:30->16:00, 10 bps")
    print("=" * 72)
    print(f"  n                 {len(scored)} trades over {len(days)} mornings")
    print(f"  point estimate    {100*point:+.3f}% per trade")
    print(f"  95% CI            [{100*ci[0]:+.3f}%, {100*ci[1]:+.3f}%]")
    print(f"  verdict           {verdict(*ci)}")
    print("\n  read these with it, not after it:")
    print(f"    direction split   {ups} up / {len(scored)-ups} down "
          f"({100*ups/len(scored):.0f}% long)")
    print(f"    market over the same windows  {100*bench_mean:+.3f}% per trade")
    print(f"    gross of costs                {100*(point+COST):+.3f}% per trade")

    # ---- secondary
    print("\n" + "-" * 72)
    print("SECONDARY (Bonferroni: 3 horizons, so read intervals as ~98.3%)")
    print("-" * 72)
    for hname in HORIZONS:
        s = results[hname]
        if not s:
            continue
        bd = defaultdict(list)
        for r in s:
            bd[r["date"]].append(r)
        p, c = mean_net(s), bootstrap(sorted(bd), bd, mean_net)
        tag = "  <- primary" if hname == PRIMARY_HORIZON else ""
        print(f"  {hname:<14} n={len(s):<5} {100*p:+.3f}%  "
              f"[{100*c[0]:+.3f}%, {100*c[1]:+.3f}%]{tag}")

    eq = [r for r in scored if r["ticker"] in equity_tickers and r["bench"] is not None]
    if eq:
        bd = defaultdict(list)
        for r in eq:
            bd[r["date"]].append({**r, "ret": r["ret"] - r["bench"]})
        p, c = mean_net([x for v in bd.values() for x in v]), \
            bootstrap(sorted(bd), bd, mean_net)
        print(f"\n  residual (equity subset only, beta assumed 1.0)")
        print(f"    n={len(eq)}  {100*p:+.3f}%  [{100*c[0]:+.3f}%, {100*c[1]:+.3f}%]")

    for conv in ("high", "low"):
        s = [r for r in scored if r["conviction"] == conv]
        if s:
            bd = defaultdict(list)
            for r in s:
                bd[r["date"]].append(r)
            p, c = mean_net(s), bootstrap(sorted(bd), bd, mean_net)
            print(f"  conviction {conv:<5} n={len(s):<5} {100*p:+.3f}%  "
                  f"[{100*c[0]:+.3f}%, {100*c[1]:+.3f}%]")

    # ---- exploratory
    print("\n" + "-" * 72)
    print("EXPLORATORY — not tests, do not quote without this label")
    print("-" * 72)
    gross = float(np.mean([(r["ret"] if r["direction"] == "up" else -r["ret"])
                           for r in scored]))
    print(f"  break-even cost   {100*gross:.3f}% per round trip")
    for bps in (5, 10, 25):
        print(f"  net at {bps:>2} bps     {100*(gross - bps/1e4):+.3f}% per trade")

    fwd = [r for r in scored if r["prospective"]]
    print(f"  forward-only      n={len(fwd)} — no figure quoted, 11 mornings "
          f"is a clean subset and not a result")

    # Section 8's independence diagnostic. A correlation coefficient is not
    # computable from one observation per name per morning, so the honest
    # measurable is co-movement: across every same-morning pair, how often did
    # the two instruments move the same way? 50% is what independence looks
    # like; well above that means same-day calls are closer to one bet than to
    # several, and the day-clustered bootstrap is carrying more weight than the
    # trade count suggests.
    pairs = together = 0
    for v in by_day.values():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                pairs += 1
                together += (v[i]["ret"] > 0) == (v[j]["ret"] > 0)
    multi = sum(1 for v in by_day.values() if len(v) > 1)
    if pairs:
        print(f"  same-day co-movement  {100*together/pairs:.0f}% of {pairs} pairs "
              f"across {multi} multi-call mornings (50% = independent)")

    top = Counter(r["ticker"] for r in scored)
    print("  most-traded       " +
          ", ".join(f"{k} x{v}" for k, v in top.most_common(8)))
    print(f"  distinct tickers  {len(top)}")
    conn.close()


if __name__ == "__main__":
    main()
