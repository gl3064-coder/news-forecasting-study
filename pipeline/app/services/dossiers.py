"""Dossier service: entity upsert, mention recording, follow/discover.

This file is the pure data layer. Snapshot generation (LLM call) lives in
`regenerate_snapshot()` further down — kept in the same file because it
operates on the same tables. The HTTP layer in `main.py` calls these
functions directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection  # reuse the existing sqlite3 connection helper


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Entity / mention CRUD ----------

def upsert_entity(kind: str, key: str, name: str) -> int:
    """Insert if missing, return the entity id either way.

    Case-insensitive on `key` — "OpenAI" and "openai" collapse to the same
    entity so we don't end up with the same company in both Followed and
    Discover. If a case-different row already exists, we reuse it (and
    refresh its display name) rather than creating a duplicate.
    """
    assert kind in {"company", "sector", "concept"}, f"bad kind: {kind}"
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM entities WHERE kind = ? AND LOWER(key) = LOWER(?)",
            (kind, key),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE entities SET name = ? WHERE id = ?",
                (name, int(existing[0])),
            )
            return int(existing[0])
        cur = conn.execute(
            "INSERT INTO entities(kind, key, name, created_at) VALUES (?,?,?,?) "
            "RETURNING id",
            (kind, key, name, _now_iso()),
        )
        row = cur.fetchone()
        return int(row[0])


def get_entity_id(kind: str, key: str) -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM entities WHERE kind=? AND LOWER(key) = LOWER(?)",
            (kind, key),
        ).fetchone()
    return int(row[0]) if row else None


def record_mention(entity_id: int, newsletter_id: str,
                   quote: str = "", confidence: float = 1.0,
                   bullets: list[str] | None = None,
                   newsletter_subject: str | None = None) -> None:
    """Insert a mention row.

    `bullets` semantics:
      None       → store SQL NULL (legacy / not yet generated; UI falls back to quote)
      []         → store "[]"     (explicitly: entity was a passing reference, no body)
      [str, ...] → store json.dumps(bullets)

    `newsletter_subject`: if not provided, the subject is looked up from the
    newsletters table so the title survives the 7-day purge. If the newsletter
    isn't in the table (already purged at write time — unlikely), stays NULL
    and the UI shows the generic "(archived newsletter)" label.
    """
    bullets_json = None if bullets is None else json.dumps(bullets)
    with get_connection() as conn:
        if newsletter_subject is None:
            row = conn.execute(
                "SELECT subject FROM newsletters WHERE gmail_message_id = ?",
                (newsletter_id,),
            ).fetchone()
            newsletter_subject = row[0] if row else None
        # INSERT OR IGNORE so a re-processed newsletter doesn't create a
        # duplicate mention row for the same (entity_id, newsletter_id).
        # Requires the unique index created by dedupe_mentions_and_purge_test_data
        # (or by init_db on fresh DBs).
        conn.execute(
            "INSERT OR IGNORE INTO mentions(entity_id, newsletter_id, quote, "
            "confidence, tagged_at, bullets_json, newsletter_subject) "
            "VALUES (?,?,?,?,?,?,?)",
            (entity_id, newsletter_id, quote, confidence, _now_iso(),
             bullets_json, newsletter_subject),
        )


def list_mentions(entity_id: int, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """Returns a page of mentions joined with newsletter metadata.

    **Sort order:** rows with substantive bullets come first (so the top of
    the list always has real content), then by recency. This keeps "Show
    more" pages weighted toward content rather than chronology of weak hits.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.newsletter_id, m.quote, m.confidence, m.tagged_at,
                   n.subject, n.source, n.source_icon, n.received_at,
                   m.bullets_json, m.newsletter_subject
            FROM mentions m
            LEFT JOIN newsletters n ON n.gmail_message_id = m.newsletter_id
            WHERE m.entity_id = ?
            ORDER BY
              CASE
                WHEN m.bullets_json IS NOT NULL AND m.bullets_json != '[]' THEN 0
                ELSE 1
              END,
              COALESCE(n.received_at, m.tagged_at) DESC
            LIMIT ? OFFSET ?
            """,
            (entity_id, limit, offset),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        bullets_raw = r[9]
        bullets: list[str] | None
        if bullets_raw is None:
            bullets = None
        else:
            try:
                parsed = json.loads(bullets_raw)
                bullets = [str(b) for b in parsed if isinstance(b, str)] if isinstance(parsed, list) else None
            except Exception:
                bullets = None
        out.append({
            "id": r[0],
            "newsletter_id": r[1],
            "quote": r[2],
            "confidence": r[3],
            "tagged_at": r[4],
            # `newsletter_title` = live JOIN (None when source row purged)
            "newsletter_title": r[5],
            "newsletter_source": r[6],
            "newsletter_source_icon": r[7],
            "newsletter_received_at": r[8],
            "bullets": bullets,
            # `newsletter_subject` = persisted at mention write-time so the
            # title survives the 7-day purge of `newsletters`.
            "newsletter_subject": r[10],
        })
    return out


def count_mentions(entity_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM mentions WHERE entity_id=?", (entity_id,)
        ).fetchone()
    return int(row[0]) if row else 0


# ---------- Follow / discover ----------

def follow(entity_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE entities SET followed=1 WHERE id=?", (entity_id,))


def unfollow(entity_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE entities SET followed=0 WHERE id=?", (entity_id,))


def is_followed(entity_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT followed FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
    return bool(row and row[0])


def list_followed() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT e.id, e.kind, e.key, e.name,
                   COUNT(m.id) as mention_count,
                   MAX(m.tagged_at) as last_mention
            FROM entities e
            LEFT JOIN mentions m ON m.entity_id = e.id
            WHERE e.followed = 1
            GROUP BY e.id
            ORDER BY (last_mention IS NULL), last_mention DESC, e.name ASC
        """).fetchall()
    return [
        {"id": r[0], "kind": r[1], "key": r[2], "name": r[3],
         "mention_count": r[4], "last_mention": r[5]} for r in rows
    ]


