from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

# curl_cffi presents a full Chrome TLS fingerprint (impersonate="chrome"),
# which clears the bot walls that 403 plain python-requests (both Citadel
# career sites). API-compatible with requests for our get/raise_for_status use.
from curl_cffi import requests

from ..db import get_connection
from .gmail import send_email
from .summaries import _call_anthropic

# ── Watchlist ────────────────────────────────────────────────────────────────
# Hand-curated. Edit this list to add/remove programs; rows are upserted into
# the program_watch table on every run. watch_hint gives the LLM judge context
# on what "relevant" means for this page.

WATCHLIST: list[dict[str, str]] = [
    {
        "name": "Jane Street INSIGHT",
        "url": "https://www.janestreet.com/join-jane-street/programs-and-events/insight/",
        "watch_hint": "First/second-year student program. Watch for applications opening and deadlines.",
    },
    {
        "name": "IMC Prosperity",
        "url": "https://prosperity.imc.com/",
        "watch_hint": "Global trading competition. Watch for registration opening and event dates.",
    },
    {
        "name": "WorldQuant International Quant Championship",
        "url": "https://www.worldquant.com/international-quant-championship/",
        "watch_hint": "Alpha-research competition. Watch for registration opening and stage dates.",
    },
    {
        "name": "Citadel Datathon (Correlation One)",
        "url": "https://www.citadel.com/careers/students/programs-events/",
        "watch_hint": "Datathon and student programs page. Watch for new datathon dates or applications opening.",
    },
    {
        "name": "Citadel Securities student programs",
        "url": "https://www.citadelsecurities.com/careers/students/",
        "watch_hint": "Student programs. Watch for new programs, datathons, or applications opening.",
    },
    {
        "name": "SIG student programs",
        "url": "https://sig.com/campus-programs/",
        "watch_hint": "Campus programs (discovery days, internships). Watch for applications opening.",
    },
    {
        "name": "Optiver student opportunities",
        "url": "https://optiver.com/working-at-optiver/career-opportunities/?level=internship",
        "watch_hint": "Internships/insight programs. Watch for new US-eligible postings for students.",
    },
]

# Standing reminders appended to every alert email that goes out. Edit freely;
# delete an entry once it's done. These do NOT trigger an email on their own.
REMINDERS: list[str] = [
    'Sign up for the Jane Street INSIGHT notification form: '
    '<a href="https://docs.google.com/forms/d/e/1FAIpQLSeRZazvwrEEnRlC6KTZP2-lQQPH_A7mpSzjKo2PPk8V5tNmgQ/viewform">Google Form</a>',
    "Create a WorldQuant BRAIN account when the next IQC cycle is announced (worldquant.com/international-quant-championship)",
    "Consider talent-community profiles at SIG (careers.sig.com), Optiver, and Citadel when application season nears",
]

_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def fetch_page(url: str, timeout: int = 30) -> tuple[str, str]:
    """Fetch a URL impersonating a real Chrome browser (TLS fingerprint + UA).
    Returns (html, "") on success or ("", error_message) on failure."""
    try:
        resp = requests.get(url, impersonate="chrome", timeout=timeout)
        resp.raise_for_status()
        return resp.text, ""
    except Exception as exc:
        return "", str(exc)


def extract_main_text(html: str) -> str:
    """Strip scripts/styles/tags and collapse whitespace. Deliberately simple:
    the hash-diff + LLM judge tolerate boilerplate; they only need the page's
    visible text to be stable run-to-run."""
    text = _STYLE_RE.sub(" ", html or "")
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_JUDGE_SYSTEM = (
    "You monitor university-program and competition webpages for a student. "
    "You are given the previous text of a page and the new text. Decide whether "
    "the change is RELEVANT: applications/registration opened, a deadline or "
    "cycle date was announced or changed, or a new edition of the program was "
    "announced. Cosmetic edits, marketing copy, staff quotes, or footer changes "
    "are NOT relevant. Respond with ONLY a JSON object: "
    '{"relevant": true/false, "what_changed": "<one sentence>"}'
)


def judge_change(old_text: str, new_text: str, watch_hint: str) -> dict[str, Any]:
    """Ask the LLM whether a page change means application status changed.
    Conservative on failure: an unclassifiable change is treated as relevant."""
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    # Cap each side so two big pages can't blow the prompt budget.
    user = (
        f"WATCH HINT: {watch_hint}\n\n"
        f"PREVIOUS PAGE TEXT:\n{old_text[:6000]}\n\n"
        f"NEW PAGE TEXT:\n{new_text[:6000]}"
    )
    try:
        raw = _call_anthropic(_JUDGE_SYSTEM, user, model, max_tokens=200)
        if raw:
            start, end = raw.index("{"), raw.rindex("}") + 1
            parsed = json.loads(raw[start:end])
            return {
                "relevant": bool(parsed.get("relevant")),
                "what_changed": str(parsed.get("what_changed", "")).strip(),
            }
    except Exception:
        pass
    return {
        "relevant": True,
        "what_changed": "Page changed but could not classify the change — check manually.",
    }


