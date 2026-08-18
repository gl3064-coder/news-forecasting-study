r"""Frozen name-line taxonomy for v3 (PRE_REGISTRATION_V3.md section 4a).

The name line exists because it was measured, before the v3 freeze, that an
index built from subject lines and roundup bullets surfaces only 6% (median) of
the tradable names present in a morning's text. `Dow Jones` appears in the text
on 90 of 164 mornings and in such an index on 0 of them. Market names live in
body prose, not in headlines.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not drop anything. Every one of the 165 entries in MARKETS +
MARKETS_FORWARD appears on the name line. This file only decides which of two
groups an entry is printed under.

A drop list was attempted first and rejected. The rule tried was "drop any entry
that fires on more than half the corpus mornings", on the theory that a name
present most days cannot distinguish today from any other day. Measured against
the 164 retrospective mornings it dropped brent (86%), oil (80%), gold (71%),
crude oil (56%), s&p 500 (55%), dow jones (55%), nasdaq 100 (54%) — the six most
tradable markets in the table, including the one the bot trades on 68% of its
calls. "Always mentioned" and "not informative" are different properties for a
market. The rule was measuring the wrong axis and was discarded rather than
tuned.

The real problem is word sense, not frequency: `energy`, `tech`, `target`,
`retail`, `materials` fire on ordinary prose that has nothing to do with the
sector or the issuer. Grouping addresses that without removing information. The
rater sees SPECIFIC first and THEMATIC second, and both are complete.

THE TAXONOMY
------------
SPECIFIC   names one issuer, one named index, or one named commodity, currency
           or rate instrument. A match is very likely to be about that thing.
THEMATIC   names a sector, a country, or a broad theme. A match may be ordinary
           prose. Kept and shown, ranked second, never removed.

This is a taxonomy of what each string denotes, not a judgment about which
markets are worth trading. It is committed once and is not edited after the v3
freeze (section 9.4). Any change is a Correction under section 11, with both
versions retained.
"""

from __future__ import annotations

import re

from markets_v2 import MARKETS, MARKETS_FORWARD

ALL_NAMES: list[str] = sorted(set(MARKETS) | set(MARKETS_FORWARD))

# ---------------------------------------------------------------- the taxonomy
# Sectors, countries, and broad themes. Everything not listed here is SPECIFIC.
THEMATIC: frozenset[str] = frozenset({
    # sectors and industry groups
    "agriculture", "airlines", "banks", "biotech", "banks", "chips",
    "clean energy", "communication services", "consumer discretionary",
    "consumer staples", "defense", "energy", "energy stocks", "financials",
    "gold miners", "healthcare", "homebuilders", "industrials",
    "infrastructure", "materials", "oil services", "real estate",
    "regional banks", "retail", "retail stocks", "semiconductors", "tech",
    "technology", "transports", "utilities", "cannabis", "cannabis stocks",
    "china tech", "uranium",
    # countries and regions (the place, not a named index)
    "brazil", "china", "chinese stocks", "emerging markets",
    "emerging market currencies", "europe", "european stocks", "germany",
    "german stocks", "india", "indian stocks", "japan", "japanese stocks",
    "korea", "mexico", "taiwan", "uk", "british stocks",
    # broad asset themes with no single instrument
    "bonds", "bond yields", "corporate bonds", "high yield", "junk bonds",
    "long bonds", "long-dated bonds", "small caps", "treasuries",
    "treasury bonds", "treasury yields", "yields", "volatility", "oil",
    "crude", "dollar",
})

SPECIFIC: frozenset[str] = frozenset(ALL_NAMES) - THEMATIC

# Compiled once. Word-boundary, case-insensitive, no stemming, no fuzzy match.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (n, re.compile(r"\b" + re.escape(n) + r"\b", re.I)) for n in ALL_NAMES
]


def scan(text: str) -> tuple[list[str], list[str]]:
    """Return (specific, thematic), each alphabetical, from one morning's text.

    Both lists are complete. Nothing found is withheld. A name is reported if
    the string occurs; whether the occurrence is *about* that market is the
    rater's judgment to make, which is the whole point of v3.
    """
    hits = {n for n, p in _PATTERNS if p.search(text)}
    return (
        sorted(h for h in hits if h in SPECIFIC),
        sorted(h for h in hits if h in THEMATIC),
    )


def render(text: str) -> str:
    """The name line as the rater sees it."""
    spec, them = scan(text)
    out = []
    out.append("  SPECIFIC  " + (", ".join(spec) if spec else "(none)"))
    out.append("  THEMATIC  " + (", ".join(them) if them else "(none)"))
    return "\n".join(out)


# ------------------------------------------------------------------ call_type
# Section 5: call_type is DERIVED, never asked, so the rater cannot classify his
# own calls inconsistently or with hindsight. The same regex is applied to
# stream A's 164 calls to produce the 10% single-name baseline in section 1.
_BROAD = re.compile(
    r"crude|brent|wti|nasdaq|s&p|dow|gold|silver|copper|treasur|yield|gilt|"
    r"bund|natural gas|gasoline|rbob|heating oil|dollar|euro|yen|franc|pound|"
    r"nikkei|dax|ftse|hang seng|nifty|bitcoin|ether|russell|vix|\boil\b|"
    r"index|futures curve|sector|stocks\b|equities\b|complex\b",
    re.I,
)


def call_type(market: str) -> str:
    """'broad' or 'single_name' for a market string, frozen at the v3 freeze."""
    return "broad" if _BROAD.search(market or "") else "single_name"
