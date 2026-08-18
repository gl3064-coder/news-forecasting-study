r"""
Pull the full NYT + WSJ newsletter history out of Gmail into a local corpus.

WHY THIS EXISTS
    Pulse stores newsletter text but its cron calls purge_old_newsletters(days=7),
    so anything older than a week is deleted from Pulse's DB. The Gmail originals
    were never touched, so the real archive has been accumulating there for free.
    This script extracts it into a separate SQLite file that Pulse's purge can
    never reach.

READ-ONLY BY DESIGN
    Only calls messages.list and messages.get. It never labels, archives,
    modifies, or deletes anything in Gmail. Safe to re-run.

RESUMABLE
    Already-stored message ids are skipped, so an interrupted run just continues.

USAGE
    # how many messages match, and the date range, without pulling bodies
    python build_corpus.py --count

    # pull everything (resumable)
    python build_corpus.py

    # pull a slice first to sanity-check
    python build_corpus.py --limit 50

Run with Pulse's venv interpreter, which already has the Google libraries:
    "..\Pulse\Pulse\backend\.venv\Scripts\python.exe" build_corpus.py --count
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Pulse owns the newsletter-cleaning regexes and the OAuth token. Reuse both
# rather than duplicating ~200 lines of noise-stripping that already works.
PULSE_BACKEND = Path(r"C:\Users\lgavi\OneDrive\Desktop\Pulse\Pulse\backend")
if not PULSE_BACKEND.exists():
    sys.exit(f"Pulse backend not found at {PULSE_BACKEND}")
sys.path.insert(0, str(PULSE_BACKEND))

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from app.services.gmail import (  # noqa: E402
    clean_newsletter_content,
    detect_source,
    detect_tier,
    extract_parts,
    get_gmail_credentials,
    newsletter_signal,
    normalize_newsletter_lines,
    parse_received_at,
)

# Pulse's .env sets GMAIL_CREDENTIALS_FILE / GMAIL_TOKEN_FILE as paths relative
# to the backend dir, and its load_dotenv runs with override=True at import time.
# So absolutise them AFTER the import (credentials_file() reads os.getenv lazily),
# otherwise auth fails when this script runs from any other directory.
os.environ["GMAIL_CREDENTIALS_FILE"] = str(PULSE_BACKEND / "credentials.json")
os.environ["GMAIL_TOKEN_FILE"] = str(PULSE_BACKEND / "token.json")

DB_PATH = Path(__file__).parent / "news_corpus.db"
QUERY = "from:nytimes.com OR from:wsj.com"
ET = ZoneInfo("America/New_York")

# Mail clients pad the preview line with runs of zero-width characters, and the
# WSJ footer wording ("View it in a web browser.") differs just enough from
# Pulse's "view in browser" pattern to slip through. Both are pure noise.
ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x034F], None
)
EXTRA_NOISE = re.compile(
    r"view it in a web browser\.?\s*›?|is this email difficult to read\??",
    re.IGNORECASE,
)


def post_clean(text: str) -> str:
    """Applied on top of Pulse's cleaning, not instead of it."""
    text = text.translate(ZERO_WIDTH)
    text = EXTRA_NOISE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n-|,›")


# ----------------------------------------------------------------- storage
def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS newsletters (
            gmail_message_id TEXT PRIMARY KEY,
            thread_id        TEXT,
            received_at_utc  TEXT,
            received_date_et TEXT,
            sender           TEXT,
            source           TEXT,
            subject          TEXT,
            tier             TEXT,
            signal           TEXT,
            n_chars          INTEGER,
            body             TEXT,
            pulled_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_date   ON newsletters(received_date_et);
        CREATE INDEX IF NOT EXISTS idx_source ON newsletters(source);
        CREATE INDEX IF NOT EXISTS idx_signal ON newsletters(signal);
        """
    )
    return conn


def stored_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT gmail_message_id FROM newsletters")}


# ----------------------------------------------------------------- gmail
def with_retries(request, what: str, tries: int = 6):
    """Gmail rate limits are per-second; back off and retry rather than dying
    partway through a few thousand fetches."""
    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc, "status_code", None) or exc.resp.status
            if status not in (403, 429, 500, 503) or attempt == tries - 1:
                raise
            delay = (2**attempt) + random.random()
            print(f"    {what}: HTTP {status}, retrying in {delay:.1f}s", flush=True)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def list_all_ids(service, query: str) -> list[str]:
    ids: list[str] = []
    token = None
    page = 0
    while True:
        page += 1
        resp = with_retries(
            service.users().messages().list(
                userId="me", q=query, maxResults=500, pageToken=token
            ),
            f"list page {page}",
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        token = resp.get("nextPageToken")
        print(f"  listed {len(ids)} ids...", flush=True)
        if not token:
            return ids


def fetch_one(service, message_id: str) -> dict | None:
    payload = with_retries(
        service.users().messages().get(userId="me", id=message_id, format="full"),
        f"get {message_id}",
    )
    headers = payload.get("payload", {}).get("headers", [])

    def header(name: str, default: str = "") -> str:
        return next(
            (h["value"] for h in headers if h["name"].lower() == name), default
        )

    sender = header("from")
    subject = header("subject", "(No subject)")

    plain, html = extract_parts(payload.get("payload", {}))
    body = clean_newsletter_content(
        plain_text=plain, html_text=html, snippet=payload.get("snippet", "")
    )
    body = post_clean("\n".join(normalize_newsletter_lines(body)))
    if not body:
        return None

    received_utc = parse_received_at(headers)
    try:
        dt = datetime.fromisoformat(received_utc)
    except ValueError:
        dt = datetime.now(timezone.utc)

    source_label, source_icon = detect_source(sender, subject)
    return {
        "gmail_message_id": message_id,
        "thread_id": payload.get("threadId", ""),
        "received_at_utc": received_utc,
        "received_date_et": dt.astimezone(ET).date().isoformat(),
        "sender": sender,
        "source": source_icon,  # "NYT" or "WSJ"
        "subject": subject,
        "tier": detect_tier(subject, body),
        "signal": newsletter_signal(subject, body),
        "n_chars": len(body),
        "body": body,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }


def insert(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO newsletters
           (gmail_message_id, thread_id, received_at_utc, received_date_et,
            sender, source, subject, tier, signal, n_chars, body, pulled_at)
           VALUES (:gmail_message_id, :thread_id, :received_at_utc,
                   :received_date_et, :sender, :source, :subject, :tier,
                   :signal, :n_chars, :body, :pulled_at)""",
        rec,
    )


