"""Curated lists of sectors and concepts the dossier system recognizes.

Edit one place to add or rename. Claude's entity-extraction prompt names
these explicitly, so do NOT let it invent novel sectors/concepts that would
fragment the taxonomy.
"""

from __future__ import annotations

# Sectors: broad industry buckets. Key is the canonical slug.
SECTORS: dict[str, dict[str, str]] = {
    "banks":      {"name": "Banks",      "blurb": "US large-cap banks, regional banks, and insurers."},
    "ai_chips":   {"name": "AI / Chips", "blurb": "Semiconductor designers and AI accelerator demand."},
    "energy":     {"name": "Energy",     "blurb": "Oil, gas, integrateds, and energy infrastructure."},
    "defense":    {"name": "Defense",    "blurb": "Prime contractors, supply chains, and geopolitical demand."},
    "crypto":     {"name": "Crypto",     "blurb": "Bitcoin, ETH, stablecoins, regulatory developments."},
    "macro":      {"name": "Macro",      "blurb": "Fed policy, rates, inflation, currency, growth data."},
}

# Concepts: finance terms the user encounters in coursework + reading.
CONCEPTS: dict[str, dict[str, str]] = {
    "dcf":             {"name": "DCF",              "blurb": "Discounted Cash Flow valuation."},
    "duration":        {"name": "Duration",         "blurb": "Bond/equity price sensitivity to interest rates."},
    "yield_curve":     {"name": "Yield Curve",      "blurb": "Term structure of US Treasury yields."},
    "m_and_a":         {"name": "M&A",              "blurb": "Mergers and acquisitions activity."},
    "ipo":             {"name": "IPO",              "blurb": "Initial public offerings."},
    "fed_independence":{"name": "Fed Independence", "blurb": "Political pressure on the Federal Reserve's mandate."},
}

# Auto-followed on first run so the Dossiers tab isn't empty.
# Tuple format: (kind, key, display_name)
SEED_FOLLOWS: list[tuple[str, str, str]] = [
    ("company", "NVDA", "Nvidia"),
    ("company", "JPM",  "JPMorgan Chase"),
    ("company", "AAPL", "Apple"),
    ("sector",  "ai_chips", "AI / Chips"),
    ("concept", "duration", "Duration"),
]


def is_known_sector(key: str) -> bool:
    return key in SECTORS


def is_known_concept(key: str) -> bool:
    return key in CONCEPTS
