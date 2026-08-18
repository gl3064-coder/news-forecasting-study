r"""
v2 forward job. Runs on the Pulse droplet every weekday morning.

Section 8 of PRE_REGISTRATION_V2.md: forward calls must be written BEFORE that
session's outcome exists. That is the entire point of the forward record, and
it is the one thing this job cannot get wrong.

FOUR HARD GUARDS, in code rather than in the cron schedule:

  1. PROSPECTIVE GUARD. Refuses to write at or after 09:30 America/New_York.
     Timezone-aware, so DST cannot quietly shift it.
  2. PROMPT-HASH GUARD. Refuses to run unless EXTRACTOR_PROMPT_V2.md still
     hashes to PROMPT_SHA. Silent prompt drift on the server would invalidate
     every call written after it.
  3. PRE-OPEN NEWS GUARD. Only newsletters that arrived before 09:30 ET are
     read. This is Correction 1: the retrospective extraction originally had no
     such filter and was shown the afternoon's wrap-ups. Here the guard is
     structural — at 08:00 the afternoon does not exist yet — but it is
     asserted anyway so the rule is enforced rather than assumed.
  4. READ-ONLY on pulse.db. This job can never corrupt production.

Idempotent: a date already called is skipped, so cron may fire as often as it
likes and only the first success of the day does any work.

  --dry-run   exercise the whole path without writing and without the time
              guard, so it can be tested at any hour
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel, ValidationError

HERE = Path(__file__).resolve().parent
PROMPT_FILE = HERE / "EXTRACTOR_PROMPT_V2.md"
DB = HERE / "calls_v2.db"
PULSE_DB = "/opt/pulse/state/pulse.db"
MODEL = "claude-opus-5"
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
INPUT_RULE = "pre_open"

# Set from the committed prompt. A mismatch aborts rather than silently
# producing calls under a different prompt than the study was frozen with.
PROMPT_SHA = "a1cdd1ccc881d91253875a91cd2ca6979e9f2f79f74950c7276f8f6ab6920625"

DIGEST_FIELDS = ("why_markets_move", "watch_today", "bull_case", "bear_case",
                 "nq_game_plan")

MUST_CALL = (
    "You must name a trade every day. If the news is quiet, say so by setting "
    "conviction to low, but still name your best available trade."
)
MAY_PASS = (
    "If the news genuinely does not support a directional trade, set market to "
    'the single word "pass" and direction to "none". Only pass when you would '
    "otherwise be guessing."
)
STREAMS = {
    "A": {"source": "raw", "mode": MUST_CALL},
    "B": {"source": "raw", "mode": MAY_PASS},
    "C": {"source": "digest", "mode": MUST_CALL},
    "D": {"source": "digest", "mode": MAY_PASS},
}

# Amendment 4 (2026-07-28). Forward collection is narrowed to stream A, the
# pre-registered PRIMARY. Grounds are cost, not results: four Opus 5 calls a day
# over the full pre-open text runs ~$8/month to accrue a record that needs 2-5
# years to resolve, against a point estimate worth ~$50/year. Stream A alone is
# ~$2/month and the primary test is untouched.
#
# What this forfeits, stated plainly: B/C/D stop accruing forward days, so the
# raw-vs-digest comparison (the retrospective's most interesting finding) can
# never be forward-validated. Their retrospective figures stand as reported.
#
# The full STREAMS table is kept above rather than deleted so the schema, the
# scoring code, and the historical rows stay legible — and so restarting a
# stream is a one-line change rather than an archaeology exercise.
ACTIVE_STREAMS = ("A",)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "market": {"type": "string"},
        "direction": {"type": "string", "enum": ["up", "down", "none"]},
        "conviction": {"type": "string", "enum": ["high", "low"]},
        "horizon": {"type": "string", "enum": ["today", "week", "month"]},
        "evidence": {"type": "string"},
    },
    "required": ["market", "direction", "conviction", "horizon", "evidence"],
    "additionalProperties": False,
}


class Trade(BaseModel):
    market: str
    direction: Literal["up", "down", "none"]
    conviction: Literal["high", "low"]
    horizon: Literal["today", "week", "month"]
    evidence: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS calls_v2 (
    date_et        TEXT NOT NULL,
    stream         TEXT NOT NULL,
    market         TEXT NOT NULL,
    direction      TEXT NOT NULL,
    conviction     TEXT,
    horizon        TEXT,
    evidence       TEXT,
    prospective    INTEGER NOT NULL DEFAULT 1,
    input_rule     TEXT NOT NULL DEFAULT 'pre_open',
    prompt_sha256  TEXT NOT NULL,
    model          TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    PRIMARY KEY (date_et, stream, prompt_sha256, input_rule)
);
"""

# ---------------------------------------------------------------- text rules
# Identical to the corpus build, so forward text is comparable to
# retrospective text. Pulse's own cleaner runs upstream of full_content; these
# two patterns are what it misses.
ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x034F], None
)
EXTRA_NOISE = re.compile(
    r"view it in a web browser\.?\s*›?|is this email difficult to read\??",
    re.IGNORECASE,
)

MONTHS = ("january|february|march|april|may|june|july|august|september|"
          "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|"
          "oct|nov|dec")
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


def post_clean(text: str) -> str:
    text = text.translate(ZERO_WIDTH)
    text = EXTRA_NOISE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n-|,›")


def strip_dates(text: str) -> str:
    for pat in DATE_PATTERNS:
        text = pat.sub("[DATE]", text)
    return re.sub(r"(\[DATE\]\s*){2,}", "[DATE] ", text).strip()


