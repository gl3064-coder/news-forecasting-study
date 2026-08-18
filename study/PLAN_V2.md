# Forecast Experiment v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pipeline that turns each morning's news into one directional trade in any market, scores it against a direction-aware baseline, and reports the pre-registered primary statistic from `PRE_REGISTRATION_V2.md`.

**Architecture:** Four label streams (raw news × digest, crossed with must-call × free-to-pass) written to a `calls_v2` table in the existing `news_corpus.db`. Extraction runs through the Batch API against `claude-opus-5` with a frozen, hash-verified prompt. A frozen market-name → ticker table resolves free-text market names; anything it does not cover is recorded as unscoreable rather than approximated. Scoring subtracts each call's direction-aware no-skill expectation and bootstraps by day.

**Tech Stack:** Python 3.12 (Pulse's venv at `OneDrive\Desktop\Pulse\Pulse\backend\.venv\Scripts\python.exe`), `anthropic`, `pydantic`, `sqlite3`, `yfinance`, `pandas`, `numpy`, `pytest`.

**Order is load-bearing.** §5 of the pre-registration requires the resolution table to exist before any label is extracted, and §6 requires the prompt to be frozen before extraction. Tasks 1 and 2 are therefore pre-commitments and must be committed before Task 4 runs. Prices are not fetched until Task 6.

---

## File Structure

| File | Responsibility |
|---|---|
| `EXTRACTOR_PROMPT_V2.md` | Create. The frozen v2 prompt. Hash stored on every row. |
| `markets_v2.py` | Create. Frozen name → ticker table + `resolve()`. No I/O, no API. |
| `test_markets_v2.py` | Create. Tests for `resolve()`. |
| `extract_v2.py` | Create. Builds the four streams, submits the batch, writes `calls_v2`. |
| `score_v2.py` | Create. Direction-aware baseline, day-clustered bootstrap, report. |
| `test_score_v2.py` | Create. Tests for the statistic and the baseline. |
| `news_corpus.db` | Modify. New `calls_v2` table. Existing tables untouched. |

`markets_v2.py` is deliberately pure: it is the piece the pre-registration freezes, so it must be testable without a database or an API key.

---

### Task 1: Frozen market resolution table

**Files:**
- Create: `markets_v2.py`
- Test: `test_markets_v2.py`

- [ ] **Step 1: Write the failing test**

Create `test_markets_v2.py`:

```python
from markets_v2 import resolve, MARKETS


def test_exact_name_resolves():
    assert resolve("gold") == "GLD"
    assert resolve("nasdaq") == "^NDX"


def test_normalisation_is_case_and_space_insensitive():
    assert resolve("  GOLD  ") == "GLD"
    assert resolve("S&P  500") == "^GSPC"
    assert resolve("The Dow") == "^DJI"


def test_unknown_market_returns_none():
    assert resolve("turkish lira") is None
    assert resolve("shipping rates") is None


def test_bond_price_and_bond_yield_are_different_tickers():
    # "bonds" is a price; "10-year yield" is a yield. They move opposite ways,
    # so mapping both to one ticker would silently invert half the calls.
    assert resolve("bonds") == "TLT"
    assert resolve("10-year yield") == "^TNX"
    assert resolve("bonds") != resolve("10-year yield")


def test_every_ticker_in_the_table_is_a_nonempty_string():
    assert MARKETS
    for name, ticker in MARKETS.items():
        assert name == name.strip().lower(), f"{name!r} is not normalised"
        assert isinstance(ticker, str) and ticker, name
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -m pytest test_markets_v2.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'markets_v2'`.

- [ ] **Step 3: Write the implementation**

Create `markets_v2.py`:

```python
"""
FROZEN market resolution table for forecast experiment v2.

PRE_REGISTRATION_V2.md section 5: resolution is exact, never approximate. A
named market either appears here or the call is recorded as unscoreable. It is
NOT stretched onto the nearest available proxy.

Cash-session instruments are preferred throughout (the v1 Amendment 2 lesson):
a daily bar for a `=F` futures ticker opens at the Globex session start the
previous evening, so scoring it open-to-close would measure a ~22-hour window
including the overnight move the 08:00 forecast already knew about.

This file is frozen once committed. Adding an entry after labels exist changes
which calls are scoreable, so it requires an Amendments entry in
PRE_REGISTRATION_V2.md with a date and a reason.
"""

from __future__ import annotations

import re

# name -> ticker. Keys must be lowercase, single-spaced, no leading article.
MARKETS: dict[str, str] = {
    # ---- equity indices (cash indices; bar is exactly the 09:30-16:00 session)
    "nasdaq": "^NDX",
    "nasdaq 100": "^NDX",
    "s&p 500": "^GSPC",
    "s&p": "^GSPC",
    "dow": "^DJI",
    "dow jones": "^DJI",
    "russell 2000": "^RUT",
    "small caps": "^RUT",
    "vix": "^VIX",
    "volatility": "^VIX",
    # ---- sectors (RTH-traded ETFs)
    "technology": "XLK",
    "tech": "XLK",
    "semiconductors": "SMH",
    "chips": "SMH",
    "financials": "XLF",
    "banks": "XLF",
    "energy": "XLE",
    "energy stocks": "XLE",
    "healthcare": "XLV",
    "utilities": "XLU",
    "industrials": "XLI",
    "consumer discretionary": "XLY",
    "consumer staples": "XLP",
    "real estate": "XLRE",
    "materials": "XLB",
    "communication services": "XLC",
    "homebuilders": "XHB",
    "regional banks": "KRE",
    "defense": "ITA",
    "gold miners": "GDX",
    # ---- major single names
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "netflix": "NFLX",
    "jpmorgan": "JPM",
    "goldman sachs": "GS",
    "exxon": "XOM",
    "chevron": "CVX",
    "walmart": "WMT",
    "eli lilly": "LLY",
    # ---- commodities (RTH-traded proxies)
    "oil": "USO",
    "crude": "USO",
    "crude oil": "USO",
    "wti": "USO",
    "brent": "BNO",
    "natural gas": "UNG",
    "gold": "GLD",
    "silver": "SLV",
    "copper": "CPER",
    "agriculture": "DBA",
    # ---- rates: YIELDS (a yield rising is the opposite of a bond rallying)
    "5-year yield": "^FVX",
    "10-year yield": "^TNX",
    "10 year yield": "^TNX",
    "treasury yields": "^TNX",
    "bond yields": "^TNX",
    "yields": "^TNX",
    "30-year yield": "^TYX",
    # ---- rates: PRICES
    "bonds": "TLT",
    "treasuries": "TLT",
    "long bonds": "TLT",
    "treasury bonds": "TLT",
    "corporate bonds": "LQD",
    "high yield": "HYG",
    "junk bonds": "HYG",
    # ---- FX (RTH-traded currency ETFs; each tracks the named currency vs USD)
    "dollar": "DX-Y.NYB",
    "dollar index": "DX-Y.NYB",
    "us dollar": "DX-Y.NYB",
    "euro": "FXE",
    "yen": "FXY",
    "japanese yen": "FXY",
    "pound": "FXB",
    "british pound": "FXB",
    "swiss franc": "FXF",
    "canadian dollar": "FXC",
    "emerging market currencies": "CEW",
    # ---- crypto (spot ETFs, so the bar is the cash session)
    "bitcoin": "IBIT",
    "ether": "ETHA",
    "ethereum": "ETHA",
    # ---- international equity
    "europe": "VGK",
    "european stocks": "VGK",
    "japan": "EWJ",
    "japanese stocks": "EWJ",
    "china": "FXI",
    "chinese stocks": "FXI",
    "emerging markets": "EEM",
}

_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)
_SPACES = re.compile(r"\s+")


def normalise(name: str) -> str:
    """Lowercase, collapse whitespace, drop a leading article."""
    out = _SPACES.sub(" ", name.strip().lower())
    return _ARTICLE.sub("", out).strip()


def resolve(name: str) -> str | None:
    """Return the ticker for a named market, or None if it is not in the frozen
    table. None means UNSCOREABLE — never substitute a nearby instrument."""
    return MARKETS.get(normalise(name))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -m pytest test_markets_v2.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add markets_v2.py test_markets_v2.py
git commit -m "Freeze the v2 market resolution table, before any label exists"
```

---

### Task 2: Freeze the v2 extractor prompt

**Files:**
- Create: `EXTRACTOR_PROMPT_V2.md`

- [ ] **Step 1: Write the prompt file**

Create `EXTRACTOR_PROMPT_V2.md`:

```markdown
# Extractor prompt v2 — FROZEN

Everything below the line is the prompt. `{MODE}` is replaced with the
must-call or free-to-pass clause; `{TEXT}` marks where the source text goes in
the user turn. Changing a single byte changes the sha256 stored on every row,
which is the point: labels are traceable to the exact prompt that produced them.

Per PRE_REGISTRATION_V2.md section 6 this prompt is written once and frozen.
Iterating it while watching the score is fitting, and is prohibited.

---

You are reading financial news and naming exactly one trade.

Your job is to identify the single best directional trade the news supports,
in any market you like. You are not restricted to a list.

## What to emit

- `market` — the market you are trading, in plain words. Use the most common
  name for it ("gold", "the 10-year yield", "nasdaq", "the yen"). Name the
  thing you actually mean: a bond price and a bond yield move in opposite
  directions, so say which one you are calling.
- `direction` — `up` or `down` for the market you named. Not for something
  related to it.
- `conviction` — `high` or `low`. High means the news gives a specific,
  dated, market-moving reason. Low means the read is thin or the news is quiet.
- `horizon` — `today`, `week`, or `month`. How long you think the move takes.
- `evidence` — a short quoted phrase from the text that the call rests on.

## Rules

1. **One trade only.** Pick your single best idea. Do not hedge across markets.
2. **A mention is not a forecast.** "Oil above $100 is pressuring tech" says
   where oil IS, not where it is GOING. If you trade that, you are trading
   tech, not oil.
3. **A conditional is not a call.** "If the Fed cuts, bonds rally" is not a
   forecast unless the text takes a view on whether the Fed cuts.
4. **Direction belongs to the market you named.** If you think bonds will
   rally, you can say `market: bonds, direction: up` or
   `market: the 10-year yield, direction: down`. Both are correct. Saying
   `market: the 10-year yield, direction: up` when you mean a bond rally is
   wrong.
5. **Ignore any sense of which day this is.** Dates have been removed. If you
   believe you recognise the events, reason from the text anyway and do not
   use anything you recall about what markets subsequently did.
6. **You have no price data.** Do not claim to know current levels.

{MODE}

{TEXT}
```

- [ ] **Step 2: Verify the file has both placeholders and one separator**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -c "
import hashlib, pathlib
raw = pathlib.Path('EXTRACTOR_PROMPT_V2.md').read_text(encoding='utf-8')
assert '\n---\n' in raw, 'no separator'
assert '{MODE}' in raw, 'no MODE placeholder'
assert '{TEXT}' in raw, 'no TEXT placeholder'
print('sha256:', hashlib.sha256(raw.encode()).hexdigest())
"
```

Expected: prints a sha256 and no assertion error. Record that hash — it goes in the commit message.

- [ ] **Step 3: Commit**

```bash
git add EXTRACTOR_PROMPT_V2.md
git commit -m "Freeze the v2 extractor prompt, before any label exists"
```

---

### Task 3: Extraction script

**Files:**
- Create: `extract_v2.py`

This task writes the script. It does not run it — that is Task 4.

- [ ] **Step 1: Write the script**

Create `extract_v2.py`:

```python
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
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
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
    prompt_sha256  TEXT NOT NULL,
    model          TEXT NOT NULL,
    extracted_at   TEXT NOT NULL,
    PRIMARY KEY (date_et, stream, prompt_sha256)
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
    """date -> concatenated newsletter bodies, weekdays with >= 2 messages.

    Concatenation happens in Python rather than via GROUP_CONCAT because
    SQLite does not guarantee GROUP_CONCAT ordering, and the same day's text
    must be byte-identical across streams A and B."""
    buckets: dict[str, list[str]] = {}
    for r in conn.execute(
        """SELECT received_date_et AS d, body
           FROM newsletters ORDER BY received_date_et, received_at_utc"""
    ):
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
            "SELECT date_et, stream FROM calls_v2 WHERE prompt_sha256=?", (sha,)
        )
    }
    jobs = []
    for stream, cfg in STREAMS.items():
        for date, text in sources[cfg["source"]].items():
            if (date, stream) in done:
                continue
            jobs.append((f"{date}|{stream}", stream, text))
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
        date, stream = result.custom_id.split("|")
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
                evidence, prospective, prompt_sha256, model, extracted_at)
               VALUES (?,?,?,?,?,?,?,0,?,?,datetime('now'))""",
            (date, stream, trade.market, trade.direction, trade.conviction,
             trade.horizon, trade.evidence, sha, MODEL),
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
```

- [ ] **Step 2: Verify it reports the expected job count without calling the API**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" extract_v2.py --count
```

Expected: prints the prompt sha256, then roughly `outstanding jobs: 486` with stream A ≈ 180, B ≈ 180, C ≈ 63, D ≈ 63. If A and B are not equal, or C and D are not equal, the source selection is wrong — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add extract_v2.py
git commit -m "v2 extraction script: four streams via the Batch API"
```

---

### Task 4: Run the retrospective extraction

**Files:**
- Modify: `news_corpus.db` (new `calls_v2` rows)

- [ ] **Step 1: Submit the batch**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" extract_v2.py --submit
```

Expected: `submitted 486 requests` and a batch id written to `batch_v2_id.txt`. Cost at this size is roughly $25.

- [ ] **Step 2: Collect the results**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" extract_v2.py --collect
```

Expected: polls until the batch ends (typically under an hour), then `written: ~486  skipped: 0`. A handful of skips is acceptable and gets reported; a large number means the output schema is being rejected — inspect one failing result before rerunning.

- [ ] **Step 3: Sanity-check the distribution without looking at any price**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -c "
import sqlite3, collections
from markets_v2 import resolve
c = sqlite3.connect('news_corpus.db'); c.row_factory = sqlite3.Row
for s in 'ABCD':
    rows = c.execute('SELECT * FROM calls_v2 WHERE stream=?', (s,)).fetchall()
    if not rows: continue
    dirs = collections.Counter(r['direction'] for r in rows)
    unres = [r['market'] for r in rows if r['direction'] != 'none' and resolve(r['market']) is None]
    top = collections.Counter(r['market'].lower() for r in rows).most_common(5)
    print(f'stream {s}: n={len(rows)} {dict(dirs)} unscoreable={len(unres)}')
    print('   most-named:', top)
    if unres: print('   unresolved:', sorted(set(unres))[:10])
"
```

Expected: a direction split per stream, an unscoreable count, and the most-named markets. Record the unscoreable rate — §5 requires reporting it. **Do not adjust `markets_v2.py` in response to this output**; the table was frozen in Task 1 and the unscoreable rate is a finding.

- [ ] **Step 4: Commit**

```bash
git add batch_v2_id.txt
git commit -m "v2 retrospective extraction complete, still no prices pulled"
```

---

### Task 5: Scorer

**Files:**
- Create: `score_v2.py`
- Test: `test_score_v2.py`

- [ ] **Step 1: Write the failing test**

Create `test_score_v2.py`:

```python
from score_v2 import corrected_hit, statistic


def test_up_call_on_a_market_that_always_rises_scores_zero():
    # market up-rate 1.0, called up, hit. Expectation is also 1.0.
    assert corrected_hit("up", "up", up_rate=1.0) == 0.0


def test_down_call_is_scored_against_the_down_rate_not_the_up_rate():
    # A market that rises 70% of the time falls 30%. A correct DOWN call beat
    # a 0.30 expectation, so it is worth +0.70 -- not +0.30.
    assert abs(corrected_hit("down", "down", up_rate=0.7) - 0.70) < 1e-9


def test_a_wrong_call_is_negative_by_the_expectation():
    assert abs(corrected_hit("up", "down", up_rate=0.6) - (-0.6)) < 1e-9


def test_direction_aware_baseline_neutralises_a_persistent_tilt():
    # A forecaster who always says "down" on a market that rises 60% of the
    # time is right 40% of the time. Expectation for a down call is 0.40.
    # Skill is therefore zero, not -20pp.
    rows = []
    for i in range(100):
        actual = "up" if i < 60 else "down"
        rows.append(("d%d" % i, "X", "down", actual, 0.6))
    assert abs(statistic(rows)) < 1e-9


def test_a_real_edge_shows_up_as_positive():
    # Always right, on a coin-flip market. Expectation 0.5 every time.
    rows = [("d%d" % i, "X", "up", "up", 0.5) for i in range(50)]
    assert abs(statistic(rows) - 0.5) < 1e-9


def test_statistic_of_no_rows_is_nan():
    assert statistic([]) != statistic([])
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -m pytest test_score_v2.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'score_v2'`.

- [ ] **Step 3: Write the implementation**

Create `score_v2.py`:

```python
r"""
Stage 2 of v2: pull prices and score the frozen rule.

