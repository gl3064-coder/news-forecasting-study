"""Verify whether detect_tier's 'geopolitical' bucket is firing on substrings
rather than real geopolitical content."""
import re
import sqlite3

conn = sqlite3.connect("news_corpus.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT subject, body FROM newsletters").fetchall()

TOKENS = ["war", "iran", "china", "russia", "sanction", "election", "trump", "nato"]

substring_only = 0
real_hit = 0
examples = []

for r in rows:
    text = f"{r['subject']} {r['body']}".lower()
    # what Pulse actually does: naive substring test
    naive = [t for t in TOKENS if t in text]
    # what it presumably meant: whole-word match
    whole = [t for t in TOKENS if re.search(rf"\b{t}\b", text)]
    if naive and not whole:
        substring_only += 1
        if len(examples) < 3:
            culprits = {}
            for t in naive:
                m = re.findall(rf"\w*{t}\w*", text)
                culprits[t] = sorted(set(m))[:5]
            examples.append((r["subject"][:60], culprits))
    elif whole:
        real_hit += 1

print(f"total messages                     {len(rows):,}")
print(f"tagged geopolitical on WHOLE words {real_hit:,}")
print(f"tagged ONLY via substring match    {substring_only:,}")

print("\nWhich tokens are substring-matching, across the whole corpus:")
for t in TOKENS:
    n_naive = sum(1 for r in rows if t in f"{r['subject']} {r['body']}".lower())
    n_whole = sum(
        1 for r in rows
        if re.search(rf"\b{t}\b", f"{r['subject']} {r['body']}".lower())
    )
    flag = "  <-- inflated" if n_naive > n_whole * 1.2 else ""
    print(f"  {t:<9} substring {n_naive:>5,}   whole-word {n_whole:>5,}{flag}")

if examples:
    print("\nExamples where only substrings matched:")
    for subj, culprits in examples:
        print(f"  {subj!r}")
        for t, words in culprits.items():
            print(f"      '{t}' matched: {words}")
