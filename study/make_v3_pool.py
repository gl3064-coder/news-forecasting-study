r"""Draw the v3 pool and create its tables. Run once. Idempotent by refusal.

Section 3: 120 mornings, 109 sampled from the 164 retrospective pre-open
weekdays with random.Random(42), plus the 11 forward mornings, shuffled once,
order frozen in handlabel_v3_manifest.json.

The manifest maps slot -> date and is the UNBLINDING KEY. It is committed so the
draw is auditable and cannot be quietly redrawn, but the rater does not read it
until all 120 calls are logged. Same pattern as handlabel_v2_key.json.

Usage: python make_v3_pool.py [--force]
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
MANIFEST = HERE / "handlabel_v3_manifest.json"
PREREG = HERE / "PRE_REGISTRATION_V3.md"

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dt.time(9, 30)
SEED = 42
N_RETRO = 109
N_TOTAL = 120

# Section 5: the rater's five fields. slot and recognized have no home in
# calls_v2, so they live here, keyed by date, one row per morning.
META_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls_v3_meta (
    slot        INTEGER PRIMARY KEY,
    date_et     TEXT UNIQUE NOT NULL,
    block       TEXT NOT NULL,          -- 'retro' | 'forward'
    recognized  TEXT,                   -- 'yes' | 'maybe' | 'no'
    note        TEXT,
    answered_at TEXT
)
"""


def morning_days(conn: sqlite3.Connection, table: str) -> list[str]:
    """Pre-open weekdays with >= 2 newsletters, matching extract_v2.raw_days."""
    buckets: dict[str, int] = defaultdict(int)
    for d, ra in conn.execute(
        f"SELECT received_date_et, received_at_utc FROM {table}"
    ):
        try:
            arrived = dt.datetime.fromisoformat(ra).astimezone(ET)
        except (TypeError, ValueError):
            continue
        if arrived.time() >= MARKET_OPEN:
            continue
        if dt.date.fromisoformat(d).weekday() >= 5:
            continue
        buckets[d] += 1
    return sorted(d for d, n in buckets.items() if n >= 2)


def main() -> None:
    if MANIFEST.exists() and "--force" not in sys.argv:
        sys.exit(f"{MANIFEST.name} already exists. The pool is drawn. "
                 f"Re-drawing would change what has already been rated; "
                 f"pass --force only if no call has been logged.")

    conn = sqlite3.connect(DB)
    retro = morning_days(conn, "newsletters")
    fwd = morning_days(conn, "newsletters_forward")
    print(f"available: {len(retro)} retrospective, {len(fwd)} forward")

    if len(retro) < N_RETRO or len(fwd) + N_RETRO != N_TOTAL:
        sys.exit(f"pool arithmetic does not hold: {len(retro)} retro available, "
                 f"need {N_RETRO}; {len(fwd)} forward, need {N_TOTAL - N_RETRO}")

    rng = random.Random(SEED)
    picked = rng.sample(retro, N_RETRO)
    pool = [(d, "retro") for d in picked] + [(d, "forward") for d in fwd]
    rng.shuffle(pool)

    rows = [{"slot": i, "date_et": d, "block": b}
            for i, (d, b) in enumerate(pool, start=1)]

    MANIFEST.write_text(json.dumps({
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seed": SEED,
        "n_retro": N_RETRO,
        "n_forward": len(fwd),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "pool": rows,
    }, indent=2), encoding="utf-8")

    conn.execute(META_SCHEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO calls_v3_meta (slot, date_et, block) "
        "VALUES (:slot, :date_et, :block)", rows)
    conn.commit()

    # Deliberately NOT printed: which slots are forward, and the date range.
    # Both are unblinding information (section 3) and this script's output is
    # read by the rater. The manifest holds them; the manifest is not read.
    print(f"\ndrew {len(rows)} mornings, seed {SEED}")
    print(f"  retrospective {sum(1 for r in rows if r['block']=='retro')}"
          f"  forward {sum(1 for r in rows if r['block']=='forward')}")
    print(f"  slot -> date mapping withheld (section 3)")
    print(f"\nwrote {MANIFEST.name} (UNBLINDING KEY - not read until all 120 logged)")
    conn.close()


if __name__ == "__main__":
    main()
