r"""Summarize the extracted labels, and compute the power the primary test
actually has given the observed label balance. No price data involved."""

import sqlite3
from collections import Counter

import numpy as np

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT date_et, instrument, label FROM labels").fetchall()
days = len({r["date_et"] for r in rows})

print(f"days labelled: {days}\n")
print(f"{'inst':<6}{'up':>6}{'down':>6}{'no_call':>9}{'usable':>8}{'coverage':>10}")
by_inst: dict[str, Counter] = {}
for inst in ("NQ", "CL", "TNX"):
    c = Counter(r["label"] for r in rows if r["instrument"] == inst)
    by_inst[inst] = c
    usable = c["up"] + c["down"]
    print(f"{inst:<6}{c['up']:>6}{c['down']:>6}{c['no_call']:>9}{usable:>8}"
          f"{usable / days * 100:>9.0f}%")

pooled = Counter(r["label"] for r in rows)
tot_usable = pooled["up"] + pooled["down"]
print(f"\npooled usable calls: {tot_usable}  "
      f"(up {pooled['up']}, down {pooled['down']})")

# --- what the smaller bucket does to the primary test -------------------
print("\n" + "=" * 62)
print("POWER, given the labels actually produced")
print("  Primary statistic is P(up | said up) - P(up | said down).")
print("  Its precision is set by the SMALLER of the two buckets.")
print("=" * 62)


def two_se(n1: int, n2: int) -> float:
    """2 standard errors of a difference in two proportions, worst case p=.5."""
    if n1 == 0 or n2 == 0:
        return float("inf")
    return 2 * np.sqrt(0.25 / n1 + 0.25 / n2)


print(f"\n  {'basis':<26}{'n_up':>6}{'n_down':>8}{'detectable gap':>17}")
for inst in ("NQ", "CL", "TNX"):
    c = by_inst[inst]
    gap = two_se(c["up"], c["down"])
    g = "  not testable" if gap == float("inf") else f"{gap * 100:>15.0f}pp"
    print(f"  {inst:<26}{c['up']:>6}{c['down']:>8}{g}")

gap = two_se(pooled["up"], pooled["down"])
print(f"  {'pooled (all three)':<26}{pooled['up']:>6}{pooled['down']:>8}"
      f"{gap * 100:>15.0f}pp")

print("\n  Reference: a real directional edge shows up as a gap of maybe")
print("  10-20pp. Anything above that is not a realistic effect size.")
conn.close()
