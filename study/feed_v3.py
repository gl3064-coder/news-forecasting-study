r"""Render one v3 morning for the rater. Section 4 of PRE_REGISTRATION_V3.md.

Per morning, in this order:

  1. INDEX      subject lines, word counts, section markers, in document order
  2. NAME LINE  every frozen-table name found in the FULL text (name_line_table)
  3. FULL TEXT  the complete pre-open text, reflowed, nothing removed

The index and name line are ADDITIVE. Every character streams A and B received
is present in the full text below them. The only two transforms applied are
paragraph reflow and unsubscribe-footer removal (section 4), both deterministic
and both applied identically to all 120 mornings.

Section 9 binds this script's operator: verbatim delivery, no commentary, no
reading stream A's call or any price for a pool morning. Nothing here touches
`calls_v2` rows for streams A-D, and nothing here fetches a price.

Usage:
    python feed_v3.py                 next unanswered slot
    python feed_v3.py --slot 7        a specific slot
    python feed_v3.py --status        progress only
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# Newsletter subject lines carry emoji and smart quotes; the Windows console
# defaults to cp1252 and would raise on them mid-render.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from extract_labels import strip_dates
from name_line_table import ALL_NAMES, render as render_names

HERE = Path(__file__).parent
DB = HERE / "news_corpus.db"
OUT = HERE / "v3_mornings"
ET = ZoneInfo("America/New_York")
MARKET_OPEN = dt.time(9, 30)

# ---------------------------------------------------------------- section index
# Candidates are ALL-CAPS runs present on >= 10 of the 164 corpus mornings,
# computed once at freeze. Two mechanical filters are then applied, because the
# raw list is 65 entries and includes photo credits and ad sponsors:
#   * anything that is also a name in the frozen market table (GOOGLE, PALANTIR,
#     GOLDMAN SACHS) — the name line already reports those, in the right place
#   * wire services and photo agencies, which are credit lines and not sections
# This filters the INDEX only. The full text below retains every marker.
_CREDIT = frozenset({
    "GETTY IMAGES", "REUTERS", "ZUMA PRESS", "BLOOMBERG NEWS", "ASSOCIATED PRESS",
    "AGENCE FRANCE-PRESSE", "CONTENT FROM", "MESSAGE FROM", "COMPLIMENTARY",
    "ABOUT US", "BNY WEALTH", "BROOKFIELD", "NUVEEN", "CUNY TV", "ANTHROPIC",
    "CHG DJIA", "U.S. A", "J.B. P.S", "SEE THE STORY", "MARKETWATCH PICKS",
})
_MARKERS = frozenset({
    "ALTERNATE-SIDE PARKING", "AND FINALLY", "AROUND THE WORLD",
    "ASK THE MORNING", "BEFORE YOU GO", "BEYOND THE NEWSROOM",
    "EXPERT TAKE 5 Q", "HAPPENING TODAY", "JOURNAL", "LIVE FROM THE MARKETS",
    "METROPOLITAN DIARY", "MORE TOP NEWS", "MORNING READ", "MORNING READS",
    "MORNING READS A", "NUMBER OF THE DAY", "OPINIONS", "OTHER NEWS",
    "OTHER NEWS A", "PLAY TODAY’S GAMES", "POLYMARKET", "PRESS POOL",
    "QUOTE OF THE DAY", "READ IT HERE FIRST", "RECIPE", "RECIPE OF THE DAY",
    "RECOMMENDATIONS", "SPORTS", "SPORTS N.B.A", "SPORTS N.F.L",
    "THE LATEST NEWS", "THE MORNING RECOMMENDS", "THE NUMBER", "TIME TO PLAY",
    "TODAY'S HEADLINES", "TODAY’S HEADLINES", "TODAY’S NUMBER",
    "TOP OF THE WORLD", "W.N.B.A", "WEATHER", "WHAT ELSE IS HAPPENING",
    "WHERE IS THIS", "WORLD CUP", "WSJ AI",
}) - _CREDIT - {n.upper() for n in ALL_NAMES}

_CAPS = re.compile(r"\b([A-Z][A-Z0-9’'&.\- ]{4,32}[A-Z])\b")

FOOTER_MARKERS = (
    "Need help? Review our newsletter help page",
    "If you received this newsletter from someone else, subscribe here",
    "To opt out of other promotional emails from The Times",
    "The New York Times Company. 620 Eighth Avenue",
    "you signed up for",
    "To stop receiving",
    "We can’t always respond, but we do love to hear from you",
)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"‘'])")


def strip_footer(text: str) -> str:
    cut = len(text)
    for marker in FOOTER_MARKERS:
        i = text.find(marker)
        if i != -1:
            cut = min(cut, i)
    return text[:cut].rstrip()


def reflow(text: str) -> str:
    """Paragraph breaks every two sentences. No truncation, ever."""
    sents = [s.strip() for s in _SENT.split(" ".join(text.split())) if s.strip()]
    return "\n\n".join(" ".join(sents[i:i + 2]) for i in range(0, len(sents), 2))


def sections(body: str) -> list[str]:
    out: list[str] = []
    for m in _CAPS.finditer(body):
        g = m.group(1).strip()
        if g in _MARKERS and g not in out:
            out.append(g)
    return out


def load(conn: sqlite3.Connection, date_et: str, block: str) -> list[sqlite3.Row]:
    table = "newsletters" if block == "retro" else "newsletters_forward"
    rows = conn.execute(
        f"""SELECT subject, source, body, received_at_utc FROM {table}
            WHERE received_date_et=? ORDER BY received_at_utc""", (date_et,)
    ).fetchall()
    keep = []
    for r in rows:
        try:
            if dt.datetime.fromisoformat(r["received_at_utc"]).astimezone(ET).time() < MARKET_OPEN:
                keep.append(r)
        except (TypeError, ValueError):
            continue
    return keep


def render(conn: sqlite3.Connection, slot: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT date_et, block FROM calls_v3_meta WHERE slot=?", (slot,)
    ).fetchone()
    if row is None:
        sys.exit(f"slot {slot} is not in the pool (1..120)")

    msgs = load(conn, row["date_et"], row["block"])
    full_raw = "\n\n---\n\n".join(m["body"] or "" for m in msgs)
    full = strip_dates(full_raw)

    # ---- card: index + name line. Short enough to read in chat.
    card = [f"MORNING {slot:03d}   ({len(msgs)} newsletters, {len(full_raw):,} chars)",
            "=" * 72, ""]
    for i, m in enumerate(msgs, 1):
        subj = strip_dates(m["subject"] or "(no subject)")
        card.append(f"  [{i}] {subj}")
        card.append(f"      {m['source']}  |  {len(m['body'] or '') // 5.5:.0f} words".replace(".0 ", " "))
        secs = sections(m["body"] or "")
        if secs:
            line = "      sections: "
            for s in secs:
                if len(line) + len(s) > 88:
                    card.append(line.rstrip(" /"))
                    line = "                "
                line += s + " / "
            card.append(line.rstrip(" /"))
        card.append("")
    card.append("  NAMES FOUND IN THE FULL TEXT")
    card.append(render_names(full))
    card.append("")

    # ---- the full text, nothing removed
    doc = [f"# MORNING {slot:03d}", "",
           f"{len(msgs)} newsletters, {len(full_raw):,} characters. "
           f"This is the complete pre-open text streams A and B received, "
           f"reflowed into paragraphs with unsubscribe footers removed. "
           f"Nothing is summarised and nothing is omitted.", "",
           "\n".join(card), "", "---", ""]
    for i, m in enumerate(msgs, 1):
        doc.append(f"## [{i}] {strip_dates(m['subject'] or '(no subject)')}")
        doc.append("")
        doc.append(reflow(strip_dates(strip_footer(m["body"] or ""))))
        doc.append("")
    return "\n".join(card), "\n".join(doc)


def status(conn: sqlite3.Connection) -> str:
    done, total = conn.execute(
        "SELECT COUNT(answered_at), COUNT(*) FROM calls_v3_meta").fetchone()
    return f"{done} / {total} logged"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    if a.status:
        print(status(conn))
        return

    slot = a.slot
    if slot is None:
        r = conn.execute("SELECT MIN(slot) s FROM calls_v3_meta "
                         "WHERE answered_at IS NULL").fetchone()
        if r["s"] is None:
            print("all 120 logged. run score_v3.py to unblind.")
            return
        slot = r["s"]

    card, doc = render(conn, slot)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"morning_{slot:03d}.md"
    path.write_text(doc, encoding="utf-8")
    print(card)
    print(f"  full text: {path}")
    print(f"  progress:  {status(conn)}")


if __name__ == "__main__":
    main()
