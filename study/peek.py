"""Spot-check corpus text quality. Usage: python peek.py [n_rows]"""
import sqlite3
import sys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT source, received_date_et, signal, tier, subject, n_chars, body
       FROM newsletters WHERE signal='high' ORDER BY n_chars DESC LIMIT ?""",
    (n,),
).fetchall()
for r in rows:
    print("=" * 70)
    print(f"{r['source']} | {r['received_date_et']} | {r['signal']}/{r['tier']} "
          f"| {r['n_chars']:,} chars")
    print(f"SUBJECT: {r['subject']}")
    print("-" * 70)
    print(r["body"][:1200])
    print()