def sync_watchlist() -> None:
    """Upsert WATCHLIST entries into program_watch. Rows whose URL left the
    list are deleted (the list is the single source of truth)."""
    urls = [e["url"] for e in WATCHLIST]
    with get_connection() as conn:
        for entry in WATCHLIST:
            conn.execute(
                "INSERT OR IGNORE INTO program_watch (name, url) VALUES (?, ?)",
                (entry["name"], entry["url"]),
            )
            conn.execute(
                "UPDATE program_watch SET name=? WHERE url=?",
                (entry["name"], entry["url"]),
            )
        if urls:
            placeholders = ",".join("?" for _ in urls)
            conn.execute(
                f"DELETE FROM program_watch WHERE url NOT IN ({placeholders})", urls
            )


def run_watch(advance_snapshots: bool = True) -> dict[str, Any]:
    """One full watch pass. Returns a digest dict:
    {"updates": [{name,url,what_changed}], "errors": [{name,url,error}]}.

    advance_snapshots: when True (default), a successful fetch/diff advances
    last_hash/last_text so the same change never re-alerts. When False
    (preview mode for the dry-run route), detection still runs and the digest
    still reports what changed, but last_hash/last_text are left untouched —
    a midweek dry run must not consume the change the next scheduled run is
    supposed to alert on. Health bookkeeping (last_checked_at/last_status/
    last_error) is updated either way. The error path is unaffected."""
    sync_watchlist()
    hints = {e["url"]: e["watch_hint"] for e in WATCHLIST}
    updates: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, url, last_hash, last_text FROM program_watch"
        ).fetchall()

    for row in rows:
        html, err = fetch_page(row["url"])
        if err:
            errors.append({"name": row["name"], "url": row["url"], "error": err})
            with get_connection() as conn:
                conn.execute(
                    "UPDATE program_watch SET last_checked_at=?, last_status='error', last_error=? WHERE id=?",
                    (now, err, row["id"]),
                )
            continue

        new_text = extract_main_text(html)
        new_hash = text_hash(new_text)

        if row["last_hash"] and new_hash != row["last_hash"]:
            verdict = judge_change(
                row["last_text"] or "", new_text, hints.get(row["url"], "")
            )
            if verdict["relevant"]:
                updates.append(
                    {"name": row["name"], "url": row["url"],
                     "what_changed": verdict["what_changed"]}
                )
        # First run (no last_hash) or unchanged: just snapshot quietly.

        with get_connection() as conn:
            if advance_snapshots:
                conn.execute(
                    "UPDATE program_watch SET last_hash=?, last_text=?, "
                    "last_checked_at=?, last_status='ok', last_error='' WHERE id=?",
                    (new_hash, new_text, now, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE program_watch SET last_checked_at=?, "
                    "last_status='ok', last_error='' WHERE id=?",
                    (now, row["id"]),
                )

    return {"updates": updates, "errors": errors}


def build_digest_html(
    updates: list[dict[str, str]],
    errors: list[dict[str, str]],
    reminders: list[str] | None = None,
) -> str:
    parts: list[str] = ["<h2>Program watch: weekly check</h2>"]
    if updates:
        parts.append("<h3>Updates</h3><ul>")
        for u in updates:
            parts.append(
                f"<li><strong>{u['name']}</strong>: {u['what_changed']} "
                f"(<a href=\"{u['url']}\">{u['url']}</a>)</li>"
            )
        parts.append("</ul>")
    if errors:
        parts.append("<h3>Couldn't check (look manually)</h3><ul>")
        for e in errors:
            parts.append(
                f"<li><strong>{e['name']}</strong>: {e['error']} "
                f"(<a href=\"{e['url']}\">{e['url']}</a>)</li>"
            )
        parts.append("</ul>")
    if reminders:
        parts.append("<h3>Reminders</h3><ul>")
        for r in reminders:
            parts.append(f"<li>{r}</li>")
        parts.append("</ul>")
    parts.append("<p style='color:#888'>Sent by Pulse's program deadline watcher.</p>")
    return "".join(parts)


def run_and_alert() -> dict[str, Any]:
    """Weekly entrypoint: run the watch, email only if something needs eyes."""
    digest = run_watch()
    updates, errors = digest["updates"], digest["errors"]
    if not updates and not errors:
        print("[deadlines] nothing new, no email", flush=True)
        return {"sent": False, **digest}
    n = len(updates)
    subject = f"Pulse: {n} program update{'s' if n != 1 else ''}" if updates \
        else "Pulse program watch: some pages couldn't be checked"
    try:
        send_email(subject, build_digest_html(updates, errors, reminders=REMINDERS))
        print(f"[deadlines] alert sent: {subject}", flush=True)
        return {"sent": True, **digest}
    except Exception as exc:
        print(f"[deadlines] email send FAILED: {exc}", flush=True)
        return {"sent": False, "send_error": str(exc), **digest}
