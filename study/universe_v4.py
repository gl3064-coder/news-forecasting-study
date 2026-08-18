r"""Freeze the v4 single-name universe. Run once, before any extraction.

v4 asks the extractor to name individual equities. Resolution therefore runs
model-output -> ticker, not text-scan -> ticker, which is the safer direction:
the ambiguity that made `target` and `gap` a problem when scanning prose does
not arise when the model has deliberately named a company as its trade.

WHY THE UNIVERSE IS FROZEN BEFORE EXTRACTION
--------------------------------------------
v2 refused a rule that would have closed most of its unscoreable gap: "use
whatever ticker the model names." That hands the choice of measuring instrument
to the thing being measured. The alternative that keeps the same discipline is
to declare the universe in advance and score against it, which is what v2's
Amendment 3 did on a small scale (71 names) and what this does at full size.

Anything the model names that is not in the frozen universe is logged
UNSCOREABLE. That is a cost, and it is the correct cost to pay.

SOURCES
-------
1. S&P 500 constituents, Wikipedia, fetched once and cached to
   `universe_v4_source.csv` so the freeze is reproducible without the network.
2. The 71 forward-only aliases already in `markets_v2.MARKETS_FORWARD`, which
   carry hand-checked colloquial names ("google" -> GOOGL, "meta" -> META).

KNOWN LIMITATION, DECLARED
--------------------------
Membership is current as of the freeze date and is applied to a window running
2025-10-23 to 2026-08-14. Companies that joined or left the index during the
window are treated as members throughout. This is survivorship bias. Over a
~10-month window it touches on the order of 10-15 names out of 503, it applies
identically to every stream, and it cannot flatter one arm over another because
all arms resolve against the same table. It is not corrected.

Usage: python universe_v4.py [--refresh]
"""

from __future__ import annotations

import io
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import requests
import yfinance as yf

from markets_v2 import MARKETS_FORWARD, NOT_TRADEABLE

HERE = Path(__file__).parent
SOURCE = HERE / "universe_v4_source.csv"
OUT = HERE / "universe_v4.json"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UA = {"User-Agent": "Mozilla/5.0 (research; news-corpus v4 universe freeze)"}

# Validation window: the corpus plus the forward block, plus slack for the
# 1-week secondary horizon to have somewhere to land.
VAL_START, VAL_END = "2025-10-01", "2026-08-15"
MIN_SESSIONS = 150

# Legal-form suffixes carry no identifying information. Stripping them turns
# "Alphabet Inc. (Class A)" into "alphabet", which is what news text calls it.
_SUFFIX = re.compile(
    r"\s*(?:,)?\s*\b(?:inc|inc\.|incorporated|corp|corp\.|corporation|company|"
    r"co|co\.|ltd|ltd\.|limited|plc|holdings?|group|the|n\.v\.|s\.a\.|"
    r"international|technologies|technology|systems|worldwide|enterprises)\b\.?",
    re.I,
)
_PAREN = re.compile(r"\s*\([^)]*\)")
_AMP = re.compile(r"\s*&\s*")


def aliases(security: str) -> set[str]:
    """Colloquial forms of a company name. Conservative: no single letters, no
    fragments shorter than four characters, nothing that is only a legal form."""
    base = _PAREN.sub("", security).strip()
    out = {base.lower()}
    short = _SUFFIX.sub("", base).strip(" ,.")
    if short:
        out.add(short.lower())
        out.add(_AMP.sub(" and ", short).lower())
        out.add(_AMP.sub("", short).lower())
    return {a for a in out if len(a) >= 4 and not a.isnumeric()}


def fetch_sp500() -> pd.DataFrame:
    r = requests.get(SP500_URL, headers=UA, timeout=45)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    df = next(t for t in tables if "Symbol" in t.columns and "Security" in t.columns)
    return df[["Symbol", "Security", "GICS Sector"]].copy()


def main() -> None:
    if OUT.exists() and "--refresh" not in sys.argv:
        sys.exit(f"{OUT.name} exists. The universe is frozen. Re-deriving it "
                 f"after extraction would change what calls resolve; "
                 f"pass --refresh only before any v4 call exists.")

    if SOURCE.exists() and "--refresh" not in sys.argv:
        df = pd.read_csv(SOURCE)
    else:
        df = fetch_sp500()
        df.to_csv(SOURCE, index=False)
    print(f"S&P 500 source: {len(df)} constituents")

    # ---- alias -> ticker, S&P 500 first, then the hand-checked v2 aliases
    table: dict[str, str] = {}
    collisions: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        tic = str(row["Symbol"]).strip().upper().replace(".", "-")
        for a in aliases(str(row["Security"])):
            if a in table and table[a] != tic:
                collisions.setdefault(a, {table[a]}).add(tic)
                continue
            table[a] = tic

    # Drop ambiguous aliases FIRST, then let v2's hand-checked table override.
    # Order matters: "alphabet" collides on GOOGL/GOOG because both share classes
    # sit in the index, but markets_v2 already resolved that to GOOGL against
    # actual news usage. Popping after the override would discard the answer.
    for a in collisions:
        table.pop(a, None)

    n_v2 = 0
    for name, tic in MARKETS_FORWARD.items():
        if tic in NOT_TRADEABLE or not re.fullmatch(r"[A-Z][A-Z0-9-]{0,5}", tic):
            continue
        table[name.lower()] = tic
        n_v2 += 1

    still_ambiguous = sorted(a for a in collisions if a not in table)
    print(f"aliases: {len(table)}  (v2 hand-checked: {n_v2}, "
          f"ambiguous found: {len(collisions)}, "
          f"left unresolved: {len(still_ambiguous)})")
    collisions = {a: collisions[a] for a in still_ambiguous}

    # ---- every ticker must have real price history or it cannot be scored
    tickers = sorted(set(table.values()))
    print(f"validating {len(tickers)} tickers against {VAL_START}..{VAL_END}")
    px = yf.download(tickers, start=VAL_START, end=VAL_END,
                     progress=False, auto_adjust=True)["Close"]
    good = {t for t in tickers if t in px.columns and px[t].notna().sum() >= MIN_SESSIONS}
    dropped = sorted(set(tickers) - good)
    table = {a: t for a, t in table.items() if t in good}

    print(f"  tickers with >= {MIN_SESSIONS} sessions: {len(good)}")
    if dropped:
        print(f"  dropped {len(dropped)}: {', '.join(dropped[:20])}"
              f"{' ...' if len(dropped) > 20 else ''}")

    OUT.write_text(json.dumps({
        "frozen_at": pd.Timestamp.utcnow().isoformat(),
        "source": "S&P 500 (Wikipedia) + markets_v2.MARKETS_FORWARD",
        "validation_window": [VAL_START, VAL_END],
        "min_sessions": MIN_SESSIONS,
        "n_tickers": len(good),
        "n_aliases": len(table),
        "dropped_tickers": dropped,
        "ambiguous_aliases_dropped": sorted(collisions),
        "aliases": dict(sorted(table.items())),
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT.name}: {len(table)} aliases -> {len(good)} tickers")


if __name__ == "__main__":
    main()
