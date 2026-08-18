r"""
Compare your hand labels against the extractor's, per PRE_REGISTRATION.md
section 5. Run this after filling in handlabel.csv.

Reports raw agreement AND Cohen's kappa. Kappa matters here: if most cells are
`no_call`, two raters who both default to `no_call` will show high raw agreement
while demonstrating nothing. Kappa corrects for agreement expected by chance.

No price data is involved, so this can be run before unblinding.

Usage: python score_agreement.py
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
LABELS = ("up", "down", "no_call")


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """Chance-corrected agreement. 1.0 = perfect, 0.0 = no better than chance."""
    n = len(pairs)
    if n == 0:
        return float("nan")
    observed = sum(a == b for a, b in pairs) / n
    mine = Counter(a for a, _ in pairs)
    theirs = Counter(b for _, b in pairs)
    expected = sum(
        (mine[l] / n) * (theirs[l] / n) for l in set(mine) | set(theirs)
    )
    if expected == 1.0:
        return float("nan")  # both raters used a single label throughout
    return (observed - expected) / (1 - expected)


def main() -> None:
    key_path = HERE / "handlabel_key.json"
    csv_path = HERE / "handlabel.csv"
    for p in (key_path, csv_path):
        if not p.exists():
            sys.exit(f"missing {p.name} — run make_handlabel_set.py first")

    key = json.loads(key_path.read_text(encoding="utf-8"))
    mapping = key["mapping"]

    mine: dict[tuple[str, str], str] = {}
    blank = 0
    bad: list[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            val = (row["my_label"] or "").strip().lower()
            if not val:
                blank += 1
                continue
            if val not in LABELS:
                bad.append(f"sample {row['sample_id']} {row['instrument']}: {val!r}")
                continue
            date = mapping.get(str(row["sample_id"]).strip())
            if date:
                mine[(date, row["instrument"].strip().upper())] = val

    if bad:
        print("invalid entries (use up / down / no_call):")
        for b in bad:
            print(f"  {b}")
        print()
    if blank:
        print(f"{blank} cells still blank — fill handlabel.csv before scoring\n")
    if not mine:
        sys.exit("nothing to score")

    conn = sqlite3.connect(HERE / "news_corpus.db")
    conn.row_factory = sqlite3.Row
    theirs = {
        (r["date_et"], r["instrument"]): r["label"]
        for r in conn.execute("SELECT date_et, instrument, label FROM labels")
    }
    conn.close()

    pairs: list[tuple[str, str]] = []
    by_inst: dict[str, list[tuple[str, str]]] = defaultdict(list)
    confusion: Counter = Counter()
    missing = 0
    for (date, inst), my in sorted(mine.items()):
        their = theirs.get((date, inst))
        if their is None:
            missing += 1
            continue
        pairs.append((my, their))
        by_inst[inst].append((my, their))
        confusion[(my, their)] += 1

    if missing:
        print(f"{missing} cells have no extractor label yet (extraction still running?)\n")
    if not pairs:
        sys.exit("no overlapping cells to compare")

    agree = sum(a == b for a, b in pairs)
    print("=" * 58)
    print("EXTRACTOR VALIDATION")
    print("=" * 58)
    print(f"  cells compared     {len(pairs)}")
    print(f"  agreed             {agree}  ({agree / len(pairs) * 100:.0f}%)")
    print(f"  Cohen's kappa      {cohens_kappa(pairs):.2f}")

    print("\n  by instrument")
    print(f"    {'inst':<6}{'n':>4}{'agree':>8}{'kappa':>8}")
    for inst in sorted(by_inst):
        pr = by_inst[inst]
        a = sum(x == y for x, y in pr)
        k = cohens_kappa(pr)
        kt = "  n/a" if k != k else f"{k:>8.2f}"
        print(f"    {inst:<6}{len(pr):>4}{a / len(pr) * 100:>7.0f}%{kt}")

    print("\n  confusion (rows = yours, cols = extractor)")
    print(f"    {'':<9}" + "".join(f"{l:>9}" for l in LABELS))
    for my in LABELS:
        cells = "".join(f"{confusion[(my, t)]:>9}" for t in LABELS)
        print(f"    {my:<9}{cells}")

    print("\n  disagreements")
    any_dis = False
    for (date, inst), my in sorted(mine.items()):
        their = theirs.get((date, inst))
        if their is not None and their != my:
            any_dis = True
            print(f"    {date}  {inst:<4} you={my:<8} extractor={their}")
    if not any_dis:
        print("    none")

    print("\n  Interpretation: kappa above ~0.6 is usually called substantial")
    print("  agreement, above ~0.8 near-perfect. Report whatever you get.")


if __name__ == "__main__":
    main()