# ----------------------------------------------------------------- report
def summarize(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """SELECT COUNT(*) n, MIN(received_date_et) lo, MAX(received_date_et) hi,
                  SUM(n_chars) chars FROM newsletters"""
    ).fetchone()
    if not row["n"]:
        print("corpus empty")
        return
    lo = datetime.fromisoformat(row["lo"]).date()
    hi = datetime.fromisoformat(row["hi"]).date()
    span_days = (hi - lo).days + 1
    print()
    print("=" * 62)
    print("CORPUS")
    print("=" * 62)
    print(f"  messages        {row['n']:,}")
    print(f"  date range      {row['lo']} -> {row['hi']}")
    print(f"  calendar span   {span_days} days ({span_days / 365.25:.2f} years)")
    print(f"  total text      {row['chars']:,} chars "
          f"(~{row['chars'] / 4 / 1000:,.0f}k tokens)")
    print(f"  msgs per day    {row['n'] / span_days:.1f}")
    print(f"  db              {DB_PATH}")

    print("\n  by source")
    for r in conn.execute(
        "SELECT source, COUNT(*) n FROM newsletters GROUP BY source ORDER BY n DESC"
    ):
        print(f"    {r['source'] or '?':<6} {r['n']:>6,}")

    print("\n  by signal (Pulse's macro-relevance heuristic)")
    for r in conn.execute(
        "SELECT signal, COUNT(*) n FROM newsletters GROUP BY signal ORDER BY n DESC"
    ):
        print(f"    {r['signal'] or '?':<6} {r['n']:>6,}")

    print("\n  by tier")
    for r in conn.execute(
        "SELECT tier, COUNT(*) n FROM newsletters GROUP BY tier ORDER BY n DESC"
    ):
        print(f"    {r['tier'] or '?':<14} {r['n']:>6,}")

    print("\n  coverage by month")
    for r in conn.execute(
        """SELECT substr(received_date_et,1,7) m, COUNT(*) n,
                  COUNT(DISTINCT received_date_et) days
           FROM newsletters GROUP BY m ORDER BY m"""
    ):
        print(f"    {r['m']}   {r['n']:>5,} msgs   {r['days']:>2} days covered")


# ----------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true",
                    help="count matching messages and exit, no bodies fetched")
    ap.add_argument("--limit", type=int, default=0,
                    help="fetch at most N new messages this run (0 = all)")
    ap.add_argument("--query", default=QUERY, help="Gmail search query")
    args = ap.parse_args()

    print(f"authenticating (Pulse token at {PULSE_BACKEND})...")
    service = build("gmail", "v1", credentials=get_gmail_credentials())

    print(f"listing messages matching: {args.query}")
    ids = list_all_ids(service, args.query)
    print(f"  {len(ids):,} messages match\n")

    if args.count:
        return

    conn = open_db(DB_PATH)
    have = stored_ids(conn)
    todo = [i for i in ids if i not in have]
    if args.limit:
        todo = todo[: args.limit]
    print(f"already stored: {len(have):,}   to fetch now: {len(todo):,}\n")

    added = skipped = 0
    for n, mid in enumerate(todo, 1):
        try:
            rec = fetch_one(service, mid)
        except HttpError as exc:
            print(f"  [{n}/{len(todo)}] {mid} FAILED: {exc}", flush=True)
            continue
        if rec is None:
            skipped += 1
        else:
            insert(conn, rec)
            added += 1
        if n % 50 == 0 or n == len(todo):
            conn.commit()
            print(f"  [{n}/{len(todo)}] added={added} empty={skipped}", flush=True)
    conn.commit()

    print(f"\ndone. added {added}, skipped {skipped} empty-body messages.")
    summarize(conn)
    conn.close()


if __name__ == "__main__":
    main()
