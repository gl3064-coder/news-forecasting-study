import json
from datetime import datetime, timezone


def test_upsert_entity_is_idempotent(db_initialized):
    from app.services import dossiers
    eid1 = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    eid2 = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    assert eid1 == eid2


def test_record_mention_links_entity_and_newsletter(db_initialized):
    from app.services import dossiers
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    dossiers.record_mention(eid, "msg_abc", quote="NVDA led the decline.")
    mentions = dossiers.list_mentions(eid, limit=10)
    assert len(mentions) == 1
    assert mentions[0]["newsletter_id"] == "msg_abc"
    assert "NVDA" in mentions[0]["quote"]


def test_follow_and_unfollow(db_initialized):
    # Use MSFT — not in seed (which auto-follows NVDA/JPM/AAPL)
    from app.services import dossiers
    eid = dossiers.upsert_entity("company", "MSFT", "Microsoft")
    assert dossiers.is_followed(eid) is False
    dossiers.follow(eid)
    assert dossiers.is_followed(eid) is True
    dossiers.unfollow(eid)
    assert dossiers.is_followed(eid) is False


def test_list_followed_returns_only_followed(db_initialized):
    # Use MSFT + AMD — neither is in the seed list
    from app.services import dossiers
    a = dossiers.upsert_entity("company", "MSFT", "Microsoft")
    b = dossiers.upsert_entity("company", "AMD", "AMD")
    dossiers.follow(a)
    followed = dossiers.list_followed()
    keys = {row["key"] for row in followed}
    assert "MSFT" in keys
    assert "AMD" not in keys


def test_list_discover_ranks_unfollowed_by_mention_count(db_initialized):
    # Use MSFT + AMD — neither is in the seed (so both start unfollowed)
    from app.services import dossiers
    msft = dossiers.upsert_entity("company", "MSFT", "Microsoft")
    amd = dossiers.upsert_entity("company", "AMD", "AMD")
    for i in range(3):
        dossiers.record_mention(msft, f"msg_{i}", "...")
    for i in range(1):
        dossiers.record_mention(amd, f"other_{i}", "...")
    discover = dossiers.list_discover(min_mentions=1, limit=5)
    keys = [row["key"] for row in discover]
    assert keys[0] == "MSFT"  # higher count first
    assert "AMD" in keys


def test_process_entities_creates_rows(db_initialized):
    from app.services import dossiers
    extracted = [
        {"kind": "company", "key": "NVDA",     "name": "Nvidia",     "quote": "q1"},
        {"kind": "sector",  "key": "ai_chips", "name": "AI / Chips", "quote": "q2"},
        {"kind": "concept", "key": "duration", "name": "Duration",   "quote": "q3"},
    ]
    dossiers.process_entities("msg_xyz", extracted)
    # All 3 entity rows + 3 mention rows exist
    assert dossiers.get_entity_id("company", "NVDA") is not None
    assert dossiers.get_entity_id("sector",  "ai_chips") is not None
    assert dossiers.get_entity_id("concept", "duration") is not None
    eid = dossiers.get_entity_id("company", "NVDA")
    assert len(dossiers.list_mentions(eid, limit=10)) == 1


import json as _json


def test_regenerate_snapshot_stores_json(db_initialized, mock_anthropic):
    from app.services import dossiers
    mock_anthropic.returns(_json.dumps({
        "overview": "AI bellwether.",
        "plain_english": "NVDA makes the chips AI companies buy.",
        "bull_thesis": "Capex supercycle continues.",
        "bear_thesis": "Customer concentration risk.",
        "topic_tags": ["AI/Tech"],
        "key_themes": [{"theme": "AI capex", "evidence_count": 3}],
        "notable_quotes": [],
    }))
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    dossiers.record_mention(eid, "m1", "q")
    dossiers.regenerate_snapshot(eid)
    snap = dossiers.get_snapshot(eid)
    assert snap is not None
    assert snap["overview"].startswith("AI bellwether")
    assert snap["mentions_at_last_snapshot"] == 1


