"""Candidate replacement for Pulse's detect_tier, kept here so it can be graded
against the corpus before it goes anywhere near gmail.py.

Two changes vs the shipped version:
  1. word-boundary regexes with explicit inflections, instead of `token in text`
  2. score every tier and take the argmax, weighting subject > lead > body,
     instead of returning on the first keyword list that hits anywhere
"""
from __future__ import annotations

import re

# Each entry is one concept. Alternatives inside an entry are inflections of that
# same concept, so "war|wars|warfare" counts once, not three times.
TIER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "geopolitical": (
        r"war|wars|warfare|wartime",
        r"cease-?fires?|armistice",
        r"invasions?|invade[ds]?|invading",
        r"air ?strikes?|bombard\w+|shelling",
        r"missiles?|warheads?",
        r"troops?|soldiers?",
        r"militar(?:y|ies)|militias?",
        r"militants?|insurgen\w+",
        r"terroris(?:m|ts?)",
        r"nato",
        r"kremlin",
        r"pentagon",
        r"iran|iranians?",
        r"israel|israelis?",
        r"gaza|west bank",
        r"hamas",
        r"hezbollah",
        r"houthis?",
        r"ukraine|ukrainians?",
        r"russia|russians?",
        r"putin",
        r"china|chinese",
        r"beijing",
        r"taiwan",
        r"north korea|pyongyang",
        r"venezuela|venezuelans?",
        r"maduro",
        r"tehran",
        r"strait of hormuz",
        r"sanctions?|sanctioned|sanctioning",
        r"embargo(?:es)?",
        r"tariffs?",
        r"diplomats?|diplomatic|diplomacy",
        r"treat(?:y|ies)",
        r"ambassadors?",
        r"geopolitical|geopolitics",
        r"refugees?|asylum",
        r"coup",
        r"regimes?",
        r"elections?|electoral",
        r"voters?|ballots?",
        r"trump",
        r"biden",
        r"netanyahu",
        r"zelensky",
        r"xi jinping",
        r"white house",
        r"congress|congressional",
        r"senate|senators?",
        r"parliament|parliamentary",
        r"prime ministers?",
        r"foreign polic(?:y|ies)",
        r"nuclear",
        r"immigration|immigrants?|deportations?",
    ),
    "finance": (
        r"markets?",
        r"stocks?|shares",
        r"equit(?:y|ies)",
        r"bonds?",
        r"yields?",
        r"treasur(?:y|ies)",
        r"federal reserve|the fed|fed's|fed chair|fomc",
        r"powell|central banks?",
        r"interest rates?|rate cuts?|rate hikes?",
        r"inflation|inflationary|deflation",
        r"cpi",
        r"recessions?",
        r"gdp",
        r"unemployment|jobless|payrolls?",
        r"earnings",
        r"revenues?",
        r"profits?|profitability",
        r"ipos?",
        r"mergers?|acquisitions?|takeovers?",
        r"buyouts?",
        r"private equity",
        r"private credit",
        r"hedge funds?",
        r"venture capital",
        r"investors?|investments?|investing",
        r"portfolios?",
        r"s&p 500|s&p",
        r"nasdaq",
        r"dow jones|the dow",
        r"etfs?",
        r"dividends?",
        r"valuations?",
        r"sell-?offs?",
        r"bull market|bear market",
        r"crude|opec|oil prices?|barrels? of oil",
        r"commodit(?:y|ies)",
        r"currenc(?:y|ies)|the dollar",
        r"bitcoin|crypto|cryptocurrenc(?:y|ies)",
        r"banks?|banking",
        r"lending|lenders?|loans?",
        r"credit",
        r"debts?|deficits?",
        r"defaults?",
        r"bankrupt|bankruptc(?:y|ies)",
        r"layoffs?",
        r"wall street",
        r"shareholders?",
        r"mortgages?",
        r"housing market",
        r"retail sales|consumer spending",
        r"traders?|trading",
        r"econom(?:y|ic|ics|ist)",
    ),
    "lifestyle": (
        r"wirecutter",
        r"recipes?",
        r"cook|cooks|cooked|cooking|chefs?",
        r"restaurants?|dining",
        r"menus?",
        r"desserts?|pastr(?:y|ies)|baker(?:y|ies)",
        r"cocktails?|wines?|brunch",
        r"tast(?:e|ed|es|ing)|flavors?|delicious",
        r"styles?|stylish|fashion|wardrobes?|outfits?",
        r"sneakers?|shoes?",
        r"skincare|makeup|mascara|beauty",
        r"sports?|athletes?",
        r"nfl|nba|mlb|nhl|soccer|tennis|golf|olympics?|world cup",
        r"workouts?|fitness|gyms?|yoga|exercise",
        r"wellness|longevity|nutrition|diets?",
        r"sleep|mattress(?:es)?|pillows?|sheets",
        r"vacuums?|air purifiers?|appliances?",
        r"gifts?",
        r"travel|traveling|hotels?|vacations?|luggage",
        r"museums?|galler(?:y|ies)",
        r"movies?|films?|tv shows?",
        r"albums?|concerts?",
        r"celebrit(?:y|ies)",
        r"novels?|memoirs?|book review",
        r"puzzles?|crossword|wordle",
        r"parenting|toddlers?",
        r"pets?|dogs?",
        r"gardens?|gardening",
        r"shopping|shoppers?",
        r"kitchens?|furniture",
        r"weddings?|dating|romance",
        r"clothes|clothing|garments?|laundry",
        r"relationships?|marriage|divorce|therapy|friendships?",
    ),
}


