r"""
Stage 1 of the scoring pipeline: turn each pre-open forecast into a
directional label per instrument.

Reads the FROZEN prompt from EXTRACTOR_PROMPT.md and applies it to one forecast
per date (the latest row before 09:30 ET). Writes to a `labels` table in
news_corpus.db.

BLINDING (per PRE_REGISTRATION.md section 5):
  - dates are stripped from the text before it reaches the model, so the model
    cannot recall what markets did on a recognisable day
  - no price data is ever loaded by this script
  - the same prompt is used for all three instruments
  - the prompt's sha256 is stored on every row, so labels are traceable to the
    exact prompt version that produced them

Run this and commit the labels BEFORE pulling any prices. Once you have seen
the prices you cannot un-see them, and any prompt tweak after that is fitting.

Usage:
    python extract_labels.py --count      # how many days need labelling
    python extract_labels.py --limit 3    # try a few first
    python extract_labels.py              # label everything outstanding
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
PROMPT_FILE = HERE / "EXTRACTOR_PROMPT.md"
MODEL = "claude-opus-5"
INSTRUMENTS = ("NQ", "CL", "TNX")

# The prompt file has a "Everything below the line is the prompt." preamble;
# the real prompt starts after the first horizontal rule.
PROMPT_SPLIT = "\n---\n"
TEXT_PLACEHOLDER = "{TEXT}"


# ------------------------------------------------------------------ blinding
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
    re.compile(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I
    ),
    re.compile(r"\b(yesterday|today|tomorrow|last night|overnight)\b", re.I),
    re.compile(rf"\b(Q[1-4])\s*(of\s*)?((19|20)\d{{2}})?\b"),
]


def strip_dates(text: str) -> str:
    """Remove anything that could let the model identify the actual session."""
    for pat in DATE_PATTERNS:
        text = pat.sub("[DATE]", text)
    return re.sub(r"(\[DATE\]\s*){2,}", "[DATE] ", text).strip()


# ------------------------------------------------------------------ schema
class Call(BaseModel):
    label: Literal["up", "down", "no_call"]
    evidence: str


class Extraction(BaseModel):
    NQ: Call
    CL: Call
    TNX: Call


# ------------------------------------------------------------------ storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    date_et        TEXT NOT NULL,
    briefing_key   TEXT NOT NULL,
    instrument     TEXT NOT NULL,
    label          TEXT NOT NULL,
    evidence       TEXT,
    prompt_sha256  TEXT NOT NULL,
    model          TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    PRIMARY KEY (date_et, instrument, prompt_sha256)
);
CREATE INDEX IF NOT EXISTS idx_labels_date ON labels(date_et);
"""


def load_prompt() -> tuple[str, str]:
    """Return (system_prompt, sha256). The system prompt is everything up to
    the {TEXT} placeholder; the forecast goes in the user turn instead, which
    keeps the frozen part byte-identical and therefore cacheable."""
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    if PROMPT_SPLIT not in raw:
        sys.exit("EXTRACTOR_PROMPT.md has no '---' separator")
    body = raw.split(PROMPT_SPLIT, 1)[1]

    if TEXT_PLACEHOLDER not in body:
        sys.exit(f"EXTRACTOR_PROMPT.md is missing the {TEXT_PLACEHOLDER} placeholder")
    system = body.split(TEXT_PLACEHOLDER, 1)[0]
    # drop the trailing "Here is the commentary:" lead-in; it belongs with the text
    return system.rstrip().removesuffix("Here is the commentary:").rstrip(), sha


def pending(conn: sqlite3.Connection, sha: str) -> list[sqlite3.Row]:
    """One forecast per date: the latest pre-open row, not yet labelled under
    the current prompt."""
    return conn.execute(
        """
        WITH chosen AS (
            SELECT date_et, briefing_key, nq_game_plan, bull_case, bear_case,
                   watch_today, why_markets_move,
                   ROW_NUMBER() OVER (
                       PARTITION BY date_et ORDER BY generated_at_utc DESC
                   ) AS rn
            FROM forecasts
            WHERE pre_open = 1
        )
        SELECT * FROM chosen
        WHERE rn = 1
          AND date_et NOT IN (
              SELECT date_et FROM labels WHERE prompt_sha256 = ?
          )
        ORDER BY date_et
        """,
        (sha,),
    ).fetchall()


def build_text(row: sqlite3.Row) -> str:
    parts = []
    for field in ("why_markets_move", "watch_today", "bull_case", "bear_case",
                  "nq_game_plan"):
        val = row[field]
        if val and val.strip():
            parts.append(f"## {field.replace('_', ' ').title()}\n{val.strip()}")
    return strip_dates("\n\n".join(parts))


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true", help="report and exit")
    ap.add_argument("--limit", type=int, default=0, help="label at most N days")
    args = ap.parse_args()

    system, sha = load_prompt()
    print(f"prompt sha256: {sha}")
    print(f"model:         {MODEL}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    todo = pending(conn, sha)
    done = conn.execute(
        "SELECT COUNT(DISTINCT date_et) FROM labels WHERE prompt_sha256=?", (sha,)
    ).fetchone()[0]
    print(f"already labelled: {done} days")
    print(f"outstanding:      {len(todo)} days\n")
    if args.count or not todo:
        return
    if args.limit:
        todo = todo[: args.limit]

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set in this shell")
    client = anthropic.Anthropic()

    counts: dict[str, int] = {}
    for n, row in enumerate(todo, 1):
        text = build_text(row)
        for attempt in range(5):
            try:
                resp = client.messages.parse(
                    model=MODEL,
                    max_tokens=16000,
                    # The frozen prompt is identical on every call, so cache it.
                    system=[{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{
                        "role": "user",
                        "content": f"Here is the commentary:\n\n{text}",
                    }],
                    output_format=Extraction,
                )
                break
            except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
                delay = 2**attempt
                print(f"    {type(exc).__name__}, retrying in {delay}s", flush=True)
                time.sleep(delay)
        else:
            print(f"  [{n}] {row['date_et']} FAILED after retries")
            continue

        if resp.stop_reason == "refusal":
            print(f"  [{n}] {row['date_et']} refused, skipping")
            continue

        result = resp.parsed_output
        if result is None:
            print(f"  [{n}] {row['date_et']} unparseable, skipping")
            continue

        for inst in INSTRUMENTS:
            call: Call = getattr(result, inst)
            counts[call.label] = counts.get(call.label, 0) + 1
            conn.execute(
                """INSERT OR REPLACE INTO labels
                   (date_et, briefing_key, instrument, label, evidence,
                    prompt_sha256, model, extracted_at)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))""",
                (row["date_et"], row["briefing_key"], inst, call.label,
                 call.evidence, sha, MODEL),
            )
        conn.commit()
        summary = " ".join(
            f"{i}={getattr(result, i).label}" for i in INSTRUMENTS
        )
        print(f"  [{n}/{len(todo)}] {row['date_et']}  {summary}", flush=True)

    print("\nlabel distribution this run:")
    for label, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {label:<9} {c}")
    print("\nNext: commit the labels, THEN pull prices. Not the other way round.")
    conn.close()


if __name__ == "__main__":
    main()
