from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def db_path() -> Path:
    configured = os.getenv("PULSE_DB_FILE", "backend/pulse.db")
    return Path(configured)


def get_connection() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletters (
                gmail_message_id TEXT PRIMARY KEY,
                thread_id TEXT,
                subject TEXT NOT NULL,
                sender TEXT NOT NULL,
                received_at TEXT,
                link TEXT,
                source TEXT NOT NULL,
                source_icon TEXT NOT NULL,
                tier TEXT NOT NULL,
                summary TEXT NOT NULL,
                description TEXT NOT NULL,
                full_content TEXT NOT NULL,
                label_names TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_newsletters_received_at
            ON newsletters (received_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS briefings (
                briefing_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS story_summaries (
                story_id TEXT PRIMARY KEY,
                story_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                engine TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Key/value store for small bits of app state that need to survive
        # restarts (spotlight ticker picks, etc.). value_json is application-
        # defined JSON; the table itself is dumb storage.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key        TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # --- Dossiers feature (added 2026-05-18) ---
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT NOT NULL CHECK (kind IN ('company','sector','concept')),
            key           TEXT NOT NULL,
            name          TEXT NOT NULL,
            aliases_json  TEXT,
            followed      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            UNIQUE(kind, key)
        );

        CREATE TABLE IF NOT EXISTS mentions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id           INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            newsletter_id       TEXT NOT NULL,
            quote               TEXT,
            confidence          REAL,
            tagged_at           TEXT NOT NULL,
            bullets_json        TEXT,
            newsletter_subject  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mentions_entity     ON mentions(entity_id);
        CREATE INDEX IF NOT EXISTS idx_mentions_newsletter ON mentions(newsletter_id);

        CREATE TABLE IF NOT EXISTS dossiers (
            entity_id                  INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
            snapshot_json              TEXT NOT NULL,
            mentions_at_last_snapshot  INTEGER NOT NULL DEFAULT 0,
            updated_at                 TEXT NOT NULL
        );
        """)

        # Unique index on (entity_id, newsletter_id). Wrapped because it will
        # fail on legacy DBs that still have duplicate rows — run
        # /admin/dossiers/dedupe once to clean those up, after which a server
        # restart (or the next call here) will create the index successfully.
        try:
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_mentions_entity_newsletter_uniq "
                "ON mentions(entity_id, newsletter_id)"
            )
        except Exception as exc:
            print(f"[db] mentions unique index skipped (likely existing duplicates): {exc}", flush=True)

        # In-place migrations for existing DBs.
        existing_cols = {row[1] for row in connection.execute("PRAGMA table_info(mentions)").fetchall()}
        if "bullets_json" not in existing_cols:
            connection.execute("ALTER TABLE mentions ADD COLUMN bullets_json TEXT")
        if "newsletter_subject" not in existing_cols:
            connection.execute("ALTER TABLE mentions ADD COLUMN newsletter_subject TEXT")
        # One-shot: populate newsletter_subject for legacy rows from the
        # newsletters table where the source row is still in-window. Idempotent
        # because the WHERE clause skips already-populated rows.
        connection.execute(
            """
            UPDATE mentions
               SET newsletter_subject = (
                       SELECT n.subject FROM newsletters n
                       WHERE n.gmail_message_id = mentions.newsletter_id
                   )
             WHERE newsletter_subject IS NULL
               AND EXISTS (
                       SELECT 1 FROM newsletters n
                       WHERE n.gmail_message_id = mentions.newsletter_id
                   )
            """
        )

        # Seed first-run follows from the taxonomy module.
        # Per-entry INSERT OR IGNORE so seeds still land if other entities
        # pre-existed (e.g. ones the entity extractor inserted earlier).
        # An entry the user explicitly unfollowed retains its followed=0
        # because OR IGNORE leaves the existing row untouched.
        from .services.dossier_taxonomy import SEED_FOLLOWS
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT OR IGNORE INTO entities(kind, key, name, followed, created_at) VALUES (?,?,?,1,?)",
            [(k, key, name, ts) for (k, key, name) in SEED_FOLLOWS],
        )

        # --- Program deadline watcher (added 2026-07-13) ---
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS program_watch (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                url             TEXT NOT NULL UNIQUE,
                last_hash       TEXT DEFAULT '',
                last_text       TEXT DEFAULT '',
                last_checked_at TEXT DEFAULT '',
                last_status     TEXT DEFAULT '',
                last_error      TEXT DEFAULT ''
            )
            """
        )