This is the unblinding step. It applies PRE_REGISTRATION_V2.md exactly as
frozen. There is one primary number (stream A, same-session, direction-aware
baseline, day-clustered bootstrap) and everything else is labelled secondary or
exploratory.

Usage: python score_v2.py
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from markets_v2 import resolve

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
SEED = 42
N_BOOT = 10_000
PRIMARY_STREAM = "A"


# ------------------------------------------------------------------ statistic
def corrected_hit(direction: str, actual: str, up_rate: float) -> float:
    """hit - E[hit | no skill], where the expectation is direction-aware:
    an `up` call is scored against the market's up-rate, a `down` call against
    its down-rate. This neutralises both the market's drift and the
    forecaster's tilt, per section 7."""
    hit = 1.0 if direction == actual else 0.0
    expected = up_rate if direction == "up" else 1.0 - up_rate
    return hit - expected


def statistic(rows: list[tuple[str, str, str, str, float]]) -> float:
    """Mean corrected hit. rows = (day, ticker, direction, actual, up_rate)."""
    if not rows:
        return float("nan")
    return sum(corrected_hit(d, a, u) for _, _, d, a, u in rows) / len(rows)


def bootstrap(days: list[str], by_day: dict, fn, seed: int = SEED):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(days))
    vals, skipped = [], 0
    for _ in range(N_BOOT):
        draw = rng.choice(idx, size=len(idx), replace=True)
        rows = [r for i in draw for r in by_day[days[i]]]
        v = fn(rows)
        if v != v:
            skipped += 1
            continue
        vals.append(v)
    return np.array(vals), skipped


def verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "CI excludes zero, positive -> evidence of directional information"
    if hi < 0:
        return "CI excludes zero, negative -> ANTI-predictive"
    return "CI includes zero -> no detectable directional information at this sample size"


# ------------------------------------------------------------------ prices
def fetch(tickers: set[str], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for tk in sorted(tickers):
        df = yf.download(
            tk,
            start=(pd.Timestamp(lo) - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            end=(pd.Timestamp(hi) + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=False, progress=False, threads=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            print(f"  {tk:<10} NO DATA")
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out[tk] = df[["Open", "Close"]].dropna()
        print(f"  {tk:<10} {len(out[tk]):>4} sessions")
    return out


HORIZON_SESSIONS = {"today": 1, "week": 5, "month": 21}


def outcome(px: pd.DataFrame, date: str, sessions: int = 1) -> str | None:
    """Sign of the move from the open on `date` to the close `sessions`
    trading days later. sessions=1 is the same-session open-to-close horizon
    that section 7 fixes as primary."""
    ts = pd.Timestamp(date)
    if ts not in px.index:
        return None
    pos = px.index.get_loc(ts)
    exit_pos = pos + sessions - 1
    if exit_pos >= len(px.index):
        return None            # horizon has not resolved yet
    diff = float(px.iloc[exit_pos]["Close"]) - float(px.iloc[pos]["Open"])
    if diff == 0:
        return None
    return "up" if diff > 0 else "down"


def up_rate(px: pd.DataFrame, days: list[str]) -> float:
    """Fraction of sessions in the window where the market closed above its
    open. Counted over every session in the window, whether or not the market
    was called on it -- the base rate describes the market, not the sample."""
    window = px.loc[(px.index >= pd.Timestamp(min(days)))
                    & (px.index <= pd.Timestamp(max(days)))]
    if window.empty:
        return float("nan")
    return float((window["Close"] > window["Open"]).mean())


# ------------------------------------------------------------------ report
def score_stream(conn, stream: str, prices, all_days, label: str,
                 prospective: int = 0) -> None:
    """Section 8 requires retrospective and prospective calls to be reported
    separately, so the flag is a filter, never a pooled default."""
    calls = conn.execute(
        """SELECT date_et, market, direction, conviction, horizon
           FROM calls_v2 WHERE stream=? AND prospective=? ORDER BY date_et""",
        (stream, prospective),
    ).fetchall()
    if not calls:
        return
    passed = [c for c in calls if c["direction"] == "none"]
    live = [c for c in calls if c["direction"] != "none"]

    recs, unscoreable, no_session = [], [], 0
    for c in live:
        tk = resolve(c["market"])
        if tk is None or prices.get(tk) is None:
            unscoreable.append(c["market"])
            continue
        px = prices[tk]
        act = outcome(px, c["date_et"], 1)
        if act is None:
            no_session += 1
            continue
        recs.append({
            "day": c["date_et"], "ticker": tk, "direction": c["direction"],
            "actual": act, "up_rate": up_rate(px, all_days),
            "conviction": c["conviction"], "horizon": c["horizon"],
        })

    def tup(r: dict) -> tuple[str, str, str, str, float]:
        return (r["day"], r["ticker"], r["direction"], r["actual"], r["up_rate"])

    rows = [tup(r) for r in recs]

    print("\n" + "=" * 66)
    print(f"{label}  (stream {stream}, same session 09:30-16:00)")
    print("=" * 66)
    print(f"  calls emitted            {len(calls)}")
    print(f"  passed                   {len(passed)}")
    print(f"  unscoreable (not in the frozen table)  {len(unscoreable)}"
          f"  = {len(unscoreable)/max(len(live),1)*100:.0f}% of live calls")
    print(f"  dropped (no session)     {no_session}")
    print(f"  scored                   {len(rows)}")
    if not rows:
        print("  nothing to score")
        return

    days = sorted({d for d, _, _, _, _ in rows})
    by_day = defaultdict(list)
    for r in rows:
        by_day[r[0]].append(r)

    point = statistic(rows)
    arr, skipped = bootstrap(days, by_day, statistic)
    lo, hi = np.percentile(arr, [2.5, 97.5])
    hits = sum(d == a for _, _, d, a, _ in rows)

    print(f"\n  raw hit rate             {hits}/{len(rows)}"
          f" = {hits/len(rows)*100:.0f}%")
    print(f"  corrected skill          {point*100:+.1f}pp")
    print(f"  95% CI                   [{lo*100:+.1f}pp, {hi*100:+.1f}pp]"
          f"   ({len(arr)} draws, {skipped} discarded)")
    print(f"  {verdict(lo, hi)}")

    # ---- SECONDARY: each call scored at the horizon it actually stated
    h_rows, h_pending = [], 0
    for r in recs:
        px = prices[r["ticker"]]
        n = HORIZON_SESSIONS[r["horizon"]]
        act = outcome(px, r["day"], n)
        if act is None:
            h_pending += 1
            continue
        h_rows.append((r["day"], r["ticker"], r["direction"], act, r["up_rate"]))
    print(f"\n  SECONDARY (each call at its own stated horizon)")
    print(f"    scored {len(h_rows)}   unresolved {h_pending}")
    if h_rows:
        h_days = sorted({d for d, _, _, _, _ in h_rows})
        h_by_day = defaultdict(list)
        for r in h_rows:
            h_by_day[r[0]].append(r)
        h_pt = statistic(h_rows)
        h_arr, _ = bootstrap(h_days, h_by_day, statistic)
        h_lo, h_hi = np.percentile(h_arr, [2.5, 97.5])
        print(f"    skill {h_pt*100:+.1f}pp   95% CI"
              f" [{h_lo*100:+.1f}pp, {h_hi*100:+.1f}pp]")
        print("    (overlapping multi-day windows make these observations")
        print("     non-independent — which is why they are not the primary)")

    print("\n  EXPLORATORY (not a test)")
    for conv in ("high", "low"):
        sub = [tup(r) for r in recs if r["conviction"] == conv]
        if sub:
            print(f"    conviction {conv:<5} n={len(sub):<4}"
                  f" skill {statistic(sub)*100:+.1f}pp")
    for hz in ("today", "week", "month"):
        n = sum(1 for r in recs if r["horizon"] == hz)
        if n:
            print(f"    stated horizon {hz:<6} n={n}")
    top = defaultdict(int)
    for r in recs:
        top[r["ticker"]] += 1
    print("    most-traded:", ", ".join(
        f"{t}({n})" for t, n in sorted(top.items(), key=lambda kv: -kv[1])[:8]))


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    all_days = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT date_et FROM calls_v2")})
    markets = {r[0] for r in conn.execute(
        "SELECT DISTINCT market FROM calls_v2 WHERE direction != 'none'")}
    tickers = {t for t in (resolve(m) for m in markets) if t}

    print(f"days:    {len(all_days)}  ({all_days[0]} .. {all_days[-1]})")
    print(f"markets named: {len(markets)}   resolving to {len(tickers)} tickers")
    print("\ndownloading prices")
    prices = fetch(tickers, all_days[0], all_days[-1])

    for flag, group in ((0, "RETROSPECTIVE"), (1, "PROSPECTIVE")):
        score_stream(conn, PRIMARY_STREAM, prices, all_days,
                     f"PRIMARY [{group}]", flag)
        for s in ("B", "C", "D"):
            score_stream(conn, s, prices, all_days,
                         f"SECONDARY {s} [{group}]", flag)

    print("\n" + "=" * 66)
    print("Per section 8: this run is RETROSPECTIVE. Leakage biases it upward,")
    print("so a positive result here is suggestive only. Re-run this identical")
    print("script as the forward sample grows. Do not change the rule now that")
    print("the number is visible.")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" -m pytest test_score_v2.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit — before any price is fetched**

```bash
git add score_v2.py test_score_v2.py
git commit -m "v2 scorer: direction-aware baseline, committed before any price exists"
```

---

### Task 6: Run the score

**Files:**
- Create: `result_v2.txt`

- [ ] **Step 1: Run the scorer and capture the output**

```bash
"/c/Users/lgavi/OneDrive/Desktop/Pulse/Pulse/backend/.venv/Scripts/python.exe" score_v2.py 2>&1 | tee result_v2.txt
```

Expected: a PRIMARY block for stream A and three SECONDARY blocks, each with a corrected skill figure and a 95% CI. Given ~170 scoreable days the CI half-width should be roughly 8 percentage points.

- [ ] **Step 2: Commit the result unchanged**

```bash
git add result_v2.txt
git commit -m "v2 retrospective result"
```

Whatever the number is, it is the result. §7 fixes the decision rule in advance: a CI containing zero is written up as "no detectable directional information at this sample size," not as a reason to try a variant.

---

### Not in this plan: the forward job

The droplet job that writes prospective v2 calls each morning is deliberately
out of scope here. It needs source code this plan does not produce — fetching
*today's* newsletters live from Gmail rather than from `news_corpus.db` — and
writing it before the retrospective run has validated the pipeline would mean
deploying an extractor whose output nobody has looked at yet.

Tasks 1-6 stand on their own: they produce a complete, committed, scored dry
run. Write the forward job as its own plan once Task 6's output exists, reusing
the three guards that already work in v1's `/opt/forecast-labels/daily_label.py`
(refuse to write at or after 09:30 ET, refuse to run on a prompt-hash mismatch,
open `pulse.db` read-only) and its own separate database so the two studies
never contaminate each other.

The `prospective` column and the retrospective/prospective split in `score_v2.py`
already exist, so the forward rows will merge into the existing scorer with no
change to the frozen rule.

---


## Notes carried from the pre-registration

- **Do not edit `markets_v2.py` or `EXTRACTOR_PROMPT_V2.md` after Task 4 runs.** Both are frozen pre-commitments; a change after labels exist requires an Amendments entry with a date and a reason.
- **Do not fetch a price before Task 6.** Tasks 1–5 are all committed while the outcome is still unknown, which is what makes the timestamps meaningful.
- **Stream A is the only primary.** B, C and D are reported every time and never promoted.
- **Extractor accuracy is unverified** (§10). v1's hand-label validation was built and skipped, and v2 inherits that gap. Worth revisiting only if the result is going to be published or if label quality comes into question.
