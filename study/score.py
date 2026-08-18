r"""
Stage 2: pull prices and score the frozen rule.

This is the unblinding step. It applies PRE_REGISTRATION.md exactly as frozen,
including Amendment 1 (primary statistic = conditional difference) and
Amendment 2 (score on cash-session instruments so the measured window is the
pre-registered 09:30-16:00 horizon).

Nothing here selects a variant. There is one primary number, one secondary
number, and an explicitly-labelled exploratory section.

Usage: python score.py
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
SEED = 42
N_BOOT = 10_000

# Amendment 2: RTH-session instruments are primary; =F reported as a check.
PRIMARY_TICKER = {"NQ": "^NDX", "CL": "USO", "TNX": "^TNX"}
CHECK_TICKER = {"NQ": "NQ=F", "CL": "CL=F", "TNX": "^TNX"}


# ------------------------------------------------------------------ prices
def fetch(tickers: dict[str, str], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for inst, tk in tickers.items():
        df = yf.download(
            tk,
            start=(pd.Timestamp(lo) - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            end=(pd.Timestamp(hi) + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=False, progress=False, threads=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out[inst] = df[["Open", "Close"]].dropna()
        print(f"  {inst:<5} {tk:<7} {len(out[inst]):>4} sessions")
    return out


def outcome(px: pd.DataFrame, date: str) -> str | None:
    """Sign of the open-to-close move on `date`, or None if no session."""
    ts = pd.Timestamp(date)
    if ts not in px.index:
        return None
    row = px.loc[ts]
    diff = float(row["Close"]) - float(row["Open"])
    if diff == 0:
        return None
    return "up" if diff > 0 else "down"


# ------------------------------------------------------------------ stats
def conditional_difference(rows: list[tuple[str, str, str]]) -> float:
    """P(actual up | said up) - P(actual up | said down). rows = (day, said, actual)."""
    said_up = [a for _, s, a in rows if s == "up"]
    said_dn = [a for _, s, a in rows if s == "down"]
    if not said_up or not said_dn:
        return float("nan")
    p_up = sum(a == "up" for a in said_up) / len(said_up)
    p_dn = sum(a == "up" for a in said_dn) / len(said_dn)
    return p_up - p_dn


def hit_rate_vs_base(rows: list[tuple[str, str, str, str]]) -> float:
    """Secondary (original) statistic: hit rate minus each instrument's own
    up-rate over the same scored days. rows = (day, inst, said, actual)."""
    if not rows:
        return float("nan")
    hits = sum(s == a for _, _, s, a in rows)
    by_inst: dict[str, list[str]] = defaultdict(list)
    for _, inst, _, a in rows:
        by_inst[inst].append(a)
    base = sum(
        sum(x == "up" for x in acts) / len(acts) * len(acts) for acts in by_inst.values()
    )
    return hits / len(rows) - base / len(rows)


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
    arr = np.array(vals)
    return arr, skipped


def verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "CI excludes zero, positive -> evidence of directional information"
    if hi < 0:
        return "CI excludes zero, negative -> ANTI-predictive"
    return "CI includes zero -> no detectable directional information at this sample size"


# ------------------------------------------------------------------ main
def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    labels = conn.execute(
        """SELECT date_et, instrument, label FROM labels
           WHERE label IN ('up','down') ORDER BY date_et, instrument"""
    ).fetchall()
    all_days = sorted({r["date_et"] for r in conn.execute(
        "SELECT DISTINCT date_et FROM labels")})
    conn.close()

    print(f"labelled days:        {len(all_days)}")
    print(f"directional calls:    {len(labels)}  (no_call rows already excluded)")
    print(f"\ndownloading prices ({all_days[0]} .. {all_days[-1]})")
    print("  PRIMARY (RTH cash session, Amendment 2)")
    prim = fetch(PRIMARY_TICKER, all_days[0], all_days[-1])
    print("  CHECK (futures, includes overnight)")
    chk = fetch(CHECK_TICKER, all_days[0], all_days[-1])

    for name, px in (("PRIMARY", prim), ("CHECK", chk)):
        rows4: list[tuple[str, str, str, str]] = []
        dropped = 0
        for r in labels:
            act = outcome(px[r["instrument"]], r["date_et"])
            if act is None:
                dropped += 1
                continue
            rows4.append((r["date_et"], r["instrument"], r["label"], act))

        print("\n" + "=" * 66)
        print(f"{name}  " + ("(^NDX / USO / ^TNX, 09:30-16:00)" if name == "PRIMARY"
                             else "(NQ=F / CL=F / ^TNX, includes overnight)"))
        print("=" * 66)
        print(f"  scored instrument-days   {len(rows4)}")
        print(f"  dropped (no session)     {dropped}")
        if not rows4:
            print("  nothing to score")
            continue

        days = sorted({d for d, _, _, _ in rows4})
        print(f"  distinct days            {len(days)}")

        n_up = sum(s == "up" for _, _, s, _ in rows4)
        n_dn = len(rows4) - n_up
        print(f"  said up / said down      {n_up} / {n_dn}")

        # ---------- PRIMARY STATISTIC ----------
        by_day3: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for d, _, s, a in rows4:
            by_day3[d].append((d, s, a))
        point = conditional_difference([r for d in days for r in by_day3[d]])
        arr, skipped = bootstrap(days, by_day3, conditional_difference)
        lo, hi = np.percentile(arr, [2.5, 97.5])

        su = [a for _, s, a in (r for d in days for r in by_day3[d]) if s == "up"]
        sd = [a for _, s, a in (r for d in days for r in by_day3[d]) if s == "down"]
        print(f"\n  P(up | said up)          {sum(x=='up' for x in su)}/{len(su)}"
              f" = {sum(x=='up' for x in su)/len(su)*100:.0f}%")
        print(f"  P(up | said down)        {sum(x=='up' for x in sd)}/{len(sd)}"
              f" = {sum(x=='up' for x in sd)/len(sd)*100:.0f}%")
        print(f"\n  PRIMARY  conditional difference = {point*100:+.1f}pp")
        print(f"           95% CI [{lo*100:+.1f}pp, {hi*100:+.1f}pp]"
              f"   ({len(arr)} draws, {skipped} discarded)")
        print(f"           {verdict(lo, hi)}")

        # ---------- SECONDARY ----------
        by_day4: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for row in rows4:
            by_day4[row[0]].append(row)
        pt2 = hit_rate_vs_base([r for d in days for r in by_day4[d]])
        arr2, sk2 = bootstrap(days, by_day4, hit_rate_vs_base)
        lo2, hi2 = np.percentile(arr2, [2.5, 97.5])
        hits = sum(s == a for _, _, s, a in rows4)
        print(f"\n  SECONDARY  raw hit rate = {hits}/{len(rows4)}"
              f" = {hits/len(rows4)*100:.0f}%")
        print(f"             minus base rate = {pt2*100:+.1f}pp"
              f"   95% CI [{lo2*100:+.1f}pp, {hi2*100:+.1f}pp]")

        # ---------- EXPLORATORY ----------
        print("\n  EXPLORATORY (not a test — per-instrument breakdown)")
        print(f"    {'inst':<6}{'n':>4}{'said up':>9}{'said dn':>9}{'hit':>7}{'cond diff':>12}")
        for inst in ("NQ", "CL", "TNX"):
            sub = [r for r in rows4 if r[1] == inst]
            if not sub:
                continue
            h = sum(s == a for _, _, s, a in sub)
            cd = conditional_difference([(d, s, a) for d, _, s, a in sub])
            cds = "     n/a" if cd != cd else f"{cd*100:>+11.1f}pp"
            print(f"    {inst:<6}{len(sub):>4}{sum(s=='up' for _,_,s,_ in sub):>9}"
                  f"{sum(s=='down' for _,_,s,_ in sub):>9}"
                  f"{h/len(sub)*100:>6.0f}%{cds}")

    print("\n" + "=" * 66)
    print("Per section 7: all of this is RETROSPECTIVE and underpowered by")
    print("design. Re-run this identical script as the forward sample grows.")
    print("Do not change the rule now that the number is visible.")


if __name__ == "__main__":
    main()
