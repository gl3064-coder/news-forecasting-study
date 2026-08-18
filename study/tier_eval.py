"""Grade a detect_tier implementation against the hand-labeled set and report
the full-corpus distribution it produces.

    python tier_eval.py                 # candidate vs baseline
    python tier_eval.py --live          # whatever gmail.py currently ships
    python tier_eval.py --grid          # sweep the scoring knobs
    python tier_eval.py --errors        # candidate's misses, with subjects
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

import tier_candidate
from tier_labels import LABELS
from tier_lab import TIERS, baseline_detect_tier, corpus_rows, load_gmail_namespace


def accuracy(fn, rows_by_id) -> tuple[float, collections.Counter, dict]:
    confusion: collections.Counter = collections.Counter()
    misses: dict[str, tuple[str, str, str]] = {}
    correct = 0
    for msg_id, gold in LABELS.items():
        row = rows_by_id[msg_id]
        got = fn(row["subject"], row["body"])
        confusion[(gold, got)] += 1
        if got == gold:
            correct += 1
        else:
            misses[msg_id] = (gold, got, row["subject"])
    return correct / len(LABELS), confusion, misses


def per_tier(confusion: collections.Counter) -> list[str]:
    lines = []
    for tier in TIERS:
        tp = confusion[(tier, tier)]
        gold_n = sum(v for (g, _), v in confusion.items() if g == tier)
        pred_n = sum(v for (_, p), v in confusion.items() if p == tier)
        recall = tp / gold_n if gold_n else 0.0
        precision = tp / pred_n if pred_n else 0.0
        lines.append(
            f"    {tier:<13} precision {precision:5.0%} ({tp}/{pred_n})"
            f"   recall {recall:5.0%} ({tp}/{gold_n})"
        )
    return lines


def report(name: str, fn, rows, rows_by_id) -> None:
    acc, confusion, _ = accuracy(fn, rows_by_id)
    counts = collections.Counter(fn(r["subject"], r["body"]) for r in rows)
    total = len(rows)
    print(f"\n{name}")
    print(f"  labeled accuracy: {acc:.0%} ({round(acc * len(LABELS))}/{len(LABELS)})")
    for line in per_tier(confusion):
        print(line)
    print("  full-corpus distribution:")
    for tier in TIERS:
        n = counts.get(tier, 0)
        print(f"    {tier:<13} {n:>5,}  {n / total:6.1%}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--errors", action="store_true")
    args = parser.parse_args()

    rows = corpus_rows()
    rows_by_id = {r["gmail_message_id"]: r for r in rows}
    missing = set(LABELS) - set(rows_by_id)
    if missing:
        print(f"labels reference {len(missing)} ids not in the corpus", file=sys.stderr)
        return 1

    if args.grid:
        print("lead  subj lead body cap margin   acc   geo%  fin%  life% mixed%")
        for lead_chars in (300, 600, 1000):
            for weights in ((3, 2, 1), (4, 2, 1), (3, 1, 1)):
                for margin in (0, 2):
                    kw = dict(
                        lead_chars=lead_chars,
                        subject_weight=weights[0],
                        lead_weight=weights[1],
                        body_weight=weights[2],
                        margin=margin,
                    )
                    fn = lambda s, c, kw=kw: tier_candidate.detect_tier(s, c, **kw)
                    acc, _, _ = accuracy(fn, rows_by_id)
                    counts = collections.Counter(fn(r["subject"], r["body"]) for r in rows)
                    pct = [counts.get(t, 0) / len(rows) for t in TIERS]
                    print(
                        f"{lead_chars:>4}  {weights[0]:>4} {weights[1]:>4} {weights[2]:>4} "
                        f"{tier_candidate.BODY_CAP:>3} {margin:>6}  {acc:5.0%}  "
                        + "  ".join(f"{p:4.0%}" for p in pct)
                    )
        return 0

    if args.errors:
        _, _, misses = accuracy(tier_candidate.detect_tier, rows_by_id)
        print(f"candidate misses ({len(misses)}):")
        for msg_id, (gold, got, subject) in misses.items():
            scores = tier_candidate.tier_scores(
                rows_by_id[msg_id]["subject"], rows_by_id[msg_id]["body"]
            )
            print(f"  want {gold:<13} got {got:<13} {subject[:66]}")
            print(f"      scores {scores}")
        return 0

    report("baseline (shipped before the fix)", baseline_detect_tier, rows, rows_by_id)
    if args.live:
        report("live gmail.py", load_gmail_namespace()["detect_tier"], rows, rows_by_id)
    report("candidate (word-boundary + argmax)", tier_candidate.detect_tier, rows, rows_by_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