def test_maybe_refresh_respects_threshold(db_initialized, mock_anthropic):
    from app.services import dossiers
    mock_anthropic.returns(_json.dumps({
        "overview": "x", "plain_english": "y", "bull_thesis": "b",
        "bear_thesis": "z", "topic_tags": [], "key_themes": [], "notable_quotes": [],
    }))
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    # 3 mentions — below threshold of 5 — should NOT refresh
    for i in range(3):
        dossiers.record_mention(eid, f"m{i}", "")
    dossiers.regenerate_snapshot(eid)  # bring baseline to 3
    snap_before = dossiers.get_snapshot(eid)
    ts_before = snap_before["updated_at"]

    # Add 2 more (still under +5 since baseline)
    for i in range(3, 5):
        dossiers.record_mention(eid, f"m{i}", "")
    dossiers.maybe_refresh_snapshot(eid, min_new_mentions=5, max_age_days=7)
    snap_after = dossiers.get_snapshot(eid)
    assert snap_after["updated_at"] == ts_before  # not refreshed

    # Add 3 more (now +5 since baseline) — should refresh
    for i in range(5, 8):
        dossiers.record_mention(eid, f"m{i}", "")
    dossiers.maybe_refresh_snapshot(eid, min_new_mentions=5, max_age_days=7)
    snap_final = dossiers.get_snapshot(eid)
    assert snap_final["updated_at"] != ts_before


def test_backfill_finds_substring_matches(db_initialized, mock_anthropic):
    # Bullets call now runs inside backfill — stub it so tests stay offline.
    mock_anthropic.returns(_json.dumps({"bullets": ["NVDA up 4% on AI demand"]}))
    from app.services import dossiers
    import sqlite3, os
    # Seed two newsletter rows directly. NOTE: this test uses only the columns
    # that the production code queries in `backfill_mentions_for_entity`
    # (gmail_message_id, subject, full_content, summary, description). If the
    # newsletters table requires additional NOT NULL columns, run
    # `PRAGMA table_info(newsletters)` once locally and add them to this INSERT
    # with empty strings — they aren't read by the backfill logic.
    db_path = os.environ["PULSE_DB_FILE"]
    conn = sqlite3.connect(db_path)
    try:
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        col_names = [c[1] for c in cols_info]
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        # Build the INSERT dynamically so we satisfy NOT NULL without assuming column list.
        base_cols = ["gmail_message_id", "subject", "full_content", "summary", "description"]
        cols = base_cols + notnull_extras
        placeholders = ",".join("?" * len(cols))
        ins_sql = f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})"
        # Empty string for any extra NOT NULL columns
        def _row(nl_id, subject, body):
            return tuple([nl_id, subject, body, body[:60], body[:60]] + [""] * len(notnull_extras))
        conn.execute(ins_sql, _row("nl1", "Nvidia surges on AI demand",
                                   "Nvidia (NVDA) surged 4% today on AI demand..."))
        conn.execute(ins_sql, _row("nl2", "Random oil story",
                                   "Crude oil prices rose 2% on Iran tensions."))
        conn.commit()
    finally:
        conn.close()

    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    result = dossiers.backfill_mentions_for_entity(eid, aliases=("Nvidia", "NVDA"))
    assert result == {"inserted": 1, "skipped_empty": 0, "skipped_error": 0}
    assert dossiers.count_mentions(eid) == 1
    # Bullets should round-trip from the mocked Haiku response
    mentions = dossiers.list_mentions(eid, limit=5)
    assert mentions[0]["bullets"] == ["NVDA up 4% on AI demand"]


