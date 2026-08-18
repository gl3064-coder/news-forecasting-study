r"""
Load the v7 overarching-analysis rows dumped from the Pulse droplet into the
local corpus DB, alongside the newsletter text.

Input:  forecasts_raw.jsonl  (produced by the read-only ssh dump)
Output: table `forecasts` in news_corpus.db

The point of putting these in the SAME database as the newsletters is that
Pulse's 7-day purge destroyed the news behind older analyses, but the Gmail
corpus still has it. Joining on date_et reconstructs what each forecast was
built from.

Usage: python load_forecasts.py
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
RAW = HERE / "forecasts_raw.jsonl"
DB = HERE / "news_corpus.db"
ET = ZoneInfo("America/New_York")

TEXT_FIELDS = [
    "tldr",
    "what_happened",
    "why_markets_move",
    "watch_today",
    "bull_case",
    "bear_case",
    "nq_game_plan",
    "stern_angle",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    briefing_key     TEXT PRIMARY KEY,
    generated_at_utc TEXT,
    date_et          TEXT,
    time_et          TEXT,
    hour_et          INTEGER,
    rank_in_day      INTEGER,
    n_in_day         INTEGER,
    is_last_of_day   INTEGER,
    pre_open         INTEGER,   -- generated before 09:30 ET
    engine           TEXT,
    tldr             TEXT,
    what_happened    TEXT,
    why_markets_move TEXT,
    watch_today      TEXT,
    bull_case        TEXT,
    bear_case        TEXT,
    nq_game_plan     TEXT,
    stern_angle      TEXT,
    payload_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_fc_date ON forecasts(date_et);
"""


def main() -> None:
    if not RAW.exists():
        raise SystemExit(f"missing {RAW}")

    records = []
    for line in RAW.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        outer = json.loads(line)
        try:
            payload = json.loads(outer["payload_json"])
        except json.JSONDecodeError:
            print(f"  unparseable payload: {outer['briefing_key']}")
            continue

        raw_ts = outer["updated_at"]
        try:
            dt_utc = datetime.fromisoformat(raw_ts)
        except ValueError:
            print(f"  bad timestamp {raw_ts!r}, skipping")
            continue
        dt_et = dt_utc.astimezone(ET)

        rec = {
            "briefing_key": outer["briefing_key"],
            "generated_at_utc": raw_ts,
            "date_et": dt_et.date().isoformat(),
            "time_et": dt_et.strftime("%H:%M:%S"),
            "hour_et": dt_et.hour,
            "engine": str(payload.get("engine", ""))[:64],
            "payload_json": outer["payload_json"],
        }
        for f in TEXT_FIELDS:
            v = payload.get(f)
            rec[f] = v.strip() if isinstance(v, str) else None
        records.append(rec)

    # rank within each ET day, chronologically
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[r["date_et"]].append(r)
    for day, group in by_day.items():
        group.sort(key=lambda r: r["generated_at_utc"])
        for i, r in enumerate(group, 1):
            r["rank_in_day"] = i
            r["n_in_day"] = len(group)
            r["is_last_of_day"] = int(i == len(group))
            r["pre_open"] = int(
                (r["hour_et"], int(r["time_et"][3:5])) < (9, 30)
            )

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    cols = [
        "briefing_key", "generated_at_utc", "date_et", "time_et", "hour_et",
        "rank_in_day", "n_in_day", "is_last_of_day", "pre_open", "engine",
        *TEXT_FIELDS, "payload_json",
    ]
    conn.executemany(
        f"INSERT OR REPLACE INTO forecasts ({','.join(cols)}) "
        f"VALUES ({','.join(':' + c for c in cols)})",
        records,
    )
    conn.commit()

    # ---------------------------------------------------------------- report
    print("=" * 64)
    print("FORECASTS LOADED")
    print("=" * 64)
    print(f"  rows            {len(records)}")
    print(f"  distinct days   {len(by_day)}")
    days = sorted(by_day)
    print(f"  range           {days[0]} -> {days[-1]}")

    full = sum(
        1 for r in records
        if r["bull_case"] and r["bear_case"] and r["nq_game_plan"]
    )
    print(f"  complete (bull+bear+nq_game_plan)   {full} / {len(records)}")

    print("\n  analyses per day:")
    for n, count in sorted(Counter(len(g) for g in by_day.values()).items()):
        print(f"    {n} per day   {count:>3} days")

    print("\n  GENERATION TIME (ET) -- decides the testable horizon:")
    hours = Counter(r["hour_et"] for r in records)
    for h in sorted(hours):
        marker = "  pre-open" if h < 9 else ("  <- straddles open" if h == 9 else "")
        bar = "#" * min(hours[h], 40)
        print(f"    {h:02d}:00  {hours[h]:>4}  {bar}{marker}")

    pre = sum(r["pre_open"] for r in records)
    pre_days = len({r["date_et"] for r in records if r["pre_open"]})
    print(f"\n  generated BEFORE 09:30 ET:  {pre} rows on {pre_days} days")
    print(f"  generated AFTER  09:30 ET:  {len(records) - pre} rows")

    print("\n  overlap with the newsletter corpus:")
    row = conn.execute(
        """SELECT COUNT(DISTINCT f.date_et) FROM forecasts f
           WHERE EXISTS (SELECT 1 FROM newsletters n
                         WHERE n.received_date_et = f.date_et)"""
    ).fetchone()
    print(f"    forecast days with same-day newsletter text: {row[0]} / {len(by_day)}")
    conn.close()


if __name__ == "__main__":
    main()
