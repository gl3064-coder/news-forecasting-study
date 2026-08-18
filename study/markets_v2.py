"""
FROZEN market resolution table for forecast experiment v2.

PRE_REGISTRATION_V2.md section 5: resolution is exact, never approximate. A
named market either appears here or the call is recorded as unscoreable. It is
NOT stretched onto the nearest available proxy.

Cash-session instruments are preferred throughout (the v1 Amendment 2 lesson):
a daily bar for a `=F` futures ticker opens at the Globex session start the
previous evening, so scoring it open-to-close would measure a ~22-hour window
including the overnight move the 08:00 forecast already knew about.

This file is frozen once committed. Adding an entry after labels exist changes
which calls are scoreable, so it requires an Amendments entry in
PRE_REGISTRATION_V2.md with a date and a reason.
"""

from __future__ import annotations

import re

# name -> ticker. Keys must be lowercase, single-spaced, no leading article.
MARKETS: dict[str, str] = {
    # ---- equity indices (cash indices; bar is exactly the 09:30-16:00 session)
    "nasdaq": "^NDX",
    "nasdaq 100": "^NDX",
    "s&p 500": "^GSPC",
    "s&p": "^GSPC",
    "dow": "^DJI",
    "dow jones": "^DJI",
    "russell 2000": "^RUT",
    "small caps": "^RUT",
    "vix": "^VIX",
    "volatility": "^VIX",
    # ---- sectors (RTH-traded ETFs)
    "technology": "XLK",
    "tech": "XLK",
    "semiconductors": "SMH",
    "chips": "SMH",
    "financials": "XLF",
    "banks": "XLF",
    "energy": "XLE",
    "energy stocks": "XLE",
    "healthcare": "XLV",
    "utilities": "XLU",
    "industrials": "XLI",
    "consumer discretionary": "XLY",
    "consumer staples": "XLP",
    "real estate": "XLRE",
    "materials": "XLB",
    "communication services": "XLC",
    "homebuilders": "XHB",
    "regional banks": "KRE",
    "defense": "ITA",
    "gold miners": "GDX",
    # ---- major single names
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "netflix": "NFLX",
    "jpmorgan": "JPM",
    "goldman sachs": "GS",
    "exxon": "XOM",
    "chevron": "CVX",
    "walmart": "WMT",
    "eli lilly": "LLY",
    # ---- commodities (RTH-traded proxies)
    "oil": "USO",
    "crude": "USO",
    "crude oil": "USO",
    "wti": "USO",
    "brent": "BNO",
    "brent crude oil": "BNO",     # Amendment 1: factual alias
    "wti crude oil": "USO",       # Amendment 1: factual alias
    "natural gas": "UNG",
    "gold": "GLD",
    "silver": "SLV",
    "copper": "CPER",
    "agriculture": "DBA",
    # ---- rates: YIELDS (a yield rising is the opposite of a bond rallying)
    "5-year yield": "^FVX",
    "10-year yield": "^TNX",
    "10 year yield": "^TNX",
    "treasury yields": "^TNX",
    "bond yields": "^TNX",
    "yields": "^TNX",
    "30-year yield": "^TYX",
    # ---- rates: PRICES
    "bonds": "TLT",
    "treasuries": "TLT",
    "long bonds": "TLT",
    "long-dated bonds": "TLT",    # Amendment 1: factual alias
    "treasury bonds": "TLT",
    "corporate bonds": "LQD",
    "high yield": "HYG",
    "junk bonds": "HYG",
    # ---- FX (RTH-traded currency ETFs; each tracks the named currency vs USD)
    "dollar": "DX-Y.NYB",
    "dollar index": "DX-Y.NYB",
    "us dollar": "DX-Y.NYB",
    "euro": "FXE",
    "yen": "FXY",
    "japanese yen": "FXY",
    "pound": "FXB",
    "british pound": "FXB",
    "swiss franc": "FXF",
    "canadian dollar": "FXC",
    "emerging market currencies": "CEW",
    # ---- crypto (spot ETFs, so the bar is the cash session)
    "bitcoin": "IBIT",
    "ether": "ETHA",
    "ethereum": "ETHA",
    # ---- international equity
    "europe": "VGK",
    "european stocks": "VGK",
    "japan": "EWJ",
    "japanese stocks": "EWJ",
    "china": "FXI",
    "chinese stocks": "FXI",
    "emerging markets": "EEM",
}

