r"""
Daily forward-label job. Runs on the Pulse droplet.

Its whole reason to exist: Amendment 1 of PRE_REGISTRATION.md requires forward
labels to be written BEFORE that session's outcome exists. Batch-extracting
months later would make them retrospective and throw away the entire advantage
of forward testing. So this runs every morning, on the always-on machine.

THREE HARD GUARDS, enforced in code rather than by the cron schedule:

  1. PROSPECTIVE GUARD. Refuses to write a label at or after 09:30 America/
     New_York. If the session has opened, the outcome exists and the label
     would no longer be prospective. Timezone-aware, so DST cannot break it.
  2. PROMPT-HASH GUARD. Refuses to run unless EXTRACTOR_PROMPT.md hashes to
     the exact value committed to the repo. Silent prompt drift on the server
     would invalidate every label produced after it.
  3. READ-ONLY on pulse.db. Opened with mode=ro. This job can never corrupt
     the production database it reads from.

Idempotent: a date already labelled is skipped, so cron can fire as often as
it likes and only the first successful run of the day does any work.

  --dry-run   exercise the full path on the most recent pre-open forecast
              without writing anything and without the time guard
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
PULSE_DB = "/opt/pulse/state/pulse.db"
LABEL_DB = HERE / "forecast_labels.db"
PROMPT_FILE = HERE / "EXTRACTOR_PROMPT.md"

# sha256 of the prompt as committed in the News Corpus repo. Must match.
EXPECTED_PROMPT_SHA = (
    "56764fad48373a4dbacb10e5cd09e4386d3320c24ca28a17407c3b63adc42f65"
)
MODEL = "claude-opus-5"
INSTRUMENTS = ("NQ", "CL", "TNX")
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)

MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
DATE_PATTERNS = [
    re.compile(rf"\b({MONTHS})\s+\d{{1,2}}(st|nd|rd|th)?(,\s*\d{{4}})?\b", re.I),
    re.compile(rf"\b\d{{1,2}}\s+({MONTHS})\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b"),
    re.compile(r"\b(19|20)\d{2}\b"),
    re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I),
    re.compile(r"\b(yesterday|today|tomorrow|last night|overnight)\b", re.I),
    re.compile(r"\b(Q[1-4])\s*(of\s*)?((19|20)\d{2})?\b"),
]
FIELDS = ("why_markets_move", "watch_today", "bull_case", "bear_case", "nq_game_plan")


class Call(BaseModel):
    label: Literal["up", "down", "no_call"]
    evidence: str


class Extraction(BaseModel):
    NQ: Call
    CL: Call
    TNX: Call


SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    date_et        TEXT NOT NULL,
    briefing_key   TEXT NOT NULL,
    instrument     TEXT NOT NULL,
    label          TEXT NOT NULL,
    evidence       TEXT,
    prompt_sha256  TEXT NOT NULL,
    model          TEXT NOT NULL,
    generated_at   TEXT NOT NULL,   -- when the FORECAST was written
    extracted_at   TEXT NOT NULL,   -- when this label was written
    prospective    INTEGER NOT NULL,-- 1 = written before the session opened
    PRIMARY KEY (date_et, instrument, prompt_sha256)
);
"""


def log(msg: str) -> None:
    stamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{stamp}] {msg}", flush=True)


def strip_dates(text: str) -> str:
    for pat in DATE_PATTERNS:
        text = pat.sub("[DATE]", text)
    return re.sub(r"(\[DATE\]\s*){2,}", "[DATE] ", text).strip()


def load_prompt() -> str:
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if sha != EXPECTED_PROMPT_SHA:
        log(f"ABORT: prompt hash {sha[:16]}... != expected "
            f"{EXPECTED_PROMPT_SHA[:16]}...  The frozen prompt was modified.")
        sys.exit(1)
    body = raw.split("\n---\n", 1)[1]
    system = body.split("{TEXT}", 1)[0]
    return system.rstrip().removesuffix("Here is the commentary:").rstrip()


