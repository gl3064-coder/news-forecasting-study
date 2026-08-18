r"""
Build the 20-day hand-labelling set required by PRE_REGISTRATION.md section 5.

Produces three files:
  handlabel_worksheet.md  <- READ THIS. 20 forecasts, dates stripped, no labels.
  handlabel.csv           <- FILL THIS. 60 blank rows (20 samples x 3 instruments).
  handlabel_key.json      <- DO NOT OPEN. sample_id -> date mapping, for scoring.

BLINDING, for the human as well as the model:
  - samples are identified as "Sample 1..20", never by date. You were watching
    these markets; a visible date would let you recall or look up the outcome,
    which is the same leak the prompt guards against on the model side.
  - the extractor's own labels are never shown here.
  - the three days already seen while testing the extractor (2026-05-24/25/26)
    are excluded, since they are no longer blind to you.
  - selection uses a fixed seed so the sample is reproducible and was not
    cherry-picked after the fact.

Usage: python make_handlabel_set.py
"""

from __future__ import annotations

import csv
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extract_labels import INSTRUMENTS, build_text  # noqa: E402

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
SEED = 42            # same seed as the bootstrap; fixed in advance
N_SAMPLES = 20
ALREADY_SEEN = {"2026-05-24", "2026-05-25", "2026-05-26"}

GUIDE = """\
# Hand-labelling worksheet

You are doing the extractor-validation step from `PRE_REGISTRATION.md` section 5.
The agreement rate between your labels and the model's gets reported alongside
the main result, whether it is flattering or not.

## What to do

For each sample below, decide what direction the commentary **predicts for that
session** for each of three instruments, and write it in `handlabel.csv`:

| instrument | what it is |
|---|---|
| `NQ`  | Nasdaq 100 futures |
| `CL`  | crude oil |
| `TNX` | the 10-year Treasury **yield** |

Write exactly one of `up`, `down`, or `no_call` per cell.

## The rules you are applying

These are the same rules the model was given. Apply them the same way.

1. **Record the stated view, not your own.** If the reasoning looks wrong to
   you, ignore that. You are transcribing, not forecasting.
2. **Hedging does not erase direction.** "cautiously bearish" is `down`.
   "cautious short-to-neutral" is `down` (there is a short lean).
   "neutral-to-cautiously-long" is `up`. "short or flat" is `down`.
   Use `no_call` only when there is genuinely no lean either way.
3. **A mention is not a forecast.** Only label an instrument if the text
   predicts *that instrument's* move. "oil above $100 is pressuring tech"
   is `down` for NQ and `no_call` for CL, because it says where oil *is*,
   not where it is *going*.
4. **TNX is a yield, so watch the sign.** "yields rise" is `up`.
   "bonds rally" or "flight to safety into Treasuries" is `down`.
   "rate hike risk growing" is `up`.
5. **A bare scenario is not a call.** "If Hormuz closes, oil spikes" alone is
   `no_call` for CL. But a stated bias plus a condition is still a call:
   "cautiously bearish unless TNX holds below 4.65%" is `down` for NQ.
6. **Use only the text in front of you.** Do not look anything up. If you think
   you recognise the day, ignore that completely.

Dates have been stripped and replaced with `[DATE]`. Samples are deliberately
not in date order.

---

"""


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        WITH chosen AS (
            SELECT date_et, nq_game_plan, bull_case, bear_case,
                   watch_today, why_markets_move,
                   ROW_NUMBER() OVER (
                       PARTITION BY date_et ORDER BY generated_at_utc DESC
                   ) AS rn
            FROM forecasts WHERE pre_open = 1
        )
        SELECT * FROM chosen WHERE rn = 1 ORDER BY date_et
        """
    ).fetchall()

    eligible = [r for r in rows if r["date_et"] not in ALREADY_SEEN]
    print(f"pre-open days:        {len(rows)}")
    print(f"excluded (already seen): {len(rows) - len(eligible)}")
    print(f"eligible:             {len(eligible)}")

    if len(eligible) < N_SAMPLES:
        sys.exit(f"only {len(eligible)} eligible days, need {N_SAMPLES}")

    rng = random.Random(SEED)
    picked = rng.sample(eligible, N_SAMPLES)
    rng.shuffle(picked)  # break date ordering so sequence carries no signal

    # ---- worksheet (what he reads) -------------------------------------
    parts = [GUIDE]
    for i, row in enumerate(picked, 1):
        parts.append(f"## Sample {i}\n\n{build_text(row)}\n\n---\n")
    (HERE / "handlabel_worksheet.md").write_text("\n".join(parts), encoding="utf-8")

    # ---- csv (what he fills) -------------------------------------------
    with open(HERE / "handlabel.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "instrument", "my_label"])
        for i in range(1, N_SAMPLES + 1):
            for inst in INSTRUMENTS:
                w.writerow([i, inst, ""])

    # ---- key (scoring only; he does not open this) ----------------------
    (HERE / "handlabel_key.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "excluded_already_seen": sorted(ALREADY_SEEN),
                "mapping": {str(i): r["date_et"] for i, r in enumerate(picked, 1)},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nwrote handlabel_worksheet.md  ({N_SAMPLES} samples to read)")
    print(f"wrote handlabel.csv           ({N_SAMPLES * len(INSTRUMENTS)} rows to fill)")
    print("wrote handlabel_key.json      (do not open — scoring uses it)")
    conn.close()


if __name__ == "__main__":
    main()