def _compile(keywords: tuple[str, ...]) -> re.Pattern[str]:
    parts = [f"(?P<k{i}>{kw})" for i, kw in enumerate(keywords)]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


TIER_PATTERNS = {tier: _compile(kws) for tier, kws in TIER_KEYWORDS.items()}

LEAD_CHARS = 600
SUBJECT_WEIGHT = 3
LEAD_WEIGHT = 2
BODY_WEIGHT = 1
BODY_CAP = 3
TIE_ORDER = ("finance", "geopolitical", "lifestyle")


def _concepts(pattern: re.Pattern[str], text: str) -> set[str]:
    """Distinct concepts (not raw hits) the pattern finds in text."""
    return {match.lastgroup for match in pattern.finditer(text) if match.lastgroup}


def tier_scores(
    subject: str,
    content: str,
    lead_chars: int = LEAD_CHARS,
    subject_weight: int = SUBJECT_WEIGHT,
    lead_weight: int = LEAD_WEIGHT,
    body_weight: int = BODY_WEIGHT,
    body_cap: int = BODY_CAP,
) -> dict[str, int]:
    subject = subject or ""
    content = content or ""
    lowered_subject = subject.lower().strip()
    body = content.lstrip()
    if lowered_subject and body.lower().startswith(lowered_subject):
        body = body[len(subject):].lstrip(" :-|\n")
    lead, rest = body[:lead_chars], body[lead_chars:]

    scores: dict[str, int] = {}
    for tier, pattern in TIER_PATTERNS.items():
        in_subject = _concepts(pattern, subject)
        in_lead = _concepts(pattern, lead) - in_subject
        in_rest = _concepts(pattern, rest) - in_subject - in_lead
        scores[tier] = (
            subject_weight * len(in_subject)
            + lead_weight * len(in_lead)
            + body_weight * min(len(in_rest), body_cap)
        )
    return scores


def detect_tier(subject: str, content: str, **kwargs) -> str:
    margin = kwargs.pop("margin", 0)
    scores = tier_scores(subject, content, **kwargs)
    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], TIE_ORDER.index(item[0])),
    )
    (best_tier, best_score), (_, runner_up) = ranked[0], ranked[1]
    if best_score == 0 or best_score - runner_up < margin:
        return "mixed"
    return best_tier