def list_discover(min_mentions: int = 2, limit: int = 10) -> list[dict[str, Any]]:
    """Returns discover candidates with their POTENTIAL coverage count — the
    number of newsletters whose body contains the entity's name or key (via
    substring scan), not just the count of newsletters Haiku decided were
    substantive enough to record as an entity mention.

    This way Discover shows the true reach of a topic. If the user follows
    it, `backfill_mentions_for_entity` will create real mention rows for the
    same set.
    """
    with get_connection() as conn:
        # Base candidates: entities with ≥ min_mentions actual entity mentions
        base_rows = conn.execute("""
            SELECT e.id, e.kind, e.key, e.name, COUNT(m.id) AS cnt
            FROM entities e
            JOIN mentions m ON m.entity_id = e.id
            WHERE e.followed = 0
            GROUP BY e.id
            HAVING cnt >= ?
            ORDER BY cnt DESC
            LIMIT ?
        """, (min_mentions, limit * 2)).fetchall()

        # Pre-load all newsletter bodies once for the substring scan
        nl_rows = conn.execute(
            "SELECT gmail_message_id, subject, full_content, summary, description "
            "FROM newsletters"
        ).fetchall()
        nl_haystacks = [
            (r[0], " ".join(s or "" for s in (r[1], r[2], r[3], r[4])).lower())
            for r in nl_rows
        ]

    out: list[dict[str, Any]] = []
    for r in base_rows:
        eid, kind, key, name, entity_mention_count = r
        # Substring scan across newsletter cache
        aliases = {(name or "").lower().strip(), (key or "").lower().strip()}
        aliases.discard("")
        matched_newsletters = sum(
            1 for _nl_id, hay in nl_haystacks
            if any(a in hay for a in aliases)
        )
        # True coverage = max(entity mentions, substring matches). Both upper-
        # bound by total newsletter count; substring is the broader number.
        true_count = max(entity_mention_count, matched_newsletters)
        out.append({
            "id": eid, "kind": kind, "key": key, "name": name,
            "mention_count": true_count,
            "entity_mention_count": entity_mention_count,
        })
    # Re-sort by the new true_count
    out.sort(key=lambda x: x["mention_count"], reverse=True)
    return out[:limit]


# ---------- Post-processing entry point ----------

def process_entities(newsletter_id: str, extracted: list[dict[str, Any]]) -> None:
    """Called by summarize_story right after a Haiku call returns.
    Upserts each entity (followed=0 by default) and records the mention.
    Bullets come from the same Haiku call (per-entity, dossier-relevant);
    `quote` is accepted for backwards-compat with older extractor payloads.
    """
    for e in extracted:
        kind = e.get("kind")
        key = (e.get("key") or "").strip()
        name = (e.get("name") or key).strip()
        quote = (e.get("quote") or "").strip()
        raw_bullets = e.get("bullets")
        bullets: list[str] | None
        if isinstance(raw_bullets, list):
            bullets = [str(b).strip() for b in raw_bullets if isinstance(b, str) and str(b).strip()][:3]
        else:
            bullets = None
        if kind not in {"company", "sector", "concept"} or not key:
            continue
        eid = upsert_entity(kind, key, name)
        record_mention(eid, newsletter_id, quote=quote, bullets=bullets)


# ---------- Snapshot generation ----------

import os
from datetime import timedelta

from .summaries import _call_anthropic, _extract_json


MENTION_BULLETS_SYSTEM_PROMPT = (
    "You write dossier-relevant bullets for Gavin — NYU Stern freshman tracking "
    "financial news. Given a newsletter and one entity (company / sector / concept), "
    "return 0-3 bullets describing what THIS newsletter says about THAT entity.\n"
    "  • Each bullet ≤25 words, scannable, contains a name / number / date / "
    "quoted phrase from the newsletter.\n"
    "  • Forbidden: generic statements like \"China risk\", \"AI bellwether\", "
    "\"Capex up\", \"regulatory headwinds\".\n"
    "  • If the entity is only a passing reference and you cannot produce "
    "grounded bullets, return {\"bullets\": []}.\n"
    "  • Respond with ONLY raw JSON. No markdown fences."
)


import time as _time


