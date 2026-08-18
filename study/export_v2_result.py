r"""
Export v2's scored result to a small JSON the droplet can render.

The droplet has no copy of news_corpus.db (22MB of newsletters it does not
need), and v2's retrospective result is fixed until forward days accrue. So the
numbers travel as data rather than as a database.

Re-run this after any re-score, then redeploy droplet/v2_result.json.

Usage: python export_v2_result.py
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from markets_v2 import resolve
from score_v2 import (DB, HORIZON_SESSIONS, bootstrap, fetch, outcome,
                      statistic, up_rate)

HERE = Path(__file__).parent
OUT = HERE / "droplet" / "v2_result.json"

# Plain-language name per ticker, for the ledger. Hand-written rather than
# derived from MARKETS: the shortest key that maps to a ticker is often the
# least clear one ("yields" beats "10-year yield" on length, not on sense).
TICKER_NAME = {
    "BNO": "Brent crude", "USO": "WTI crude", "UNG": "Natural gas",
    "GLD": "Gold", "SLV": "Silver", "CPER": "Copper", "DBA": "Agriculture",
    "^NDX": "Nasdaq 100", "^GSPC": "S&P 500", "^DJI": "Dow",
    "^RUT": "Russell 2000", "^VIX": "VIX",
    "^FVX": "5-year yield", "^TNX": "10-year yield", "^TYX": "30-year yield",
    "TLT": "Long bonds", "LQD": "Corporate bonds", "HYG": "High yield",
    "DX-Y.NYB": "Dollar", "FXE": "Euro", "FXY": "Yen", "FXB": "Pound",
    "FXF": "Swiss franc", "FXC": "Canadian dollar",
    "CEW": "EM currencies", "IBIT": "Bitcoin", "ETHA": "Ether",
    "XLK": "Technology", "SMH": "Semiconductors", "XLF": "Financials",
    "XLE": "Energy", "XLV": "Healthcare", "XLU": "Utilities",
    "XLI": "Industrials", "XLY": "Consumer disc.", "XLP": "Consumer staples",
    "XLRE": "Real estate", "XLB": "Materials", "XLC": "Communications",
    "XHB": "Homebuilders", "KRE": "Regional banks", "ITA": "Defense",
    "GDX": "Gold miners", "VGK": "Europe", "EWJ": "Japan", "FXI": "China",
    "EEM": "Emerging markets",
    # Single names: the ticker is not a display name, and repeating it in both
    # the name and ticker columns reads as a rendering bug rather than a stock.
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon",
    "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom",
    "NFLX": "Netflix", "JPM": "JPMorgan", "GS": "Goldman Sachs",
    "XOM": "Exxon", "CVX": "Chevron", "WMT": "Walmart", "LLY": "Eli Lilly",
}

STREAM_LABEL = {
    "A": "Raw news, must call",
    "B": "Raw news, may pass",
    "C": "Pulse digest, must call",
    "D": "Pulse digest, may pass",
}


def pct_move(px, date: str) -> float | None:
    """Open-to-close move as a fraction of the open. Percent rather than
    dollars because Study II spans 15 instruments: a point of gold is not a
    dollar of Nasdaq, and raw contract P&L cannot be summed across them."""
    ts = pd.Timestamp(date)
    if ts not in px.index:
        return None
    o, c = float(px.loc[ts, "Open"]), float(px.loc[ts, "Close"])
    return (c - o) / o if o else None


def chance_beats(rows: list, observed: float, n_iter: int = 10_000,
                 seed: int = 7) -> float:
    """Fraction of random-direction histories that match or beat the observed
    skill. Answers 'how often does luck alone do this well?' directly, rather
    than leaving it to be inferred from an interval.

    The calls' markets, dates and outcomes are held fixed; only the up/down
    decisions are reshuffled, so this asks whether the DIRECTIONS carried
    information. Exploratory: it is a restatement of the frozen statistic's
    uncertainty, not a second pre-registered test."""
    rng = np.random.default_rng(seed)
    dirs = np.array([r[2] for r in rows])
    beat = 0
    for _ in range(n_iter):
        shuffled = rng.permutation(dirs)
        s = statistic([(r[0], r[1], d, r[3], r[4])
                       for r, d in zip(rows, shuffled)])
        if s >= observed:
            beat += 1
    return beat / n_iter


def pct_block(recs: list, n_boot: int = 10_000, seed: int = 42) -> dict:
    """Study I reports dollars because it traded one contract. Study II cannot:
    summing raw P&L across gold, crude and the Nasdaq would be adding
    incompatible units. Percent of the position is the normalisation that makes
    the instruments commensurable, and it is exploratory either way —
    section 9 does not claim tradeability."""
    vals = [r[7] for r in recs if r[7] is not None]
    if not vals:
        return {}
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in recs:
        if r[7] is not None:
            by_day[r[0]].append(r[7])
    days = sorted(by_day)

    rng = np.random.default_rng(seed)
    idx = np.arange(len(days))
    totals = []
    for _ in range(n_boot):
        draw = rng.choice(idx, size=len(idx), replace=True)
        totals.append(sum(v for i in draw for v in by_day[days[i]]))
    lo, hi = np.percentile(totals, [2.5, 97.5])

    arr = np.array(vals)
    return {
        "n": len(vals),
        "total": float(arr.sum()),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "best": float(arr.max()),
        "worst": float(arr.min()),
        "lo": float(lo),
        "hi": float(hi),
        "win_rate": float((arr > 0).mean()),
    }


def score(conn, stream, prices, all_days,
          input_rule: str = 'pre_open') -> dict | None:
    calls = conn.execute(
        """SELECT date_et, market, direction, conviction, horizon
           FROM calls_v2 WHERE stream=? AND prospective=0
             AND input_rule=?
           ORDER BY date_et""", (stream, input_rule)
    ).fetchall()
    live = [c for c in calls if c["direction"] != "none"]
    recs, unscoreable = [], 0
    for c in live:
        tk = resolve(c["market"])
        if tk is None or prices.get(tk) is None:
            unscoreable += 1
            continue
        act = outcome(prices[tk], c["date_et"], 1)
        if act is None:
            continue
        mv = pct_move(prices[tk], c["date_et"])
        signed = None if mv is None else (mv if c["direction"] == "up" else -mv)
        recs.append((c["date_et"], tk, c["direction"], act,
                     up_rate(prices[tk], all_days), c["conviction"],
                     c["market"], signed))
    if not recs:
        return None

    rows = [r[:5] for r in recs]
    days = sorted({r[0] for r in rows})
    by_day = defaultdict(list)
    for r in rows:
        by_day[r[0]].append(r)
    arr, _ = bootstrap(days, by_day, statistic)
    lo, hi = np.percentile(arr, [2.5, 97.5])

    conv = {}
    for c in ("high", "low"):
        sub = [r[:5] for r in recs if r[5] == c]
        if sub:
            conv[c] = {"n": len(sub), "skill": statistic(sub)}

    top = defaultdict(int)
    for r in recs:
        top[r[1]] += 1

    # Per-market ledger: the point of an open universe is that the instrument
    # varies, so how it did on each one is the view the aggregate hides.
    by_market: dict[str, dict] = {}
    for _, tk, direction, actual, _, _, _, signed in recs:
        m = by_market.setdefault(tk, {"ticker": tk, "n": 0, "hits": 0,
                                     "ret": 0.0,
                                     "name": TICKER_NAME.get(tk, tk)})
        m["n"] += 1
        m["hits"] += direction == actual
        if signed is not None:
            m["ret"] += signed
    for m in by_market.values():
        m["hit_rate"] = m["hits"] / m["n"]
        m["ret_per"] = m["ret"] / m["n"]

    return {
        "stream": stream,
        "label": STREAM_LABEL[stream],
        "n": len(rows),
        "days": len(days),
        "passed": len(calls) - len(live),
        "unscoreable": unscoreable,
        "unscoreable_pct": unscoreable / max(len(live), 1),
        "hits": sum(d == a for _, _, d, a, _ in rows),
        "hit_rate": sum(d == a for _, _, d, a, _ in rows) / len(rows),
        "skill": statistic(rows),
        "lo": float(lo),
        "hi": float(hi),
        "conviction": conv,
        "top_markets": sorted(top.items(), key=lambda kv: -kv[1])[:6],
        "pct": pct_block(recs),
        "chance": chance_beats(rows, statistic(rows)),
        "by_market": sorted(by_market.values(),
                            key=lambda m: (-m["n"], m["ticker"])),
        "calls": [
            {"date": day, "market": mkt, "ticker": tk, "call": direction,
             "name": TICKER_NAME.get(tk, tk), "actual": actual,
             "hit": direction == actual, "conviction": cv, "ret": signed}
            for day, tk, direction, actual, _, cv, mkt, signed in recs
        ],
    }


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    all_days = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT date_et FROM calls_v2 "
        "WHERE prospective=0 AND input_rule='pre_open'")})
    markets = {r[0] for r in conn.execute(
        "SELECT DISTINCT market FROM calls_v2 WHERE direction != 'none'")}
    tickers = {t for t in (resolve(m) for m in markets) if t}
    tickers |= {t for t in (resolve(m, forward=True) for m in markets) if t}
    print(f"{len(all_days)} days, {len(tickers)} tickers")
    prices = fetch(tickers, all_days[0], all_days[-1])

    streams = {}
    for s in "ABCD":
        r = score(conn, s, prices, all_days)
        if r:
            streams[s] = r
            print(f"  {s}: n={r['n']:>4}  skill {r['skill']*100:+.1f}pp"
                  f"  [{r['lo']*100:+.1f}, {r['hi']*100:+.1f}]")

    payload = {
        "window": [all_days[0], all_days[-1]],
        "days": len(all_days),
        "primary": "A",
        "streams": streams,
        "prompt_sha": "a1cdd1ccc881d91253875a91cd2ca6979e9f2f79f74950c7276f8f6ab6920625",
        "forward_days": conn.execute(
            "SELECT COUNT(DISTINCT date_et) FROM calls_v2 WHERE prospective=1"
        ).fetchone()[0],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    conn.close()


if __name__ == "__main__":
    main()