def test_backfill_skips_when_bullets_empty(db_initialized, mock_anthropic):
    """Ghost-row fix: empty bullets array → mention is NOT inserted."""
    mock_anthropic.returns(_json.dumps({"bullets": []}))
    from app.services import dossiers
    import sqlite3, os
    db_path = os.environ["PULSE_DB_FILE"]
    conn = sqlite3.connect(db_path)
    try:
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        cols = ["gmail_message_id", "subject", "full_content", "summary", "description"] + notnull_extras
        placeholders = ",".join("?" * len(cols))
        ins_sql = f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})"
        conn.execute(ins_sql, tuple(
            ["nl_passing", "Some oil story", "Oil up 2%. Nvidia mentioned once.", "s", "d"]
            + [""] * len(notnull_extras)))
        conn.commit()
    finally:
        conn.close()

    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    result = dossiers.backfill_mentions_for_entity(eid, aliases=("Nvidia",))
    assert result == {"inserted": 0, "skipped_empty": 1, "skipped_error": 0}
    assert dossiers.count_mentions(eid) == 0  # no ghost row created


def test_record_mention_round_trips_bullets(db_initialized):
    from app.services import dossiers
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    dossiers.record_mention(eid, "msg_a", quote="q", bullets=["First specific fact", "Second one"])
    dossiers.record_mention(eid, "msg_b", quote="q", bullets=[])
    dossiers.record_mention(eid, "msg_c", quote="legacy")  # bullets default None
    rows = {m["newsletter_id"]: m for m in dossiers.list_mentions(eid, limit=10)}
    assert rows["msg_a"]["bullets"] == ["First specific fact", "Second one"]
    assert rows["msg_b"]["bullets"] == []         # explicit "passing reference"
    assert rows["msg_c"]["bullets"] is None       # legacy / not yet generated


def test_record_mention_persists_subject_for_archive(db_initialized):
    """When the newsletter is purged later, the stored subject still rides
    along with the mention row so the UI can show "Title  (archived)" instead
    of the generic placeholder."""
    from app.services import dossiers
    import sqlite3, os
    # Seed a newsletter so record_mention can look up its subject.
    db_path = os.environ["PULSE_DB_FILE"]
    conn = sqlite3.connect(db_path)
    try:
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        cols = ["gmail_message_id", "subject", "full_content", "summary", "description"] + notnull_extras
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})",
            tuple(["nl_x", "WSJ Politics: The Real Subject", "body", "s", "d"] + [""] * len(notnull_extras)),
        )
        conn.commit()
    finally:
        conn.close()

    eid = dossiers.upsert_entity("company", "TSLA", "Tesla")
    # Path A: auto-lookup at write time
    dossiers.record_mention(eid, "nl_x", quote="q", bullets=["b1"])
    # Path B: explicit pass-through (used by future caller plumbing)
    dossiers.record_mention(eid, "nl_y", quote="q", bullets=["b2"],
                            newsletter_subject="Explicit subject")
    rows = {m["newsletter_id"]: m for m in dossiers.list_mentions(eid, limit=10)}
    assert rows["nl_x"]["newsletter_subject"] == "WSJ Politics: The Real Subject"
    assert rows["nl_y"]["newsletter_subject"] == "Explicit subject"

    # Simulate the 7-day purge — drop the newsletter row.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM newsletters WHERE gmail_message_id='nl_x'")
        conn.commit()
    finally:
        conn.close()
    rows = {m["newsletter_id"]: m for m in dossiers.list_mentions(eid, limit=10)}
    # JOIN-derived title is now None, but persisted subject survives.
    assert rows["nl_x"]["newsletter_title"] is None
    assert rows["nl_x"]["newsletter_subject"] == "WSJ Politics: The Real Subject"


def test_process_entities_passes_through_bullets(db_initialized):
    from app.services import dossiers
    dossiers.process_entities("msg_pe", [
        {"kind": "company", "key": "NVDA", "name": "Nvidia",
         "bullets": ["Specific claim with number 4%", "Another grounded line"]},
    ])
    eid = dossiers.get_entity_id("company", "NVDA")
    assert eid is not None
    mentions = dossiers.list_mentions(eid, limit=5)
    assert mentions[0]["bullets"] == ["Specific claim with number 4%", "Another grounded line"]


