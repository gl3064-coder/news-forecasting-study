import pytest
import sqlite3
from app.db import init_db


def _table_columns(db_path: str, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    finally:
        conn.close()


def test_entities_table(tmp_db_path):
    init_db()
    cols = _table_columns(tmp_db_path, "entities")
    assert {"id", "kind", "key", "name", "aliases_json", "followed", "created_at"}.issubset(cols)


def test_mentions_table(tmp_db_path):
    init_db()
    cols = _table_columns(tmp_db_path, "mentions")
    assert {"id", "entity_id", "newsletter_id", "quote", "confidence",
            "tagged_at", "bullets_json", "newsletter_subject"}.issubset(cols)


def test_init_db_backfills_newsletter_subject(tmp_db_path):
    """Existing mention rows pointing at an in-window newsletter should have
    their newsletter_subject populated by the idempotent UPDATE in init_db."""
    import sqlite3
    init_db()
    conn = sqlite3.connect(tmp_db_path)
    try:
        # Seed a newsletter + a mention with NULL subject
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        cols = ["gmail_message_id", "subject", "full_content", "summary", "description"] + notnull_extras
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})",
            tuple(["nl_seed", "WSJ: A real subject", "body", "s", "d"] + [""] * len(notnull_extras)),
        )
        conn.execute(
            "INSERT INTO entities(kind, key, name, created_at) VALUES ('company','TSLA','Tesla', datetime('now'))"
        )
        eid = conn.execute("SELECT id FROM entities WHERE key='TSLA'").fetchone()[0]
        conn.execute(
            "INSERT INTO mentions(entity_id, newsletter_id, quote, confidence, tagged_at, newsletter_subject) "
            "VALUES (?, 'nl_seed', '', 1.0, datetime('now'), NULL)",
            (eid,),
        )
        conn.commit()
    finally:
        conn.close()
    # Run init_db again — should backfill newsletter_subject
    init_db()
    conn = sqlite3.connect(tmp_db_path)
    try:
        subj = conn.execute(
            "SELECT newsletter_subject FROM mentions WHERE newsletter_id='nl_seed'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert subj == "WSJ: A real subject"


def test_mentions_table_migration_adds_bullets_json(tmp_db_path):
    """Simulate an existing DB created before bullets_json existed: drop the column,
    re-run init_db, and confirm migration adds it back without losing rows.
    """
    import sqlite3
    init_db()
    # Force-recreate mentions table without bullets_json to simulate legacy state
    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.executescript("""
            DROP TABLE mentions;
            CREATE TABLE mentions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id      INTEGER NOT NULL,
                newsletter_id  TEXT NOT NULL,
                quote          TEXT,
                confidence     REAL,
                tagged_at      TEXT NOT NULL
            );
            INSERT INTO mentions(entity_id, newsletter_id, quote, confidence, tagged_at)
            VALUES (1, 'm1', 'legacy quote', 1.0, datetime('now'));
        """)
        conn.commit()
    finally:
        conn.close()
    init_db()  # should ALTER TABLE to add bullets_json
    cols = _table_columns(tmp_db_path, "mentions")
    assert "bullets_json" in cols
    # Existing row preserved with NULL bullets_json
    conn = sqlite3.connect(tmp_db_path)
    try:
        row = conn.execute(
            "SELECT quote, bullets_json FROM mentions WHERE newsletter_id='m1'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "legacy quote"
    assert row[1] is None


def test_dossiers_table(tmp_db_path):
    init_db()
    cols = _table_columns(tmp_db_path, "dossiers")
    assert {"entity_id", "snapshot_json", "mentions_at_last_snapshot", "updated_at"}.issubset(cols)


def test_entities_kind_key_unique(tmp_db_path):
    init_db()
    conn = sqlite3.connect(tmp_db_path)
    try:
        conn.execute(
            "INSERT INTO entities(kind, key, name, created_at) VALUES (?,?,?, datetime('now'))",
            ("company", "TSLA", "Tesla"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entities(kind, key, name, created_at) VALUES (?,?,?, datetime('now'))",
                ("company", "TSLA", "Tesla Dup"),
            )
    finally:
        conn.close()


def test_seed_follows_present_after_init(tmp_db_path):
    init_db()
    conn = sqlite3.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT kind, key, name, followed FROM entities WHERE followed=1"
        ).fetchall()
    finally:
        conn.close()
    keys = {(k, key) for k, key, _name, _f in rows}
    assert ("company", "NVDA") in keys
    assert ("company", "JPM") in keys
    assert ("company", "AAPL") in keys
    assert ("sector", "ai_chips") in keys
    assert ("concept", "duration") in keys


def test_seed_idempotent(tmp_db_path):
    init_db()
    init_db()  # should not duplicate
    conn = sqlite3.connect(tmp_db_path)
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE followed=1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert cnt == 5