# ---------------------------------------------------------------- Amendment 2
# Forward-only overrides (2026-07-28). The retrospective sample keeps MARKETS
# exactly as frozen; only calls flagged prospective use these.
#
# ^NDX and ^GSPC are indices, not securities: nobody could have bought them at
# the open and sold at the close. QQQ and SPY track the same cash session at
# 98% and 96% sign agreement (r = 0.99 and 0.98), so this is a swap to a
# tradeable instrument, not a change of market.
#
# The yields are deliberately NOT swapped. The obvious proxies invert with the
# yield, and measured over this window TBX agreed with ^TNX on direction only
# 79% of the time (r = 0.63) and TBF with ^TYX 80% (r = 0.81). Replacing a
# clean measurement with a proxy that disagrees one day in five would make the
# study worse in exchange for tradeability the study does not claim (section 9).
# They stay measured on the yield and are reported as not directly tradeable.
FORWARD_OVERRIDES: dict[str, str] = {
    "^NDX": "QQQ",
    "^GSPC": "SPY",
}

# ---------------------------------------------------------------- Amendment 3
# Forward-only additions (2026-07-28). 15% of corrected retrospective calls
# named a market the frozen table did not contain, and roughly half of those
# were ordinary US-listed securities — the model twice named the ticker itself
# ("the MSOS / U.S. marijuana equity complex", "the XRT retail ETF"). Nothing
# about those is hard to price; they were simply absent from a list written
# before anyone knew what the model would say.
#
# Every ticker below was checked for >150 sessions of Yahoo history before
# being added; none was rejected. Forward only, so no existing result moves.
#
# Deliberately NOT added, because no clean US-listed cash-session equivalent
# exists and a stretched proxy is worse than an honest gap: UK gilt yields,
# Dutch TTF gas, the 2-year Treasury yield, Argentine sovereign bonds,
# Copenhagen- and Tokyo-listed single names.
FORWARD_ADDITIONS: dict[str, str] = {
    # single names
    # Full corporate names get explicit aliases rather than a generic
    # "strip the industry word" rule: stripping "technology" would resolve
    # "Micron Technology" correctly and break "technology stocks", which
    # currently reaches the sector ETF through the same cascade.
    "micron": "MU", "micron technology": "MU",
    "asml holding": "ASML", "advanced micro devices": "AMD",
    "asml": "ASML", "uber": "UBER", "doordash": "DASH",
    "moderna": "MRNA", "warner bros discovery": "WBD", "bally's": "BALY",
    "intel": "INTC", "amd": "AMD", "salesforce": "CRM", "oracle": "ORCL",
    "palantir": "PLTR", "coinbase": "COIN", "boeing": "BA", "ford": "F",
    "general motors": "GM", "disney": "DIS", "starbucks": "SBUX",
    "nike": "NKE", "pfizer": "PFE", "merck": "MRK",
    "johnson & johnson": "JNJ", "bank of america": "BAC",
    "wells fargo": "WFC", "citigroup": "C", "berkshire hathaway": "BRK-B",
    "costco": "COST", "target": "TGT", "home depot": "HD",
    "mcdonald's": "MCD", "coca-cola": "KO", "pepsi": "PEP",
    "qualcomm": "QCOM", "applied materials": "AMAT", "super micro": "SMCI",
    "dell": "DELL", "ibm": "IBM", "cisco": "CSCO", "adobe": "ADBE",
    "airbnb": "ABNB", "snowflake": "SNOW", "crowdstrike": "CRWD",
    "lyft": "LYFT",
    # thematic ETFs
    "cannabis stocks": "MSOS", "cannabis": "MSOS", "retail stocks": "XRT",
    "retail": "XRT", "biotech": "XBI", "transports": "IYT",
    "airlines": "JETS", "oil services": "OIH", "uranium": "URA",
    "clean energy": "ICLN", "infrastructure": "PAVE", "china tech": "KWEB",
    # international, via US-listed ETFs
    "nikkei": "EWJ", "nikkei 225": "EWJ", "india": "INDA",
    "indian stocks": "INDA", "uk": "EWU", "british stocks": "EWU",
    "germany": "EWG", "german stocks": "EWG", "korea": "EWY",
    "taiwan": "EWT", "brazil": "EWZ", "mexico": "EWW",
    # commodities
    "gasoline": "UGA",
}