def test_backfill_bullets_for_existing_mentions(db_initialized, mock_anthropic):
    """Migration only fills rows where bullets_json IS NULL AND the newsletter
    is still in the table. Archived rows stay NULL."""
    mock_anthropic.returns(_json.dumps({"bullets": ["Backfilled bullet w/ specific 5% figure"]}))
    from app.services import dossiers
    import sqlite3, os
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    # mention #1: newsletter row exists → should get filled
    dossiers.record_mention(eid, "nl_live", quote="legacy", bullets=None)
    # mention #2: newsletter row does NOT exist (archived) → should stay NULL
    dossiers.record_mention(eid, "nl_archived", quote="legacy", bullets=None)
    # mention #3: bullets already present → should be skipped (idempotent)
    dossiers.record_mention(eid, "nl_already", quote="legacy", bullets=["already there"])

    # Seed one newsletter row for nl_live only
    db_path = os.environ["PULSE_DB_FILE"]
    conn = sqlite3.connect(db_path)
    try:
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        base_cols = ["gmail_message_id", "subject", "full_content", "summary", "description"]
        cols = base_cols + notnull_extras
        placeholders = ",".join("?" * len(cols))
        ins_sql = f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})"
        row_vals = ["nl_live", "Nvidia news", "Nvidia up 5% today.", "summary", "desc"] + [""] * len(notnull_extras)
        conn.execute(ins_sql, tuple(row_vals))
        conn.commit()
    finally:
        conn.close()

    result = dossiers.backfill_bullets_for_existing_mentions()
    # Only nl_live qualifies (archived skipped by JOIN, already-set skipped by IS NULL filter)
    assert result["considered"] == 1
    assert result["filled"] == 1

    by_nl = {m["newsletter_id"]: m for m in dossiers.list_mentions(eid, limit=10)}
    assert by_nl["nl_live"]["bullets"] == ["Backfilled bullet w/ specific 5% figure"]
    assert by_nl["nl_archived"]["bullets"] is None   # untouched
    assert by_nl["nl_already"]["bullets"] == ["already there"]   # idempotent


def test_backfill_bullets_empty_array_for_passing_reference(db_initialized, mock_anthropic):
    """When Haiku returns {"bullets": []} the row gets bullets_json='[]', not NULL,
    so we don't keep re-querying on every migration run."""
    mock_anthropic.returns(_json.dumps({"bullets": []}))
    from app.services import dossiers
    import sqlite3, os
    eid = dossiers.upsert_entity("company", "NVDA", "Nvidia")
    dossiers.record_mention(eid, "nl_passing", quote="legacy", bullets=None)

    db_path = os.environ["PULSE_DB_FILE"]
    conn = sqlite3.connect(db_path)
    try:
        cols_info = conn.execute("PRAGMA table_info(newsletters)").fetchall()
        notnull_extras = [c[1] for c in cols_info
                          if c[3] == 1 and c[1] not in
                          ("gmail_message_id", "subject", "full_content", "summary", "description")]
        cols = ["gmail_message_id", "subject", "full_content", "summary", "description"] + notnull_extras
        placeholders = ",".join("?" * len(cols))
        ins_sql = f"INSERT INTO newsletters({','.join(cols)}) VALUES ({placeholders})"
        conn.execute(ins_sql, tuple(
            ["nl_passing", "Mostly about oil", "Oil up 2%. (NVDA mentioned once.)",
             "summary", "desc"] + [""] * len(notnull_extras)))
        conn.commit()
    finally:
        conn.close()

    result = dossiers.backfill_bullets_for_existing_mentions()
    assert result["empty"] == 1
    rows = dossiers.list_mentions(eid, limit=5)
    assert rows[0]["bullets"] == []   # not None — won't be reprocessed
