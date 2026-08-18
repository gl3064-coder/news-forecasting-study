r"""
Stage 1 of v2: turn each morning's text into one trade per stream.

Four streams, per PRE_REGISTRATION_V2.md section 3:
  A  raw newsletters, must call        <- PRIMARY
  B  raw newsletters, may pass
  C  Pulse digest,    must call
  D  Pulse digest,    may pass

Blinding (section 6): dates are stripped before the text is sent, no price data
is ever loaded here, one prompt across all four streams, and the prompt's
sha256 is stored on every row.

Uses the Batch API: 486 independent calls with no latency requirement, at half
price. Submit, then poll.

Usage:
    python extract_v2.py --count        # what is outstanding
    python extract_v2.py --submit       # create the batch, save its id
    python extract_v2.py --collect      # poll and write results
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel, ValidationError

from extract_labels import strip_dates  # reuse v1's frozen date blinding

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
PROMPT_FILE = HERE / "EXTRACTOR_PROMPT_V2.md"
BATCH_ID_FILE = HERE / "batch_v2_id.txt"
MODEL = "claude-opus-5"
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dt.time(9, 30)
# Which text-selection rule produced a row. Stored so the pre-open fix
# and the all-day rows it replaces can be compared rather than one
# silently overwriting the other.
INPUT_RULE = "pre_open"

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
    "A": {"source": "raw", "mode": MUST_CALL, "may_pass": False},
    "B": {"source": "raw", "mode": MAY_PASS, "may_pass": True},
    "C": {"source": "digest", "mode": MUST_CALL, "may_pass": False},
    "D": {"source": "digest", "mode": MAY_PASS, "may_pass": True},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls_v2 (
    date_et        TEXT NOT NULL,
    stream         TEXT NOT NULL,
    market         TEXT NOT NULL,
    direction      TEXT NOT NULL,
    conviction     TEXT,
    horizon        TEXT,
    evidence       TEXT,
    prospective    INTEGER NOT NULL DEFAULT 0,
    input_rule     TEXT NOT NULL DEFAULT 'all_day',
    prompt_sha256  TEXT NOT NULL,
    model          TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    PRIMARY KEY (date_et, stream, prompt_sha256, input_rule)
);
CREATE INDEX IF NOT EXISTS idx_calls_v2_date ON calls_v2(date_et);
"""

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


# ------------------------------------------------------------------ prompt
def load_prompt() -> tuple[str, str]:
    """Return (template, sha256). The template still contains {MODE}; {TEXT} is
    stripped because the text goes in the user turn."""
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if "\n---\n" not in raw:
        sys.exit("EXTRACTOR_PROMPT_V2.md has no '---' separator")
    body = raw.split("\n---\n", 1)[1]
    if "{MODE}" not in body or "{TEXT}" not in body:
        sys.exit("EXTRACTOR_PROMPT_V2.md is missing {MODE} or {TEXT}")
    return body.split("{TEXT}", 1)[0].rstrip(), sha


# ------------------------------------------------------------------ sources
def raw_days(conn: sqlite3.Connection) -> dict[str, str]:
    """date -> concatenated newsletter bodies, weekdays with >= 2 messages,
    RESTRICTED TO NEWSLETTERS THAT ARRIVED BEFORE 09:30 ET.

    Section 3 defines the raw input as "every NYT and WSJ newsletter received
    before 09:30 ET that morning". The first implementation of this function
    had no time filter, which let the afternoon's coverage — including the
    16:00 closing wrap-ups, the single largest arrival spike of the day — into
    a call scored from that morning's open. That is reading the answer, not
    forecasting it. Fixed to match the frozen spec.

    Timezone-aware rather than a fixed UTC offset, so the cutoff lands at 09:30
    local across the DST boundary.

    Concatenation happens in Python rather than via GROUP_CONCAT because
    SQLite does not guarantee GROUP_CONCAT ordering, and the same day's text
    must be byte-identical across streams A and B."""
    buckets: dict[str, list[str]] = {}
    for r in conn.execute(
        """SELECT received_date_et AS d, received_at_utc, body
           FROM newsletters ORDER BY received_date_et, received_at_utc"""
    ):
        try:
            arrived = dt.datetime.fromisoformat(r["received_at_utc"]).astimezone(ET)
        except (TypeError, ValueError):
            continue                      # unparseable timestamp: cannot prove
        if arrived.time() >= MARKET_OPEN:  # it was pre-open, so drop it
            continue
        buckets.setdefault(r["d"], []).append(r["body"])

    out: dict[str, str] = {}
    for date, bodies in buckets.items():
        if len(bodies) < 2:
            continue
        if dt.date.fromisoformat(date).weekday() >= 5:
            continue
        out[date] = strip_dates("\n\n---\n\n".join(bodies))
    return out