def _call_anthropic_with_429_retry(system: str, user: str, model: str,
                                   max_tokens: int = 400) -> str | None:
    """Wrap _call_anthropic with 429-aware backoff. Mirrors the pattern used in
    _call_openai_compat (summaries.py): sleeps before retrying when the upstream
    raises a 429. Other exceptions propagate immediately."""
    last_exc: Exception | None = None
    for wait in (0, 5, 15, 30, 60):
        if wait:
            _time.sleep(wait)
        try:
            return _call_anthropic(system, user, model=model, max_tokens=max_tokens)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "rate" in msg.lower():
                last_exc = exc
                continue
            raise
    if last_exc is not None:
        print(f"[dossiers] mention-bullets giving up after retries: {last_exc}", flush=True)
    return None


def _generate_mention_bullets(entity: dict[str, Any],
                              newsletter: dict[str, Any],
                              model_override: str | None = None) -> list[str] | None:
    """One LLM call returning a list of dossier-specific bullets for this
    (newsletter, entity) pair. Defaults to Haiku via PULSE_SUMMARY_MODEL; the
    Sonnet re-pass passes `model_override=PULSE_ANALYSIS_MODEL`.
    Returns None on any failure (caller decides whether to leave bullets_json
    NULL or store an empty list).
    """
    body = (newsletter.get("full_content") or newsletter.get("summary") or "")[:3000]
    user = (
        f"Entity kind: {entity['kind']}\n"
        f"Entity name: {entity['name']} (key: {entity['key']})\n"
        f"Newsletter subject: {newsletter.get('subject') or ''}\n"
        f"Newsletter source: {newsletter.get('source') or ''}\n"
        f"Newsletter content (first ~3000 chars):\n{body}\n\n"
        'Return: {"bullets": ["...", "..."]}'
    )
    model = model_override or os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    try:
        text = _call_anthropic_with_429_retry(MENTION_BULLETS_SYSTEM_PROMPT, user, model=model, max_tokens=400)
    except Exception as exc:
        print(f"[dossiers] mention-bullets LLM failed: {exc}", flush=True)
        return None
    if not text:
        return None
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("bullets")
    if not isinstance(raw, list):
        return None
    return [str(b).strip() for b in raw if isinstance(b, str) and str(b).strip()][:3]


def dedupe_same_name_entities() -> dict[str, Any]:
    """Collapse taxonomy-fragmented entities — same kind + same normalized
    display name, different keys. Caused by the Haiku extractor inventing
    multiple tickers/slugs for the same company (e.g. Cerebras = CEREBRAS +
    CREBR + CBRS, or Adani Group = ADANIGROUP + adani_group).

    For each (kind, normalized_name) group with >1 row: pick winner (followed
    > most mentions > prettiest key), reassign mentions to winner (UPDATE OR
    IGNORE respects the unique index), delete losers.
    """
    import re
    def _normalize_name(name: str) -> str:
        s = (name or "").lower()
        s = re.sub(r"[\s\.,'\-\(\)/&]+", "", s)  # strip whitespace + common punctuation
        return s

    def _key_score(key: str) -> int:
        if not key:
            return 0
        if any(c.isupper() for c in key) and any(c.islower() for c in key):
            return 3  # mixed-case wins (e.g. "OpenAI")
        if key.isupper() and 2 <= len(key) <= 5 and key.isalnum():
            return 2  # short ALL-CAPS = ticker (NVDA, JPM)
        if key.islower() and "_" in key:
            return 1  # slug-style
        return 0

    with get_connection() as conn:
        all_rows = conn.execute(
            "SELECT id, kind, key, name, followed FROM entities WHERE name IS NOT NULL"
        ).fetchall()
    # Bucket by (kind, normalized_name)
    buckets: dict[tuple, list] = {}
    for r in all_rows:
        eid, kind, key, name, followed = r[0], r[1], r[2], r[3], r[4]
        norm = _normalize_name(name)
        if not norm:
            continue
        buckets.setdefault((kind, norm), []).append((eid, key, name, followed))

    merged_count = 0
    mentions_reassigned = 0
    losers_deleted = 0
    with get_connection() as conn:
        for (kind, norm), candidates in buckets.items():
            if len(candidates) < 2:
                continue
            # Score each candidate
            scored = []
            for c in candidates:
                eid = c[0]
                mc = conn.execute(
                    "SELECT COUNT(*) FROM mentions WHERE entity_id = ?", (eid,)
                ).fetchone()[0]
                scored.append((eid, c[1], c[2], c[3], mc, _key_score(c[1])))
            # Sort: followed DESC, mentions DESC, key_score DESC
            scored.sort(key=lambda t: (t[3], t[4], t[5]), reverse=True)
            winner_id = scored[0][0]
            for loser in scored[1:]:
                loser_id = loser[0]
                cursor = conn.execute(
                    "UPDATE OR IGNORE mentions SET entity_id = ? WHERE entity_id = ?",
                    (winner_id, loser_id),
                )
                mentions_reassigned += cursor.rowcount or 0
                conn.execute("DELETE FROM mentions WHERE entity_id = ?", (loser_id,))
                conn.execute("DELETE FROM dossiers WHERE entity_id = ?", (loser_id,))
                conn.execute("DELETE FROM entities WHERE id = ?", (loser_id,))
                losers_deleted += 1
            merged_count += 1
    print(f"[dossiers] same-name dedupe: merged {merged_count} groups, "
          f"reassigned {mentions_reassigned} mentions, deleted {losers_deleted} entities", flush=True)
    return {
        "groups_merged": merged_count,
        "mentions_reassigned": mentions_reassigned,
        "losers_deleted": losers_deleted,
    }


