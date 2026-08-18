r"""Record one v3 call. Section 5 of PRE_REGISTRATION_V3.md.

Writes to two places:

  calls_v2  as stream='H', so score_v2.score_stream() applies the identical
            normalisation cascade, the identical 09:30-16:00 horizon and the
            identical unscoreable rules to the human arm and the bot arm. No
            new scoring code exists for v3, by design.
  calls_v3_meta  slot, recognition flag, free note. Fields that have no home
            in calls_v2.

call_type is DERIVED here and never asked, so the rater cannot classify his own
calls inconsistently or with hindsight.

Refuses to overwrite an existing call. A rating already made cannot be revised
after the fact without destroying the point of the exercise.

Usage:
    python log_v3.py --slot 1 --market "nvidia" --direction up \
        --conviction high --horizon today --recognized no [--note "..."]
    python log_v3.py --slot 1 --direction pass --recognized no
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

from name_line_table import call_type

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
PREREG = HERE / "PRE_REGISTRATION_V3.md"

DIRECTIONS = ("up", "down", "pass")
CONVICTIONS = ("high", "low")
HORIZONS = ("today", "week", "month")
RECOGNIZED = ("yes", "maybe", "no")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True)
    ap.add_argument("--market", default="")
    ap.add_argument("--direction", required=True, choices=DIRECTIONS)
    ap.add_argument("--conviction", choices=CONVICTIONS, default="low")
    ap.add_argument("--horizon", choices=HORIZONS, default="today")
    ap.add_argument("--recognized", required=True, choices=RECOGNIZED)
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    if a.direction != "pass" and not a.market.strip():
        sys.exit("a non-pass call needs --market")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT date_et, block, answered_at FROM calls_v3_meta WHERE slot=?",
        (a.slot,)).fetchone()
    if row is None:
        sys.exit(f"slot {a.slot} is not in the pool (1..120)")
    if row["answered_at"]:
        sys.exit(f"slot {a.slot} was already logged at {row['answered_at']}. "
                 f"Ratings are not revisable.")

    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    conn.execute(
        """INSERT INTO calls_v2
           (date_et, stream, market, direction, conviction, horizon, evidence,
            prospective, input_rule, prompt_sha256, model, extracted_at)
           VALUES (?, 'H', ?, ?, ?, ?, ?, ?, 'pre_open', ?, 'human',
                   datetime('now'))""",
        (row["date_et"], a.market.strip(), a.direction, a.conviction, a.horizon,
         a.note, 1 if row["block"] == "forward" else 0, sha))
    conn.execute(
        """UPDATE calls_v3_meta SET recognized=?, note=?,
           answered_at=datetime('now') WHERE slot=?""",
        (a.recognized, a.note, a.slot))
    conn.commit()

    done, total = conn.execute(
        "SELECT COUNT(answered_at), COUNT(*) FROM calls_v3_meta").fetchone()
    ct = call_type(a.market) if a.direction != "pass" else "pass"
    print(f"slot {a.slot:03d} logged: "
          f"{a.market or '(pass)'} {a.direction} / {a.conviction} / {a.horizon}"
          f"  [{ct}]  recognized={a.recognized}")
    print(f"progress: {done} / {total}")

    # Running descriptive. Never a score: no price is fetched here (section 9.3).
    calls = conn.execute(
        """SELECT c.market, c.direction FROM calls_v2 c
           JOIN calls_v3_meta m ON m.date_et = c.date_et
           WHERE c.stream='H'""").fetchall()
    named = [c for c in calls if c["direction"] != "pass"]
    sn = [c for c in named if call_type(c["market"]) == "single_name"]
    if named:
        print(f"so far: {len(named)} calls, {len(calls) - len(named)} passes, "
              f"{len(sn)} single-name ({100 * len(sn) / len(named):.0f}%) "
              f"vs bot 6.7%")
    conn.close()


if __name__ == "__main__":
    main()
