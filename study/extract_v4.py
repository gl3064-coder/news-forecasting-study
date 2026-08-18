r"""Stage 1 of v4: extract routed calls from the frozen pre-open news.

Mirrors extract_v2.py deliberately — same model, same Batch API, same
date-blinding, same 09:30 ET pre-open input rule — so that any difference
between stream E and stream A is the routing rule in the prompt and not the
machinery around it. See PRE_REGISTRATION_V4.md, Amendment 1.

One difference from v2 that matters: v2 emitted exactly one trade per morning,
v4 emits zero to five, so calls_v4 carries one row per trade and a morning with
no trade is recorded as a pass row rather than being absent (absence and a
deliberate pass are different findings, and section 7 reports the pass rate).

Usage:
    python extract_v4.py --count     what would be submitted, no API call
    python extract_v4.py --dry-run   render one morning's request, no API call
    python extract_v4.py --submit    create the batch
    python extract_v4.py --collect   poll, then load results into calls_v4
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel, ValidationError

from extract_labels import strip_dates       # v1's frozen date blinding

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
PROMPT_FILE = HERE / "EXTRACTOR_PROMPT_V4.md"
PREREG = HERE / "PRE_REGISTRATION_V4.md"
BATCH_ID_FILE = HERE / "batch_v4_id.txt"

MODEL = "claude-opus-5"                       # same as stream A, section 2
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dt.time(9, 30)
INPUT_RULE = "pre_open"
MAX_TRADES = 5                                # section 3, declared arbitrary

MODE = (
    "Name between zero and five trades for this morning. Most mornings support "
    "one or none. Only name several when the news genuinely contains several "
    "separate, independently-evidenced stories. If nothing in the news supports "
    "a directional trade, return an empty list."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "market": {"type": "string"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "conviction": {"type": "string", "enum": ["high", "low"]},
                    "evidence": {"type": "string"},
                },
                "required": ["market", "direction", "conviction", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["trades"],
    "additionalProperties": False,
}

TABLE = """
CREATE TABLE IF NOT EXISTS calls_v4 (
    date_et       TEXT NOT NULL,
    seq           INTEGER NOT NULL,        -- 0-based index within the morning
    stream        TEXT NOT NULL,
    market        TEXT,                    -- NULL on a pass row
    direction     TEXT,                    -- 'up' | 'down' | NULL on a pass
    conviction    TEXT,
    evidence      TEXT,
    prospective   INTEGER NOT NULL,
    input_rule    TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    model         TEXT NOT NULL,
    extracted_at  TEXT NOT NULL,
    PRIMARY KEY (date_et, seq, stream, prompt_sha256)
)
"""


class TradeItem(BaseModel):
    market: str
    direction: Literal["up", "down"]
    conviction: Literal["high", "low"]
    evidence: str


class Trades(BaseModel):
    trades: list[TradeItem]


# ------------------------------------------------------------------ prompt
def load_prompt() -> tuple[str, str]:
    raw = PROMPT_FILE.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    body = raw.decode("utf-8").split("\n---\n", 1)[1]
    if "{MODE}" not in body or "{TEXT}" not in body:
        sys.exit("EXTRACTOR_PROMPT_V4.md is missing {MODE} or {TEXT}")
    return body.split("{TEXT}", 1)[0].rstrip(), sha


# ------------------------------------------------------------------ sources
def raw_days(conn: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    """date -> (concatenated pre-open text, prospective flag).

    Same selection rule as extract_v2.raw_days: weekdays with >= 2 newsletters,
    restricted to arrivals strictly before 09:30 ET. That time filter is the
    Correction 1 fix from v2 — without it the afternoon's closing wrap-ups leak
    into a call scored from that morning's open, which is reading the answer.

    Reads both the frozen v2 corpus and the forward block pulled 2026-08-16,
    tagging each morning with which one it came from.
    """
    buckets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    prospective: dict[str, int] = {}
    for table, flag in (("newsletters", 0), ("newsletters_forward", 1)):
        try:
            rows = conn.execute(
                f"""SELECT received_date_et AS d, received_at_utc, body
                    FROM {table} ORDER BY received_date_et, received_at_utc"""
            ).fetchall()
        except sqlite3.OperationalError:
            continue                          # forward table may not exist yet
        for d, ra, body in rows:
            try:
                arrived = dt.datetime.fromisoformat(ra).astimezone(ET)
            except (TypeError, ValueError):
                continue
            if arrived.time() >= MARKET_OPEN:
                continue
            if dt.date.fromisoformat(d).weekday() >= 5:
                continue
            buckets[d].append((ra, body or ""))
            prospective[d] = flag

    out: dict[str, tuple[str, int]] = {}
    for date, rows in buckets.items():
        if len(rows) < 2:
            continue
        rows.sort()
        text = strip_dates("\n\n---\n\n".join(b for _, b in rows))
        out[date] = (text, prospective[date])
    return out


def build_jobs(conn: sqlite3.Connection, sha: str) -> list[tuple[str, str, int]]:
    """(date, text, prospective) for mornings not yet extracted at this prompt."""
    done = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT date_et FROM calls_v4 WHERE prompt_sha256=?", (sha,))
    }
    return [(d, t, p) for d, (t, p) in sorted(raw_days(conn).items())
            if d not in done]


def make_request(date: str, text: str, template: str) -> Request:
    system = template.replace("{MODE}", MODE)
    return Request(
        custom_id=f"{date}_E",
        params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=16000,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": f"Here is the news:\n\n{text}"}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        ),
    )


def write_rows(conn, date: str, prospective: int, sha: str,
               trades: list[TradeItem]) -> int:
    """One row per trade; a single NULL-market row when the morning is a pass."""
    if len(trades) > MAX_TRADES:
        print(f"  {date}: {len(trades)} trades, truncated to {MAX_TRADES}")
        trades = trades[:MAX_TRADES]
    rows = [(date, i, "E", t.market, t.direction, t.conviction, t.evidence,
             prospective, INPUT_RULE, sha, MODEL)
            for i, t in enumerate(trades)]
    if not rows:
        rows = [(date, 0, "E", None, None, None, None,
                 prospective, INPUT_RULE, sha, MODEL)]
    conn.executemany(
        """INSERT OR REPLACE INTO calls_v4
           (date_et, seq, stream, market, direction, conviction, evidence,
            prospective, input_rule, prompt_sha256, model, extracted_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""", rows)
    return len(rows)


# ------------------------------------------------------------------ commands
def cmd_count(conn, sha, template) -> None:
    jobs = build_jobs(conn, sha)
    retro = sum(1 for _, _, p in jobs if p == 0)
    print(f"outstanding mornings: {len(jobs)}  "
          f"(retrospective {retro}, forward {len(jobs) - retro})")
    if jobs:
        chars = sum(len(t) for _, t, _ in jobs)
        print(f"total input: {chars:,} chars "
              f"(~{chars // 4:,} tokens, ~${chars / 4 / 1e6 * 5 * 0.5:.2f} "
              f"input at batch rates)")
    print(f"prompt sha256: {sha[:16]}...")


def cmd_dry_run(conn, sha, template) -> None:
    jobs = build_jobs(conn, sha)
    if not jobs:
        print("nothing outstanding")
        return
    date, text, prospective = jobs[0]
    req = make_request(date, text, template)
    p = req["params"]
    print(f"custom_id: {req['custom_id']}   prospective={prospective}")
    print(f"model: {p['model']}   max_tokens: {p['max_tokens']}")
    print(f"system: {len(p['system'][0]['text']):,} chars, "
          f"cache_control={p['system'][0].get('cache_control')}")
    print(f"user:   {len(p['messages'][0]['content']):,} chars")
    print(f"schema: {json.dumps(p['output_config']['format']['schema'])[:90]}...")
    print("\n--- system prompt (first 40 lines) ---")
    print("\n".join(p["system"][0]["text"].splitlines()[:40]))
    print("\nNo API call was made.")


def cmd_submit(conn, sha, template) -> None:
    jobs = build_jobs(conn, sha)
    if not jobs:
        print("nothing outstanding")
        return
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[make_request(d, t, template) for d, t, _ in jobs])
    BATCH_ID_FILE.write_text(batch.id, encoding="utf-8")
    print(f"submitted {len(jobs)} requests")
    print(f"batch id: {batch.id}  (saved to {BATCH_ID_FILE.name})")
    print("Now run: python extract_v4.py --collect")


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

    prospective = {d: p for d, (_, p) in raw_days(conn).items()}
    written = skipped = passes = 0
    for result in client.messages.batches.results(batch_id):
        date = result.custom_id.rsplit("_", 1)[0]
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
            parsed = Trades.model_validate_json(text)
        except ValidationError as exc:
            print(f"  {result.custom_id}: unparseable ({exc.error_count()} errors)")
            skipped += 1
            continue
        n = write_rows(conn, date, prospective.get(date, 0), sha, parsed.trades)
        written += n
        if not parsed.trades:
            passes += 1
    conn.commit()

    days = conn.execute(
        "SELECT COUNT(DISTINCT date_et) FROM calls_v4 WHERE prompt_sha256=?",
        (sha,)).fetchone()[0]
    calls = conn.execute(
        "SELECT COUNT(*) FROM calls_v4 WHERE prompt_sha256=? AND market IS NOT NULL",
        (sha,)).fetchone()[0]
    print(f"\nwrote {written} rows, skipped {skipped}")
    print(f"mornings: {days}   calls: {calls}   passes: {passes}")
    if days:
        print(f"calls per morning: {calls / days:.2f}   "
              f"(section 7: under ~2 means the floor rises above 0.3%)")


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for flag in ("count", "dry-run", "submit", "collect"):
        g.add_argument(f"--{flag}", action="store_true")
    a = ap.parse_args()

    template, sha = load_prompt()
    conn = sqlite3.connect(DB)
    conn.execute(TABLE)
    {"count": cmd_count, "dry_run": cmd_dry_run,
     "submit": cmd_submit, "collect": cmd_collect}[
        next(k for k in ("count", "dry_run", "submit", "collect")
             if getattr(a, k))](conn, sha, template)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
