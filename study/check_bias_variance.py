r"""Feasibility check BEFORE building the extractor: does the digest's stated
bias actually vary?

A forecaster whose prediction never changes cannot beat a base rate, by
construction. If 90% of days say "cautiously bearish" then there is nothing to
score. This looks only at PREDICTIONS, never at outcomes, so it cannot
contaminate anything (no price data exists yet).
"""

import re
import sqlite3
from collections import Counter

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT date_et, nq_game_plan FROM forecasts
       WHERE pre_open=1 AND nq_game_plan IS NOT NULL ORDER BY date_et"""
).fetchall()

# grab the first sentence after the bias marker, which is where the call lives
BIAS_LINE = re.compile(
    r"(?:directional\s+)?bias[^.:]*[:\-]\s*(.{0,110})", re.IGNORECASE | re.DOTALL
)

BEAR = re.compile(r"\b(bearish|short|down|negative|defensive|fade)\b", re.I)
BULL = re.compile(r"\b(bullish|long|up|positive|constructive)\b", re.I)
NEUTRAL = re.compile(r"\b(neutral|balanced|mixed|sidelines|wait|flat|range)\b", re.I)
HEDGE = re.compile(r"\b(caution\w*|slight\w*|modest\w*|mild\w*|leaning|tilt\w*)\b", re.I)

buckets = Counter()
hedged = 0
found = 0
snippets = []

for r in rows:
    m = BIAS_LINE.search(r["nq_game_plan"])
    if not m:
        buckets["no bias line"] += 1
        continue
    found += 1
    frag = re.sub(r"\s+", " ", m.group(1)).strip()
    snippets.append(frag[:70])
    if HEDGE.search(frag):
        hedged += 1
    b, u, n = bool(BEAR.search(frag)), bool(BULL.search(frag)), bool(NEUTRAL.search(frag))
    if b and not u:
        buckets["bearish-leaning"] += 1
    elif u and not b:
        buckets["bullish-leaning"] += 1
    elif b and u:
        buckets["both words present"] += 1
    elif n:
        buckets["neutral only"] += 1
    else:
        buckets["unclear"] += 1

print(f"pre-open forecasts with an nq_game_plan: {len(rows)}")
print(f"of those, a parseable bias line:         {found}\n")
for k, v in buckets.most_common():
    pct = v / len(rows) * 100
    print(f"  {k:<22} {v:>3}  ({pct:>4.0f}%)  {'#' * v}")

print(f"\nhedged wording ('cautiously', 'leaning', 'slight'): "
      f"{hedged}/{found} ({hedged / max(found,1) * 100:.0f}%)")

print("\nevery bias line, in date order:")
for r, s in zip([r for r in rows if BIAS_LINE.search(r["nq_game_plan"])], snippets):
    print(f"  {r['date_et']}  {s}")