MARKETS_FORWARD: dict[str, str] = {
    **{name: FORWARD_OVERRIDES.get(ticker, ticker)
       for name, ticker in MARKETS.items()},
    **FORWARD_ADDITIONS,
}

# Tickers that are measured but could not have been traded at those prices.
NOT_TRADEABLE = {"^TNX", "^TYX", "^FVX", "^DJI", "^RUT", "^VIX", "DX-Y.NYB",
                 "^NDX", "^GSPC"}

_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)
_SPACES = re.compile(r"\s+")

# Amendment 1: words that qualify how an instrument is traded or quoted, but do
# not change WHICH instrument it is. "nasdaq 100 futures" is the nasdaq 100.
# "u.s." needs its own branch: \b after a "." never matches, because "." is not
# a word character, so \bu\.s\.\b can never fire.
_MODIFIER = re.compile(
    r"\bu\.s\.|"
    r"\b(?:futures?|shares?|stock|stocks|index|prices?|contracts?|"
    r"front-month|treasury|us|american|"
    # corporate suffixes: "Bally's Corporation stock" is Bally's
    r"corporation|corp|inc|incorporated|ltd|limited|plc|holdings)\b", re.I
)
# Abbreviating periods survive modifier-stripping ("Warner Bros. Discovery"),
# so they get their own pass, run AFTER the u.s. branch above — that is the
# only place in a market name where a period is load-bearing.
_DOTS = re.compile(r"(?<=[a-z])\.(?=\s|$)", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)")
_INNER = re.compile(r"\(([^)]*)\)")
_SPLIT_INNER = re.compile(r"[/,]|\bor\b")


def normalise(name: str) -> str:
    """Lowercase, collapse whitespace, drop a leading article."""
    out = _SPACES.sub(" ", name.strip().lower())
    return _ARTICLE.sub("", out).strip()


def _exact(name: str, table: dict[str, str] = MARKETS) -> str | None:
    return table.get(normalise(name))


def resolve(name: str, forward: bool = False) -> str | None:
    """Return the ticker for a named market, or None if it is not in the frozen
    table. None means UNSCOREABLE — never substitute a nearby instrument.

    Amendment 1 (2026-07-28, before any price existed) added an ordered
    normalisation cascade. Every stage rewrites the NAME; no stage substitutes
    a different instrument, so the exact-never-approximate principle of section
    5 is intact.
    """
    table = MARKETS_FORWARD if forward else MARKETS

    # 1. exact
    if (t := _exact(name, table)):
        return t

    # 2. a parenthetical qualifier is MORE specific than the outer term, so it
    #    wins: "crude oil (brent)" is Brent, not WTI.
    for inner in _INNER.findall(name):
        for part in _SPLIT_INNER.split(inner):
            if (t := _exact(_MODIFIER.sub("", part).strip(), table)):
                return t

    # 3. drop parentheticals entirely
    base = _PAREN.sub("", name)
    if (t := _exact(base, table)):
        return t

    # 4. drop modifiers that do not identify the instrument
    stripped = _SPACES.sub(" ", _MODIFIER.sub("", base))
    if (t := _exact(stripped, table)):
        return t

    # 5. drop abbreviating periods ("Warner Bros. Discovery")
    if (t := _exact(_SPACES.sub(" ", _DOTS.sub("", stripped)), table)):
        return t

    return None


def resolve_exact(name: str, forward: bool = False) -> str | None:
    """Pre-Amendment-1 behaviour, kept so the original frozen rule can be
    scored alongside the amended one as a robustness check. Takes `forward`
    only so it is interchangeable with resolve() as a resolver argument."""
    return _exact(name, MARKETS_FORWARD if forward else MARKETS)