def dedupe_case_collision_entities() -> dict[str, Any]:
    """One-shot cleanup for case-collision entities (e.g. 'OpenAI' and 'openai'
    existing as separate rows). For each (kind, LOWER(key)) group with >1 row:

      • Pick a winner: prefer followed=1, then most mentions, then nicest casing
        (mixed > all-upper > all-lower).
      • Re-point all mentions from losers to the winner (INSERT OR IGNORE so we
        respect the unique index we already have).
      • Drop any duplicate-key mentions left on the loser (already merged).
      • Drop the loser's dossier snapshot row (winner keeps its own).
      • Update the winner's display name to the loser's if the winner had an
        ugly all-caps slug.
      • Delete the loser entity.

    Returns counts so the admin route can show what changed.
    """
    def _casing_score(key: str) -> int:
        """Higher is better. Mixed > all-lower > all-upper for company tickers,
        but tickers like NVDA SHOULD stay uppercase. Heuristic: prefer the casing
        that matches the display name if available; otherwise prefer mixed."""
        if not key:
            return 0
        if any(c.isupper() for c in key) and any(c.islower() for c in key):
            return 3  # mixed case wins (e.g. "OpenAI")
        if key.isupper() and len(key) <= 5:
            return 2  # short ALL-CAPS = ticker convention (NVDA, JPM)
        if key.islower():
            return 1  # slug-style (ai_chips)
        return 0  # ugly ALL_CAPS_SLUG

    with get_connection() as conn:
        groups = conn.execute(
            """
            SELECT kind, LOWER(key) AS lkey, COUNT(*) AS n
            FROM entities
            GROUP BY kind, LOWER(key)
            HAVING n > 1
            """
        ).fetchall()
        merged_count = 0
        mentions_reassigned = 0
        losers_deleted = 0
        for kind, lkey, _n in groups:
            candidates = conn.execute(
                "SELECT id, key, name, followed FROM entities "
                "WHERE kind = ? AND LOWER(key) = ?",
                (kind, lkey),
            ).fetchall()
            # Mention counts per candidate
            scored: list[tuple] = []
            for c in candidates:
                eid, ekey, ename, followed = c[0], c[1], c[2], c[3]
                mc = conn.execute(
                    "SELECT COUNT(*) FROM mentions WHERE entity_id = ?", (eid,)
                ).fetchone()[0]
                scored.append((eid, ekey, ename, followed, mc))
            # Sort: followed DESC, mention_count DESC, casing_score DESC
            scored.sort(key=lambda t: (t[3], t[4], _casing_score(t[1])), reverse=True)
            winner = scored[0]
            losers = scored[1:]
            for loser in losers:
                loser_id = loser[0]
                # Re-point mentions. UNIQUE INDEX on (entity_id, newsletter_id)
                # means we use INSERT OR IGNORE semantics via UPDATE OR IGNORE.
                # Any conflict (newsletter already in winner) is silently dropped.
                cursor = conn.execute(
                    "UPDATE OR IGNORE mentions SET entity_id = ? WHERE entity_id = ?",
                    (winner[0], loser_id),
                )
                mentions_reassigned += cursor.rowcount or 0
                # Drop any leftover dupe rows that couldn't merge (winner already had that newsletter)
                conn.execute("DELETE FROM mentions WHERE entity_id = ?", (loser_id,))
                # Drop the loser's dossier snapshot
                conn.execute("DELETE FROM dossiers WHERE entity_id = ?", (loser_id,))
                # Delete the loser entity
                conn.execute("DELETE FROM entities WHERE id = ?", (loser_id,))
                losers_deleted += 1
            merged_count += 1
    print(f"[dossiers] entity-case dedupe: merged {merged_count} groups, "
          f"reassigned {mentions_reassigned} mentions, deleted {losers_deleted} loser entities", flush=True)
    return {
        "groups_merged": merged_count,
        "mentions_reassigned": mentions_reassigned,
        "losers_deleted": losers_deleted,
    }


