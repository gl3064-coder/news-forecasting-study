"""Show that the new patterns fix substring matching WITHOUT the naive \\btoken\\b
fix's opposite failure: silently dropping legitimate plurals like "sanctions".

    naive substring   what the shipped code does  ("war" in "forwarded" -> hit)
    bare \\btoken\\b    the tempting one-line fix   (misses "sanctions")
    inflected         what the new detect_tier does
"""
from __future__ import annotations

import re
import sqlite3

import tier_candidate

CONCEPTS = {
    "war": r"war|wars|warfare|wartime",
    "nato": r"nato",
    "sanction": r"sanctions?|sanctioned|sanctioning",
    "election": r"elections?|electoral",
    "russia": r"russia|russians?",
    "china": r"china|chinese",
    "stock": r"stocks?|shares",
    "rate": r"interest rates?|rate cuts?|rate hikes?",
}

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
texts = [
    f"{r['subject']} {r['body']}".lower()
    for r in conn.execute("SELECT subject, body FROM newsletters")
]

print(f"{'token':<10} {'substring':>10} {'bare \\b':>10} {'inflected':>10}")
for token, inflected in CONCEPTS.items():
    bare = re.compile(rf"\b{token}\b", re.IGNORECASE)
    full = re.compile(rf"\b(?:{inflected})\b", re.IGNORECASE)
    n_sub = sum(1 for t in texts if token in t)
    n_bare = sum(1 for t in texts if bare.search(t))
    n_full = sum(1 for t in texts if full.search(t))
    print(f"{token:<10} {n_sub:>10,} {n_bare:>10,} {n_full:>10,}")

print("\nfalse friends that no longer match (was: substring hit):")
for token, decoys in {
    "war": ["forwarded", "toward", "warm", "software", "warren"],
    "nato": ["senator", "coordinator", "designator"],
    "iran": ["tyrannical"],
}.items():
    inflected = CONCEPTS.get(token, token)
    full = re.compile(rf"\b(?:{inflected})\b", re.IGNORECASE)
    for decoy in decoys:
        status = "STILL MATCHES" if full.search(decoy) else "ok"
        print(f"  {token:<6} in {decoy:<12} {status}")

print("\nsanity: 'Linen sheets' newsletter now scores")
row = conn.execute(
    "SELECT subject, body FROM newsletters WHERE subject LIKE 'Linen sheets%' LIMIT 1"
).fetchone()
print("  subject:", row["subject"])
print("  scores :", tier_candidate.tier_scores(row["subject"], row["body"]))
print("  tier   :", tier_candidate.detect_tier(row["subject"], row["body"]))
