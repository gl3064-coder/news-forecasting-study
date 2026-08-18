r"""Which instruments do the forecasts actually make claims about, and do those
claims look directional or merely mentioned?

Only looks at the pre-open (08:00 ET) rows, since those are the only unbiased
ones for a same-session test.
"""

import re
import sqlite3
from collections import Counter

INSTRUMENTS = {
    "NQ / nasdaq":  r"\b(nq|nasdaq|qqq)\b",
    "oil / crude":  r"\b(oil|crude|wti|brent)\b",
    "gold":         r"\bgold\b",
    "S&P / ES":     r"\b(s&p|spx|es futures|sp500|s and p)\b",
    "treasuries":   r"\b(treasur\w*|2-year|10-year|yield curve|bonds?)\b",
    "dollar / FX":  r"\b(dollar|dxy|euro|yen|currenc\w+)\b",
    "bitcoin":      r"\b(bitcoin|btc|crypto)\b",
    "rates / Fed":  r"\b(fed|fomc|rate hike|rate cut|powell)\b",
}

# words that turn a mention into a directional claim
DIRECTIONAL = re.compile(
    r"\b(bullish|bearish|long|short|upside|downside|rally|rallies|fade|"
    r"break(?:s|out|down)?|higher|lower|rise|rises|fall|falls|drop|drops|"
    r"spike|spikes|squeeze|reverse|reverses|support|resistance|target)\b",
    re.IGNORECASE,
)

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT date_et, bull_case, bear_case, nq_game_plan, watch_today,
              why_markets_move
       FROM forecasts WHERE pre_open = 1 ORDER BY date_et"""
).fetchall()

print(f"pre-open forecasts examined: {len(rows)}\n")

mention_days = Counter()
directional_days = Counter()

for r in rows:
    blob = " ".join(
        str(r[f] or "")
        for f in ("bull_case", "bear_case", "nq_game_plan", "watch_today",
                  "why_markets_move")
    )
    lower = blob.lower()
    for name, pat in INSTRUMENTS.items():
        hits = list(re.finditer(pat, lower))
        if not hits:
            continue
        mention_days[name] += 1
        # directional if a direction word sits within ~120 chars of a mention
        near = False
        for m in hits:
            window = lower[max(0, m.start() - 120): m.end() + 120]
            if DIRECTIONAL.search(window):
                near = True
                break
        if near:
            directional_days[name] += 1

n = len(rows)
print(f"{'instrument':<14}{'mentioned':>11}{'directional':>13}{'usable %':>10}")
for name in INSTRUMENTS:
    m = mention_days[name]
    d = directional_days[name]
    print(f"{name:<14}{m:>7} /{n:>3}{d:>9} /{n:>3}{d / n * 100:>9.0f}%")

# how many instruments carry a directional claim on a typical day
per_day = []
for r in rows:
    blob = " ".join(
        str(r[f] or "")
        for f in ("bull_case", "bear_case", "nq_game_plan", "watch_today",
                  "why_markets_move")
    ).lower()
    c = 0
    for pat in INSTRUMENTS.values():
        hits = list(re.finditer(pat, blob))
        if hits and any(
            DIRECTIONAL.search(blob[max(0, m.start() - 120): m.end() + 120])
            for m in hits
        ):
            c += 1
    per_day.append(c)

print("\ndirectional instruments per day:")
for k, v in sorted(Counter(per_day).items()):
    print(f"  {k} instruments   {v:>3} days")
print(f"\nmean per day: {sum(per_day) / len(per_day):.1f}")
print(f"total instrument-days available: {sum(per_day)}")
