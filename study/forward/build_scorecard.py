r"""
Generate the scorecard page. Runs on the droplet after the daily label job.

Computes the numbers; `render.py` owns the presentation. Writes a self-contained
HTML file to /opt/forecast-labels/public/index.html, which Caddy serves at
https://DROPLET-IP.nip.io/scorecard/

Usage: python build_scorecard.py
"""

from __future__ import annotations

import html
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render  # noqa: E402
import forward_v2  # noqa: E402

LABEL_DB = HERE / "forecast_labels.db"
V2_JSON = HERE / "v2_result.json"
OUT_DIR = HERE / "public"
ET = ZoneInfo("America/New_York")

SEED = 42
N_BOOT = 10_000
DOLLARS_PER_POINT = 20.0
COST_POINTS = 0.75
CHECKPOINT_DAYS = 190          # pre-registered: 15pp detection floor
CHECKPOINT_2 = 315             # pre-registered: 10pp detection floor
PROMPT_SHA = "56764fad48373a4dbacb10e5cd09e4386d3320c24ca28a17407c3b63adc42f65"
TICKERS = {"NQ": "^NDX", "CL": "USO", "TNX": "^TNX"}


def load_labels() -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{LABEL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date_et, instrument, label, prospective FROM labels ORDER BY date_et"
    ).fetchall()
    conn.close()
    return rows


def prices(ticker: str, lo: str, hi: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=(pd.Timestamp(lo) - pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
        end=(pd.Timestamp(hi) + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=False, progress=False, threads=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[["Open", "Close"]].dropna()


def cond_diff(rows: list[tuple[str, str]]) -> float:
    su = [a for s, a in rows if s == "up"]
    sd = [a for s, a in rows if s == "down"]
    if not su or not sd:
        return float("nan")
    return sum(x == "up" for x in su) / len(su) - sum(x == "up" for x in sd) / len(sd)


def money(v: float) -> str:
    return f"{'-' if v < 0 else ''}${abs(v):,.0f}"


def pp(v: float) -> str:
    return f"{v*100:+.1f}pp"


def main() -> None:
    rows = load_labels()
    OUT_DIR.mkdir(exist_ok=True)
    now = datetime.now(ET)

    if not rows:
        OUT_DIR.joinpath("index.html").write_text(
            f"<!doctype html><meta charset=utf-8><title>No data</title>"
            f"<p>No labels yet. Generated {now:%Y-%m-%d %H:%M %Z}.</p>",
            encoding="utf-8")
        print("no labels; wrote placeholder")
        return

    days_all = sorted({r["date_et"] for r in rows})
    fwd = sorted({r["date_et"] for r in rows if r["prospective"]})
    retro = [d for d in days_all if d not in set(fwd)]

    # ---- dollar view: NQ only, ^NDX points x $20 -------------------------
    ndx = prices("^NDX", days_all[0], days_all[-1])
    trades: list[tuple[str, str, float]] = []
    for r in rows:
        if r["instrument"] != "NQ" or r["label"] not in ("up", "down"):
            continue
        ts = pd.Timestamp(r["date_et"])
        if ts not in ndx.index:
            continue
        o, c = float(ndx.loc[ts, "Open"]), float(ndx.loc[ts, "Close"])
        mv = c - o
        pts = mv if r["label"] == "up" else -mv
        # Percent of the index level, so Study I can be read on the same
        # footing as Study II's fifteen instruments. NET of the same round-trip
        # cost the dollar figure carries: showing a net dollar column beside a
        # gross percent column would describe one trade two different ways.
        pct = (pts - COST_POINTS) / o if o else 0.0
        trades.append((r["date_et"], r["label"],
                       pts * DOLLARS_PER_POINT - COST_POINTS * DOLLARS_PER_POINT,
                       pct))

    net = np.array([t[2] for t in trades]) if trades else np.array([0.0])
    pcts = np.array([t[3] for t in trades]) if trades else np.array([0.0])
    total = float(net.sum())
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(net))
    t_lo, t_hi = np.percentile(
        [net[rng.choice(idx, size=len(idx), replace=True)].sum()
         for _ in range(N_BOOT)], [2.5, 97.5])
    wins = int((net > 0).sum())

    # ---- primary test: all instruments, day-clustered bootstrap ----------
    px = {k: prices(v, days_all[0], days_all[-1]) for k, v in TICKERS.items()}
    by_day: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in rows:
        if r["label"] not in ("up", "down"):
            continue
        p = px[r["instrument"]]
        ts = pd.Timestamp(r["date_et"])
        if ts not in p.index:
            continue
        d = float(p.loc[ts, "Close"]) - float(p.loc[ts, "Open"])
        if d == 0:
            continue
        by_day[r["date_et"]].append((r["label"], "up" if d > 0 else "down"))

    sday = sorted(by_day)
    flat = [x for d in sday for x in by_day[d]]
    cd = cond_diff(flat)
    rng2 = np.random.default_rng(SEED)
    di = np.arange(len(sday))
    vals = []
    for _ in range(N_BOOT):
        draw = rng2.choice(di, size=len(di), replace=True)
        v = cond_diff([x for i in draw for x in by_day[sday[i]]])
        if v == v:
            vals.append(v)
    c_lo, c_hi = np.percentile(vals, [2.5, 97.5])

    table_rows = "".join(
        f"<tr><td>{html.escape(d)}</td><td>{html.escape(s)}</td>"
        f"<td>{money(v)}</td><td>{pc*100:+.2f}%</td></tr>"
        for d, s, v, pc in reversed(trades[-15:])
    )

    # Study II's retrospective numbers travel as JSON: the droplet has no copy
    # of news_corpus.db, and the result is fixed until forward days accrue.
    v2 = (json.loads(V2_JSON.read_text(encoding="utf-8"))
          if V2_JSON.exists() else None)

    # Close the loop: retrospective figures are fixed and travel as JSON, the
    # forward record is recomputed from calls_v2.db on every build.
    # NB: `fwd` above is v1's forward DAY LIST and is still needed below.
    v2_forward = forward_v2.compute()
    if v2 is not None:
        v2["forward"] = v2_forward

    # Machine-readable health, served next to the page so an external check can
    # assert on it without scraping HTML.
    status = v2_forward["status"] if v2_forward else forward_v2.status()
    OUT_DIR.joinpath("status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8")

    page = render.page(
        now=now, days_all=days_all, fwd_days=fwd, retro_days=retro,
        trades=trades, total=total, t_lo=float(t_lo), t_hi=float(t_hi),
        wins=wins,
        net_std=money(float(net.std(ddof=1))) if len(net) > 1 else "n/a",
        cd=cd, c_lo=float(c_lo), c_hi=float(c_hi), n_calls=len(flat),
        inconclusive=bool(c_lo < 0 < c_hi),
        checkpoint=CHECKPOINT_DAYS, checkpoint_2=CHECKPOINT_2,
        table_rows=table_rows, money=money, pp=pp, prompt_sha=PROMPT_SHA,
        dollars_per_point=DOLLARS_PER_POINT, cost_points=COST_POINTS,
        n_boot=N_BOOT, seed=SEED, v2=v2,
        pct_total=float(pcts.sum()), pct_mean=float(pcts.mean()),
    )
    OUT_DIR.joinpath("index.html").write_text(page, encoding="utf-8")
    print(f"wrote {OUT_DIR/'index.html'}  ({len(days_all)} days, "
          f"{len(trades)} trades, net {money(total)}, {len(page):,} bytes)")


if __name__ == "__main__":
    main()
