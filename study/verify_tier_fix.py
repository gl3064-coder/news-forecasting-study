r"""Independent verification of the detect_tier fix.

The `tier` column in news_corpus.db was written by the OLD classifier at pull
time, so this re-runs the CURRENT Pulse detect_tier over the same 2,049 stored
bodies and diffs the two distributions. Nothing is taken on trust.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PULSE_BACKEND = Path(r"C:\Users\lgavi\OneDrive\Desktop\Pulse\Pulse\backend")
sys.path.insert(0, str(PULSE_BACKEND))
from app.services.gmail import detect_tier  # noqa: E402

os.environ["GMAIL_CREDENTIALS_FILE"] = str(PULSE_BACKEND / "credentials.json")
os.environ["GMAIL_TOKEN_FILE"] = str(PULSE_BACKEND / "token.json")

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT subject, body, tier FROM newsletters").fetchall()

old = Counter()
new = Counter()
moves = Counter()
for r in rows:
    t_old = r["tier"]
    t_new = detect_tier(r["subject"], r["body"])
    old[t_old] += 1
    new[t_new] += 1
    if t_old != t_new:
        moves[(t_old, t_new)] += 1

n = len(rows)
print(f"newsletters re-classified: {n:,}\n")
tiers = sorted(set(old) | set(new))
print(f"{'tier':<16}{'OLD':>8}{'':>4}{'NEW':>8}{'':>4}{'change':>9}")
for t in tiers:
    print(f"{t:<16}{old[t]:>8}{'':>4}{new[t]:>8}{'':>4}{new[t] - old[t]:>+9}")

print(f"\nrows whose label changed: {sum(moves.values()):,} "
      f"({sum(moves.values()) / n * 100:.0f}%)")
print("\nlargest moves:")
for (a, b), c in moves.most_common(8):
    print(f"  {a:<14} -> {b:<14} {c:>5}")

# The specific false positives that proved the bug.
print("\nthe messages that proved the bug ('war' inside 'forwarded', etc.):")
probes = [
    "Linen sheets that don%t cost a fortune",
    "Ideas for a screen-free summer",
    "The best luggage for checking",
]
for probe in probes:
    like = probe.replace("%t", "_t")
    row = conn.execute(
        "SELECT subject, body, tier FROM newsletters WHERE subject LIKE ? LIMIT 1",
        (f"{like[:20]}%",),
    ).fetchone()
    if not row:
        print(f"  (not found: {probe[:40]})")
        continue
    t_new = detect_tier(row["subject"], row["body"])
    verdict = "FIXED" if t_new != "geopolitical" else "STILL WRONG"
    print(f"  [{verdict}] {row['subject'][:46]!r}")
    print(f"           old={row['tier']}  new={t_new}")

# Does 'finance' now actually surface market newsletters? Spot-check titles.
print("\nsample of newsletters NOW tagged finance:")
shown = 0
for r in rows:
    if shown >= 8:
        break
    if detect_tier(r["subject"], r["body"]) == "finance":
        print(f"  {r['subject'][:66]}")
        shown += 1
if shown == 0:
    print("  none")