def dedupe_mentions_and_purge_test_data() -> dict[str, Any]:
    """Clean up two issues that accumulated before the UNIQUE constraint:

    1. Test pollution — mention rows created by pytest leaking into prod db
       (newsletter_ids like 'e2e_1', 'm1', 'msg_abc' that never matched a real
       Gmail purge). Heuristic: real Gmail message IDs are >=15 hex chars.
    2. True duplicates of (entity_id, newsletter_id) — same newsletter
       processed multiple times. Keep the best row per group.

    After cleaning, creates the UNIQUE INDEX so future writes are protected.
    """
    import re
    GMAIL_ID = re.compile(r"^[0-9a-fA-F]{15,}$")

    with get_connection() as conn:
        # Pass 1: purge test pollution
        rows = conn.execute("SELECT id, newsletter_id FROM mentions").fetchall()
        test_ids = [r[0] for r in rows if not GMAIL_ID.match(r[1] or "")]
        test_purged = 0
        if test_ids:
            # Chunk to keep the IN-list manageable
            for i in range(0, len(test_ids), 500):
                chunk = test_ids[i:i+500]
                conn.execute(
                    f"DELETE FROM mentions WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
            test_purged = len(test_ids)

        # Pass 2: dedupe — find groups with >1 row, keep best
        groups = conn.execute(
            """
            SELECT entity_id, newsletter_id, COUNT(*) AS n
            FROM mentions
            GROUP BY entity_id, newsletter_id
            HAVING n > 1
            """
        ).fetchall()
        deduped = 0
        for entity_id, newsletter_id, _n in groups:
            candidates = conn.execute(
                "SELECT id, bullets_json, tagged_at FROM mentions "
                "WHERE entity_id = ? AND newsletter_id = ?",
                (entity_id, newsletter_id),
            ).fetchall()

            def _score(row: tuple) -> tuple:
                _id, bj, ts = row
                has_bullets = bj is not None
                non_empty = bj is not None and bj != "[]"
                return (has_bullets, non_empty, ts or "")

            candidates.sort(key=_score, reverse=True)
            drop_ids = [c[0] for c in candidates[1:]]
            if drop_ids:
                conn.execute(
                    f"DELETE FROM mentions WHERE id IN ({','.join('?' * len(drop_ids))})",
                    drop_ids,
                )
                deduped += len(drop_ids)

        # Pass 3: create the unique index now that the table is clean
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_mentions_entity_newsletter_uniq "
                "ON mentions(entity_id, newsletter_id)"
            )
            index_created = True
        except Exception as exc:
            print(f"[dossiers] unique index create failed: {exc}", flush=True)
            index_created = False

    print(f"[dossiers] dedupe: test_purged={test_purged} duplicates_removed={deduped} "
          f"unique_index_active={index_created}", flush=True)
    return {
        "test_purged": test_purged,
        "duplicates_removed": deduped,
        "unique_index_active": index_created,
    }


def delete_titleless_archived_mentions() -> dict[str, Any]:
    """Delete mention rows whose source newsletter is no longer in the
    newsletters table AND has no persisted newsletter_subject. These rows
    can only ever render as "(archived newsletter)" with no provenance, so
    they're not informative.

    New mentions don't fall into this state — record_mention now persists
    the subject at write time — so this is a one-shot cleanup for legacy
    rows created before that column existed.
    """
    with get_connection() as conn:
        by_entity = conn.execute(
            """
            SELECT m.entity_id, e.kind, e.key, e.name, COUNT(*) AS n
            FROM mentions m
            JOIN entities e ON e.id = m.entity_id
            WHERE m.newsletter_subject IS NULL
              AND m.newsletter_id NOT IN (SELECT gmail_message_id FROM newsletters)
            GROUP BY m.entity_id
            """
        ).fetchall()
        deleted = conn.execute(
            """
            DELETE FROM mentions
            WHERE newsletter_subject IS NULL
              AND newsletter_id NOT IN (SELECT gmail_message_id FROM newsletters)
            """
        ).rowcount or 0
    per_entity = [
        {"entity_id": r[0], "kind": r[1], "key": r[2], "name": r[3], "deleted": r[4]}
        for r in by_entity
    ]
    print(f"[dossiers] delete_titleless_archived: removed {deleted} rows across {len(per_entity)} entities", flush=True)
    return {"total_deleted": deleted, "by_entity": per_entity}


def prune_empty_mentions() -> dict[str, Any]:
    """Delete mention rows whose bullets_json = '[]' (both passes agreed the
    entity was a passing reference). Returns per-entity counts of how many
    were dropped so the caller can decide which dossier snapshots to refresh.
    """
    with get_connection() as conn:
        by_entity = conn.execute(
            """
            SELECT m.entity_id, e.kind, e.key, e.name, COUNT(*) as n
            FROM mentions m
            JOIN entities e ON e.id = m.entity_id
            WHERE m.bullets_json = '[]'
            GROUP BY m.entity_id
            """
        ).fetchall()
        deleted_cur = conn.execute("DELETE FROM mentions WHERE bullets_json = '[]'")
        total = deleted_cur.rowcount or 0
    per_entity = [
        {"entity_id": r[0], "kind": r[1], "key": r[2], "name": r[3], "deleted": r[4]}
        for r in by_entity
    ]
    print(f"[dossiers] prune_empties: removed {total} rows across {len(per_entity)} entities", flush=True)
    return {"total_deleted": total, "by_entity": per_entity}


