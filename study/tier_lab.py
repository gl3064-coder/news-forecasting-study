"""Harness for measuring detect_tier against the 2,049-message corpus.

Loads the live detect_tier straight out of Pulse's gmail.py (module-level
constants + plain functions only, so none of the google/dotenv imports run),
so what gets measured here is exactly what ships.

Usage:
    python tier_lab.py              # distribution of the live detect_tier
    python tier_lab.py --baseline   # distribution of the old first-match version
"""
from __future__ import annotations

import argparse
import ast
import base64
import collections
import os
import re
import sqlite3
import sys
from pathlib import Path

GMAIL_PY = Path(
    r"C:\Users\lgavi\OneDrive\Desktop\Pulse\Pulse\backend\app\services\gmail.py"
)
CORPUS_DB = Path(__file__).with_name("news_corpus.db")

TIERS = ["geopolitical", "finance", "lifestyle", "mixed"]


def load_gmail_namespace() -> dict:
    """Exec gmail.py's constants and pure functions in an isolated namespace."""
    tree = ast.parse(GMAIL_PY.read_text(encoding="utf-8"))
    keep: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef):
            keep.append(node)
    module = ast.Module(body=keep, type_ignores=[])
    namespace = {
        "re": re,
        "os": os,
        "base64": base64,
        "Path": Path,
        "__name__": "gmail_extract",
        "__file__": str(GMAIL_PY),
    }
    exec(compile(module, str(GMAIL_PY), "exec"), namespace)
    return namespace


def baseline_detect_tier(subject: str, content: str) -> str:
    """The pre-fix implementation, kept verbatim for before/after comparison."""
    text = f"{subject} {content}".lower()
    if any(token in text for token in ["war", "iran", "china", "russia", "sanction", "election", "trump", "nato"]):
        return "geopolitical"
    if any(token in text for token in ["markets", "stocks", "fed", "inflation", "rates", "oil", "earnings", "economy"]):
        return "finance"
    if any(token in text for token in ["style", "sports", "culture", "travel", "book review", "recipes", "wirecutter"]):
        return "lifestyle"
    return "mixed"


def corpus_rows() -> list[sqlite3.Row]:
    conn = sqlite3.connect(CORPUS_DB)
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT gmail_message_id, source, subject, body FROM newsletters"
    ).fetchall()


def distribution(fn, rows) -> collections.Counter:
    return collections.Counter(fn(r["subject"], r["body"]) for r in rows)


def print_distribution(label: str, counts: collections.Counter, total: int) -> None:
    print(f"\n{label}")
    for tier in TIERS:
        n = counts.get(tier, 0)
        print(f"  {tier:<13} {n:>5,}  {n / total:6.1%}")
    extra = set(counts) - set(TIERS)
    for tier in sorted(extra):
        print(f"  {tier:<13} {counts[tier]:>5,}   <-- UNEXPECTED tier value")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--samples", type=int, default=0,
                        help="print N example classifications per tier")
    args = parser.parse_args()

    rows = corpus_rows()
    total = len(rows)
    print(f"corpus: {total:,} messages from {CORPUS_DB.name}")

    if args.baseline:
        counts = distribution(baseline_detect_tier, rows)
        print_distribution("baseline detect_tier (first-match, substring)", counts, total)
        return 0

    ns = load_gmail_namespace()
    detect_tier = ns["detect_tier"]
    counts = distribution(detect_tier, rows)
    print_distribution("live detect_tier (from gmail.py)", counts, total)

    if args.samples:
        by_tier: dict[str, list[str]] = collections.defaultdict(list)
        for r in rows:
            by_tier[detect_tier(r["subject"], r["body"])].append(r["subject"])
        for tier in TIERS:
            print(f"\n  --- {tier} samples ---")
            for subject in by_tier[tier][: args.samples]:
                print(f"    {subject[:88]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