def log(msg: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


# ------------------------------------------------------------------ sources
def _pulse():
    conn = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def raw_text(target) -> tuple[str, int]:
    """Today's pre-open newsletters, concatenated in arrival order.

    Guard 3 is applied here rather than assumed from the clock: every message
    is checked against 09:30 ET individually, so a late run cannot pull in
    afternoon coverage even if the time guard were somehow bypassed."""
    conn = _pulse()
    rows = conn.execute(
        "SELECT received_at, full_content FROM newsletters ORDER BY received_at"
    ).fetchall()
    conn.close()

    bodies = []
    for r in rows:
        if not r["full_content"]:
            continue
        try:
            arrived = datetime.fromisoformat(r["received_at"]).astimezone(ET)
        except (TypeError, ValueError):
            continue
        if arrived.date() != target or arrived.time() >= MARKET_OPEN:
            continue
        bodies.append(post_clean(r["full_content"]))

    if len(bodies) < 2:                      # same floor as the corpus build
        return "", len(bodies)
    return strip_dates("\n\n---\n\n".join(bodies)), len(bodies)


def digest_text(target, any_date: bool) -> tuple[str, str | None]:
    """The latest Pulse analysis generated before 09:30 ET today."""
    conn = _pulse()
    rows = conn.execute(
        """SELECT briefing_key, payload_json, updated_at FROM briefings
           WHERE briefing_key LIKE 'overarching_analysis_v7_%'
           ORDER BY updated_at DESC"""
    ).fetchall()
    conn.close()

    for r in rows:
        try:
            gen = datetime.fromisoformat(r["updated_at"]).astimezone(ET)
        except (TypeError, ValueError):
            continue
        if gen.time() >= MARKET_OPEN:
            continue
        if not any_date and gen.date() != target:
            continue
        try:
            payload = json.loads(r["payload_json"])
        except json.JSONDecodeError:
            continue
        parts = [
            f"## {f.replace('_', ' ').title()}\n{payload[f].strip()}"
            for f in DIGEST_FIELDS
            if isinstance(payload.get(f), str) and payload[f].strip()
        ]
        if parts:
            return strip_dates("\n\n".join(parts)), r["briefing_key"]
    return "", None


def load_prompt() -> str:
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if sha != PROMPT_SHA:
        log(f"ABORT: prompt hash {sha[:12]} != expected {PROMPT_SHA[:12]}")
        sys.exit(1)
    body = raw.split("\n---\n", 1)[1]
    return body.split("{TEXT}", 1)[0].rstrip()


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now(ET)
    if not args.dry_run and now.time() >= MARKET_OPEN:
        log(f"skip: it is {now:%H:%M} ET, at or past the open. "
            "A call written now would not be prospective.")
        return
    if now.weekday() >= 5 and not args.dry_run:
        log("skip: weekend, no session to call")
        return

    template = load_prompt()
    target = now.date()

    raw, n_msgs = raw_text(target)
    digest, key = digest_text(target, args.dry_run)
    log(f"pre-open newsletters: {n_msgs} ({len(raw):,} chars) | "
        f"digest: {'yes' if digest else 'no'} ({len(digest):,} chars)")
    if not raw and not digest:
        log("skip: no pre-open input available yet today")
        return

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    today = target.isoformat()
    done = {r[0] for r in conn.execute(
        "SELECT stream FROM calls_v2 WHERE date_et=? AND prompt_sha256=?",
        (today, PROMPT_SHA))}

    client = anthropic.Anthropic()
    written = 0
    for stream, cfg in STREAMS.items():
        if stream not in ACTIVE_STREAMS:
            continue
        if stream in done:
            log(f"  {stream}: already called today")
            continue
        text = raw if cfg["source"] == "raw" else digest
        if not text:
            log(f"  {stream}: no {cfg['source']} text, skipped")
            continue

        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=16000,
                system=[{"type": "text",
                         "text": template.replace("{MODE}", cfg["mode"]),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user",
                           "content": f"Here is the news:\n\n{text}"}],
                output_config={"format": {"type": "json_schema",
                                          "schema": OUTPUT_SCHEMA}},
            )
        except anthropic.APIError as exc:
            log(f"  {stream}: API error {type(exc).__name__}, skipped")
            continue
        if resp.stop_reason == "refusal":
            log(f"  {stream}: refused, skipped")
            continue
        body = next((b.text for b in resp.content if b.type == "text"), None)
        try:
            trade = Trade.model_validate_json(body or "")
        except ValidationError:
            log(f"  {stream}: unparseable, skipped")
            continue

        log(f"  {stream}: {trade.market} {trade.direction} "
            f"({trade.conviction}, {trade.horizon})")
        if args.dry_run:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO calls_v2
               (date_et, stream, market, direction, conviction, horizon,
                evidence, prospective, input_rule, prompt_sha256, model,
                extracted_at)
               VALUES (?,?,?,?,?,?,?,1,?,?,?,datetime('now'))""",
            (today, stream, trade.market, trade.direction, trade.conviction,
             trade.horizon, trade.evidence, INPUT_RULE, PROMPT_SHA, MODEL))
        written += 1

    conn.commit()
    if args.dry_run:
        log("dry run: nothing written")
    else:
        total = conn.execute(
            "SELECT COUNT(DISTINCT date_et) FROM calls_v2").fetchone()[0]
        log(f"wrote {written} calls for {today} (forward record now {total} days)")
    conn.close()


if __name__ == "__main__":
    main()