def latest_pre_open(now_et: datetime, any_date: bool) -> dict | None:
    """The latest analysis generated before 09:30 ET. Normally restricted to
    today's ET date; --dry-run relaxes that to whatever is most recent."""
    conn = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT briefing_key, payload_json, updated_at FROM briefings
           WHERE briefing_key LIKE 'overarching_analysis_v7_%'
           ORDER BY updated_at DESC"""
    ).fetchall()
    conn.close()

    target = now_et.date()
    for r in rows:
        try:
            gen_et = datetime.fromisoformat(r["updated_at"]).astimezone(ET)
        except ValueError:
            continue
        if gen_et.time() >= MARKET_OPEN:
            continue                              # not a pre-open forecast
        if not any_date and gen_et.date() != target:
            continue
        import json
        try:
            payload = json.loads(r["payload_json"])
        except json.JSONDecodeError:
            continue
        parts = [
            f"## {f.replace('_', ' ').title()}\n{payload[f].strip()}"
            for f in FIELDS
            if isinstance(payload.get(f), str) and payload[f].strip()
        ]
        if not parts:
            continue
        return {
            "briefing_key": r["briefing_key"],
            "date_et": gen_et.date().isoformat(),
            "generated_at": gen_et.isoformat(),
            "text": strip_dates("\n\n".join(parts)),
        }
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the full path without writing; skips the time guard")
    ap.add_argument("--db", default=None,
                    help="write to an alternate label DB (for verifying the "
                         "write path without touching the real forward record)")
    ap.add_argument("--allow-any-date", action="store_true",
                    help="with --db only: label the most recent pre-open "
                         "forecast regardless of date")
    args = ap.parse_args()

    global LABEL_DB
    if args.db:
        LABEL_DB = Path(args.db)

    system = load_prompt()
    now_et = datetime.now(ET)

    # ---- guard 1: prospective ------------------------------------------
    relaxed = args.dry_run or bool(args.db)
    if not relaxed and now_et.time() >= MARKET_OPEN:
        log(f"skip: it is {now_et.strftime('%H:%M')} ET, at or past the 09:30 "
            f"open. A label written now would not be prospective.")
        return

    fc = latest_pre_open(now_et, any_date=args.dry_run or args.allow_any_date)
    if fc is None:
        log("skip: no pre-open forecast available yet for today")
        return

    conn = sqlite3.connect(LABEL_DB)
    conn.executescript(SCHEMA)
    already = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE date_et=? AND prompt_sha256=?",
        (fc["date_et"], EXPECTED_PROMPT_SHA),
    ).fetchone()[0]
    if already and not args.dry_run:
        log(f"skip: {fc['date_et']} already labelled")
        conn.close()
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        log("ABORT: ANTHROPIC_API_KEY not in environment")
        sys.exit(1)

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user",
                   "content": f"Here is the commentary:\n\n{fc['text']}"}],
        output_format=Extraction,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log(f"ABORT: model returned {resp.stop_reason} / unparseable")
        sys.exit(1)

    result = resp.parsed_output
    summary = " ".join(f"{i}={getattr(result, i).label}" for i in INSTRUMENTS)

    if args.dry_run:
        log(f"DRY RUN ok — would label {fc['date_et']}: {summary}")
        log(f"  forecast generated {fc['generated_at']}")
        conn.close()
        return

    for inst in INSTRUMENTS:
        call: Call = getattr(result, inst)
        conn.execute(
            """INSERT OR REPLACE INTO labels
               (date_et, briefing_key, instrument, label, evidence,
                prompt_sha256, model, generated_at, extracted_at, prospective)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (fc["date_et"], fc["briefing_key"], inst, call.label, call.evidence,
             EXPECTED_PROMPT_SHA, MODEL, fc["generated_at"],
             now_et.isoformat()),
        )
    conn.commit()
    total = conn.execute("SELECT COUNT(DISTINCT date_et) FROM labels").fetchone()[0]
    conn.close()
    log(f"labelled {fc['date_et']}: {summary}   (forward record now {total} days)")


if __name__ == "__main__":
    main()