def digest_days(conn: sqlite3.Connection) -> dict[str, str]:
    """date -> the latest pre-open Pulse digest, same selection rule as v1."""
    rows = conn.execute(
        """WITH chosen AS (
               SELECT date_et, why_markets_move, watch_today, bull_case,
                      bear_case, nq_game_plan,
                      ROW_NUMBER() OVER (PARTITION BY date_et
                                         ORDER BY generated_at_utc DESC) AS rn
               FROM forecasts WHERE pre_open = 1)
           SELECT * FROM chosen WHERE rn = 1 ORDER BY date_et"""
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        parts = [
            f"## {f.replace('_', ' ').title()}\n{r[f].strip()}"
            for f in ("why_markets_move", "watch_today", "bull_case",
                      "bear_case", "nq_game_plan")
            if r[f] and r[f].strip()
        ]
        if parts:
            out[r["date_et"]] = strip_dates("\n\n".join(parts))
    return out


def build_jobs(conn: sqlite3.Connection, sha: str) -> list[tuple[str, str, str]]:
    """Return (custom_id, stream, text) for everything not yet extracted."""
    sources = {"raw": raw_days(conn), "digest": digest_days(conn)}
    done = {
        (r["date_et"], r["stream"])
        for r in conn.execute(
            "SELECT date_et, stream FROM calls_v2 "
            "WHERE prompt_sha256=? AND input_rule=?", (sha, INPUT_RULE)
        )
    }
    jobs = []
    for stream, cfg in STREAMS.items():
        if cfg["source"] == "digest":
            continue      # C and D read the 08:00 digest: already pre-open
        for date, text in sources[cfg["source"]].items():
            if (date, stream) in done:
                continue
            # custom_id must match ^[a-zA-Z0-9_-]{1,64}$ — underscore, not pipe.
            jobs.append((f"{date}_{stream}", stream, text))
    return sorted(jobs)


def make_request(custom_id: str, stream: str, text: str, template: str) -> Request:
    system = template.replace("{MODE}", STREAMS[stream]["mode"])
    return Request(
        custom_id=custom_id,
        params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=16000,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": f"Here is the news:\n\n{text}"}],
            output_config={"format": {"type": "json_schema",
                                      "schema": OUTPUT_SCHEMA}},
        ),
    )


# ------------------------------------------------------------------ commands
def cmd_count(conn, sha, template) -> None:
    jobs = build_jobs(conn, sha)
    by_stream: dict[str, int] = {}
    for _, s, _ in jobs:
        by_stream[s] = by_stream.get(s, 0) + 1
    print(f"outstanding jobs: {len(jobs)}")
    for s in sorted(by_stream):
        print(f"  stream {s}: {by_stream[s]}")


def cmd_submit(conn, sha, template) -> None:
    jobs = build_jobs(conn, sha)
    if not jobs:
        print("nothing outstanding")
        return
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[make_request(cid, s, t, template) for cid, s, t in jobs]
    )
    BATCH_ID_FILE.write_text(batch.id, encoding="utf-8")
    print(f"submitted {len(jobs)} requests")
    print(f"batch id: {batch.id}  (saved to {BATCH_ID_FILE.name})")
    print("Now run: python extract_v2.py --collect")


def cmd_collect(conn, sha, template) -> None:
    if not BATCH_ID_FILE.exists():
        sys.exit("no batch id file; run --submit first")
    batch_id = BATCH_ID_FILE.read_text(encoding="utf-8").strip()
    client = anthropic.Anthropic()

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        print(f"  {batch.processing_status}: "
              f"{batch.request_counts.processing} still processing", flush=True)
        time.sleep(60)

    written = skipped = 0
    for result in client.messages.batches.results(batch_id):
        date, stream = result.custom_id.rsplit("_", 1)
        if result.result.type != "succeeded":
            print(f"  {result.custom_id}: {result.result.type}")
            skipped += 1
            continue
        msg = result.result.message
        if msg.stop_reason == "refusal":
            print(f"  {result.custom_id}: refused")
            skipped += 1
            continue
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text is None:
            skipped += 1
            continue
        try:
            trade = Trade.model_validate_json(text)
        except ValidationError as exc:
            print(f"  {result.custom_id}: unparseable ({exc.error_count()} errors)")
            skipped += 1
            continue

        conn.execute(
            """INSERT OR REPLACE INTO calls_v2
               (date_et, stream, market, direction, conviction, horizon,
                evidence, prospective, input_rule, prompt_sha256, model,
                extracted_at)
               VALUES (?,?,?,?,?,?,?,0,?,?,?,datetime('now'))""",
            (date, stream, trade.market, trade.direction, trade.conviction,
             trade.horizon, trade.evidence, INPUT_RULE, sha, MODEL),
        )
        written += 1
    conn.commit()
    print(f"\nwritten: {written}   skipped: {skipped}")
    print("Next: commit the calls, THEN score. Not the other way round.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--collect", action="store_true")
    args = ap.parse_args()

    template, sha = load_prompt()
    print(f"prompt sha256: {sha}")
    print(f"model:         {MODEL}\n")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    if args.submit:
        cmd_submit(conn, sha, template)
    elif args.collect:
        cmd_collect(conn, sha, template)
    else:
        cmd_count(conn, sha, template)
    conn.close()


if __name__ == "__main__":
    main()