def reprocess_empty_bullets_with_sonnet(limit: int | None = None) -> dict[str, int]:
    """Second pass over mentions where bullets_json = '[]' (Haiku said
    'passing reference'). Re-ask Sonnet 4.6 with the same strict prompt —
    Sonnet has better judgment about what counts as grounded vs. generic.
    Upgrades the row if Sonnet returns content; leaves '[]' if still empty.
    """
    sonnet_model = os.getenv("PULSE_ANALYSIS_MODEL", "claude-sonnet-4-6")
    with get_connection() as conn:
        sql = (
            "SELECT m.id, m.entity_id, m.newsletter_id, "
            "       e.kind, e.key, e.name, "
            "       n.subject, n.full_content, n.summary, n.source "
            "FROM mentions m "
            "JOIN entities   e ON e.id = m.entity_id "
            "JOIN newsletters n ON n.gmail_message_id = m.newsletter_id "
            "WHERE m.bullets_json = '[]'"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
    total = len(rows)
    print(f"[dossiers] reprocess_empties: starting for {total} mentions (model={sonnet_model})", flush=True)
    upgraded = 0
    still_empty = 0
    skipped = 0
    for i, r in enumerate(rows, start=1):
        mention_id, _eid, _nl_id, kind, key, name, subject, full, summary, source = r
        entity = {"kind": kind, "key": key, "name": name}
        bullets = _generate_mention_bullets(
            entity,
            {"subject": subject, "full_content": full, "summary": summary, "source": source},
            model_override=sonnet_model,
        )
        if bullets is None:
            skipped += 1
            print(f"[dossiers] reprocess_empties [{i}/{total}] {kind}/{key}: skipped (LLM None)", flush=True)
            continue
        if bullets:
            upgraded += 1
            with get_connection() as conn:
                conn.execute("UPDATE mentions SET bullets_json=? WHERE id=?",
                             (json.dumps(bullets), mention_id))
            print(f"[dossiers] reprocess_empties [{i}/{total}] {kind}/{key}: UPGRADED -> {len(bullets)} bullets", flush=True)
        else:
            still_empty += 1
            print(f"[dossiers] reprocess_empties [{i}/{total}] {kind}/{key}: still empty", flush=True)
        _time.sleep(0.8)
    print(f"[dossiers] reprocess_empties: done considered={total} upgraded={upgraded} still_empty={still_empty} skipped={skipped}", flush=True)
    return {"considered": total, "upgraded": upgraded,
            "still_empty": still_empty, "skipped": skipped}


SNAPSHOT_SYSTEM_PROMPT = (
    "You are writing a structured dossier snapshot for Gavin — NYU Stern "
    "freshman who follows financial news. Given an entity (a company, sector, "
    "or finance concept) and the user's recent newsletter mentions of it, "
    "produce a SPECIFIC, EVIDENCE-GROUNDED snapshot. Cite numbers, names, "
    "dates from the mentions. If the mentions don't actually support a bull "
    "or bear case, say so plainly (e.g. \"No bull thesis surfaced in this "
    "week's coverage — recent mentions are factual/neutral.\"). Refuse to "
    "invent generic boilerplate like \"Capex up\" or \"China risk\". Plain "
    "English alongside the technical view. Respond with ONLY raw JSON — no "
    "markdown fences, no prose."
)


def _build_snapshot_prompt(entity: dict[str, Any], mentions: list[dict[str, Any]],
                           vault_block: str = "") -> str:
    if not mentions:
        mention_blocks = "(no mentions yet — write the overview from general knowledge and explicitly note that bull/bear theses cannot be sourced)"
    else:
        mention_blocks = "\n".join(
            f"- [{(m.get('newsletter_received_at') or m.get('tagged_at') or '')[:10]}] "
            f"{(m.get('newsletter_title') or 'untitled')} ({m.get('newsletter_source') or 'unknown'}): "
            f"{m.get('quote','')[:400]}"
            for m in mentions
        )
    prefix = ""
    if vault_block:
        prefix = (
            vault_block
            + "\n\nNote: when the dossier overview/bull/bear theses touch a concept the "
              "user has written about (one of the titles above), name the concept inline "
              "in your prose — do NOT use [[bracketed wiki syntax]] in the output. Write "
              "smooth editorial prose that naturally references the concept.\n\n"
        )
    return (
        prefix
        + f'Entity kind: {entity["kind"]}\n'
        + f'Entity name: {entity["name"]}\n'
        + f'Entity key: {entity["key"]}\n\n'
        + f"Newsletter mentions ({len(mentions)} total, most recent first):\n{mention_blocks}\n\n"
        "Quality bar (read this twice):\n"
        "  • EVERY bullet must contain at least one specific name, number, date, or quoted phrase from the mentions above.\n"
        "  • Generic statements like \"Capex up\", \"China risk\", \"AI bellwether\" are FORBIDDEN — they tell the user nothing.\n"
        "  • If a thesis cannot be grounded in the mentions, write the field as \"Insufficient coverage this period — no specific [bull/bear] catalyst surfaced in the cited mentions.\"\n"
        "  • Pick 1-2 actual quoted phrases from the mentions for notable_quotes (include the newsletter_id reference).\n\n"
        "Return JSON with this exact shape:\n"
        "{\n"
        '  "overview":      "3-4 sentences. What this entity IS and the current narrative around it as of the cited mentions. Name companies, numbers, dates.",\n'
        '  "plain_english": "1-2 sentence layman version, no jargon",\n'
        '  "bull_thesis":   "Specific positive case grounded in the mentions. Cite numbers/quotes. Or the insufficiency disclaimer.",\n'
        '  "bear_thesis":   "Specific negative case grounded in the mentions. Cite numbers/quotes. Or the insufficiency disclaimer.",\n'
        '  "topic_tags":    ["AI/Tech", "Corporate", ...],\n'
        '  "key_themes":    [{"theme":"specific theme", "evidence_count": 0}, ...],\n'
        '  "notable_quotes": [{"newsletter_id":"<copy from mention header>", "quote":"<actual phrase>", "date":"YYYY-MM-DD"}],\n'
        '  "jargon": [{"match":"EXACT substring as it appears in overview/bull_thesis/bear_thesis above", "plain":"1-sentence contextual definition for a Stern freshman"}, ...]\n'
        "}\n\n"
        "JARGON EXTRACTION RULES (read carefully — this is a learning aid for a finance beginner):\n"
        "  • Look at the overview / bull_thesis / bear_thesis you JUST wrote and extract 5-12 phrases a beginner would need explained.\n"
        "  • PRIORITIZE MULTI-WORD PHRASES over single words. Examples of what should be flagged:\n"
        "      \"a model-sharing agreement with the U.S. Commerce Department\"\n"
        "      \"failed to meet internal targets\"\n"
        "      \"a brief flutter through AI-related stocks\"\n"
        "      \"extending the United States' lead over China\"\n"
        "      \"pre-IPO employee liquidity\"\n"
        "      \"cleared a definitive path for an OpenAI IPO\"\n"
        "      \"underlying bid in yields\"\n"
        "      \"safe-haven bid\", \"transmission mechanism\", \"soft landing\".\n"
        "  • Also include relevant ACRONYMS that appear (IPO, M&A, FOMC, etc.).\n"
        "  • Each `match` MUST appear character-for-character somewhere in the overview / bull_thesis / bear_thesis fields you wrote.\n"
        "  • Each `plain` must explain what the PHRASE means in THIS dossier's context — not a generic dictionary entry.\n"
    )


def regenerate_snapshot(entity_id: int) -> dict[str, Any] | None:
    """Call Sonnet, store the snapshot, return the payload (or None on failure)."""
    with get_connection() as conn:
        erow = conn.execute(
            "SELECT id, kind, key, name FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
    if not erow:
        return None
    entity = {"id": erow[0], "kind": erow[1], "key": erow[2], "name": erow[3]}
    mentions = list_mentions(entity_id, limit=20)

    # Dossier snapshots moved from Sonnet to Haiku for cost. The mention grounding
    # + vault context carries most of the analytical weight; Haiku is sufficient
    # for the synthesis step. Gated by min_new_mentions/max_age so it fires rarely
    # anyway.
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    from ..vault import inject as vault_inject
    vault_block = vault_inject.for_sonnet(filter_entities=[entity["name"], entity["key"]])
    prompt = _build_snapshot_prompt(entity, mentions, vault_block=vault_block)
    try:
        text = _call_anthropic(SNAPSHOT_SYSTEM_PROMPT, prompt, model=model, max_tokens=2600)
    except Exception as exc:
        print(f"[dossiers] snapshot LLM failed: {exc}", flush=True)
        return None
    if not text:
        return None
    parsed = _extract_json(text)
    if not parsed:
        print(f"[dossiers] snapshot JSON parse failed; head={text[:120]!r}", flush=True)
        return None
    parsed["updated_at"] = _now_iso()
    total_mentions = count_mentions(entity_id)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO dossiers(entity_id, snapshot_json, mentions_at_last_snapshot, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 snapshot_json=excluded.snapshot_json,
                 mentions_at_last_snapshot=excluded.mentions_at_last_snapshot,
                 updated_at=excluded.updated_at""",
            (entity_id, json.dumps(parsed), total_mentions, parsed["updated_at"]),
        )
    return parsed


def _snap_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand the [start, end) window outward to the nearest word boundaries
    so quotes don't start or end mid-word."""
    if start > 0:
        # Walk left until we hit whitespace, but not too far
        scan = start
        while scan > max(0, start - 30) and not text[scan - 1].isspace():
            scan -= 1
        start = scan
    if end < len(text):
        scan = end
        while scan < min(len(text), end + 30) and not text[scan].isspace():
            scan += 1
        end = scan
    return start, end


def _extract_quote_window(haystack: str, idx: int, length: int,
                          left: int = 80, right: int = 160) -> str:
    """Returns a clean quote around `idx` with word-boundary snapping and a
    leading/trailing ellipsis when truncated mid-paragraph."""
    raw_start = max(0, idx - left)
    raw_end = min(len(haystack), idx + length + right)
    start, end = _snap_to_word_boundaries(haystack, raw_start, raw_end)
    quote = haystack[start:end].strip()
    if start > 0:
        quote = "…" + quote
    if end < len(haystack):
        quote = quote + "…"
    return quote


def backfill_mentions_for_entity(entity_id: int, aliases: tuple[str, ...]) -> dict[str, int]:
    """Substring-scan cached newsletters for any alias. For each match we ask
    Haiku for dossier-relevant bullets. **Only insert the mention if the LLM
    returns at least one bullet.** If the LLM errors (None) or judges the match
    a passing reference (empty list), we skip the row entirely — no ghost
    mention with an empty body in the UI.

    Returns counts so callers can show progress.
    """
    if not aliases:
        return {"inserted": 0, "skipped_empty": 0, "skipped_error": 0}
    with get_connection() as conn:
        entity_row = conn.execute(
            "SELECT id, kind, key, name FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT gmail_message_id, subject, full_content, summary, description, source "
            "FROM newsletters"
        ).fetchall()
    if not entity_row:
        return {"inserted": 0, "skipped_empty": 0, "skipped_error": 0}
    entity = {"id": entity_row[0], "kind": entity_row[1],
              "key": entity_row[2], "name": entity_row[3]}
    inserted = 0
    skipped_empty = 0
    skipped_error = 0
    for r in rows:
        nl_id, subject, full, summary, desc, source = r
        haystack = " ".join(s or "" for s in (subject, full, summary, desc))
        hay_lower = haystack.lower()
        match: str | None = None
        for alias in aliases:
            if alias and alias.lower() in hay_lower:
                match = alias
                break
        if match is None:
            continue
        bullets = _generate_mention_bullets(entity, {
            "subject": subject, "full_content": full, "summary": summary, "source": source,
        })
        if bullets is None:
            # LLM error after retries — don't create a ghost row, user can re-follow
            skipped_error += 1
            continue
        if not bullets:
            # Passing reference — Haiku judged the newsletter doesn't really
            # discuss this entity. Skip the row entirely.
            skipped_empty += 1
            continue
        # Word-boundary-aware quote window kept as a legacy fallback field;
        # the UI prefers bullets but having the quote means we never render
        # an empty body.
        idx = hay_lower.find(match.lower())
        quote = _extract_quote_window(haystack, idx, len(match))
        record_mention(entity_id, nl_id, quote=quote, confidence=0.6, bullets=bullets)
        inserted += 1
    print(f"[dossiers] backfill {entity['key']}: inserted={inserted} "
          f"skipped_empty={skipped_empty} skipped_error={skipped_error}", flush=True)
    return {"inserted": inserted, "skipped_empty": skipped_empty, "skipped_error": skipped_error}


def backfill_bullets_for_existing_mentions(limit: int | None = None) -> dict[str, int]:
    """One-time migration: fill bullets_json for mentions that pre-date the feature.

    Idempotent: filters WHERE m.bullets_json IS NULL. Skips rows whose source
    newsletter is no longer in the table (post-7-day-purge) — those keep the
    legacy quote in the UI.
    """
    with get_connection() as conn:
        sql = (
            "SELECT m.id, m.entity_id, m.newsletter_id, "
            "       e.kind, e.key, e.name, "
            "       n.subject, n.full_content, n.summary, n.source "
            "FROM mentions m "
            "JOIN entities   e ON e.id = m.entity_id "
            "JOIN newsletters n ON n.gmail_message_id = m.newsletter_id "
            "WHERE m.bullets_json IS NULL"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
    total = len(rows)
    print(f"[dossiers] backfill_bullets: starting for {total} mentions", flush=True)
    processed = 0
    filled = 0
    empty = 0
    for i, r in enumerate(rows, start=1):
        mention_id, _eid, _nl_id, kind, key, name, subject, full, summary, source = r
        entity = {"kind": kind, "key": key, "name": name}
        bullets = _generate_mention_bullets(entity, {
            "subject": subject, "full_content": full, "summary": summary, "source": source,
        })
        if bullets is None:
            # LLM error — leave NULL so a re-run picks it up next time.
            print(f"[dossiers] backfill_bullets [{i}/{total}] {kind}/{key}: skipped (LLM None)", flush=True)
            continue
        processed += 1
        if bullets:
            filled += 1
        else:
            empty += 1
        with get_connection() as conn:
            conn.execute(
                "UPDATE mentions SET bullets_json=? WHERE id=?",
                (json.dumps(bullets), mention_id),
            )
        print(f"[dossiers] backfill_bullets [{i}/{total}] {kind}/{key}: {len(bullets)} bullets", flush=True)
        # Inter-call cooldown so we don't blast the per-minute rate limit.
        _time.sleep(0.8)
    print(f"[dossiers] backfill_bullets: done considered={total} filled={filled} empty={empty}", flush=True)
    return {"considered": total, "processed": processed,
            "filled": filled, "empty": empty}


def get_snapshot(entity_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_json, mentions_at_last_snapshot, updated_at "
            "FROM dossiers WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row[0])
    payload["mentions_at_last_snapshot"] = row[1]
    payload["updated_at"] = row[2]
    return payload


def maybe_refresh_snapshot(entity_id: int,
                           min_new_mentions: int = 5,
                           max_age_days: int = 7) -> bool:
    """Returns True if it refreshed."""
    snap = get_snapshot(entity_id)
    current_count = count_mentions(entity_id)
    if snap is None:
        regenerate_snapshot(entity_id)
        return True
    new_since = current_count - int(snap.get("mentions_at_last_snapshot", 0))
    age = datetime.now(timezone.utc) - datetime.fromisoformat(snap["updated_at"])
    if new_since >= min_new_mentions or age >= timedelta(days=max_age_days):
        regenerate_snapshot(entity_id)
        return True
    return False
