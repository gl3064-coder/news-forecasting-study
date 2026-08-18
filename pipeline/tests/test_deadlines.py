from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_program_watch_table_exists(db_initialized):
    from app.db import get_connection
    with get_connection() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(program_watch)").fetchall()}
    assert {"id", "name", "url", "last_hash", "last_text",
            "last_checked_at", "last_status", "last_error"} <= cols


def test_watchlist_shape():
    from app.services.deadlines import WATCHLIST
    assert len(WATCHLIST) >= 5
    for entry in WATCHLIST:
        assert entry["name"] and entry["url"].startswith("http")
        assert "watch_hint" in entry


def test_fetch_page_ok():
    from app.services import deadlines
    resp = MagicMock(status_code=200, text="<html><body>Apply now</body></html>")
    resp.raise_for_status = MagicMock()
    with patch.object(deadlines.requests, "get", return_value=resp) as mock_get:
        text, err = deadlines.fetch_page("https://example.com/x")
    assert err == ""
    assert "Apply now" in text
    # full browser fingerprint via curl_cffi, not python-requests default
    assert mock_get.call_args.kwargs["impersonate"] == "chrome"


def test_fetch_page_error():
    from app.services import deadlines
    with patch.object(deadlines.requests, "get", side_effect=Exception("403 Client Error")):
        text, err = deadlines.fetch_page("https://example.com/x")
    assert text == ""
    assert "403" in err


def test_extract_main_text_strips_noise():
    from app.services.deadlines import extract_main_text
    html = """
    <html><head><style>.x{color:red}</style><script>var a=1;</script></head>
    <body><nav>Home About</nav><p>Applications open September 2026.</p></body></html>
    """
    text = extract_main_text(html)
    assert "Applications open September 2026." in text
    assert "var a=1" not in text
    assert "color:red" not in text


def test_text_hash_stable_and_sensitive():
    from app.services.deadlines import text_hash
    assert text_hash("abc") == text_hash("abc")
    assert text_hash("abc") != text_hash("abd")


def test_judge_change_relevant(monkeypatch):
    from app.services import deadlines
    fake_response = '{"relevant": true, "what_changed": "Applications are now open, deadline Oct 1."}'
    with patch.object(deadlines, "_call_anthropic", return_value=fake_response):
        verdict = deadlines.judge_change("Old text", "New text", "Watch for apps opening")
    assert verdict["relevant"] is True
    assert "Oct 1" in verdict["what_changed"]


def test_judge_change_irrelevant(monkeypatch):
    from app.services import deadlines
    fake_response = '{"relevant": false, "what_changed": "Footer copyright year updated."}'
    with patch.object(deadlines, "_call_anthropic", return_value=fake_response):
        verdict = deadlines.judge_change("Old", "New", "hint")
    assert verdict["relevant"] is False


def test_judge_change_llm_failure_is_conservative(monkeypatch):
    """If the LLM call fails, treat the change as relevant (better a false
    alarm than a missed opening)."""
    from app.services import deadlines
    with patch.object(deadlines, "_call_anthropic", return_value=None):
        verdict = deadlines.judge_change("Old", "New", "hint")
    assert verdict["relevant"] is True
    assert "could not classify" in verdict["what_changed"].lower()


def test_judge_change_llm_exception_is_conservative(monkeypatch):
    """A raising LLM call (timeout, HTTP error) must also hit the fallback,
    not crash the watch run."""
    from app.services import deadlines
    with patch.object(deadlines, "_call_anthropic", side_effect=RuntimeError("boom")):
        verdict = deadlines.judge_change("Old", "New", "hint")
    assert verdict["relevant"] is True
    assert "could not classify" in verdict["what_changed"].lower()


def test_sync_watchlist_upserts(db_initialized):
    from app.services.deadlines import sync_watchlist, WATCHLIST
    from app.db import get_connection
    sync_watchlist()
    sync_watchlist()  # idempotent
    with get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM program_watch").fetchone()[0]
    assert n == len(WATCHLIST)


def test_run_watch_first_run_snapshots_without_alert(db_initialized):
    """First fetch of a page (no prior hash) stores a snapshot, no alert."""
    from app.services import deadlines
    with patch.object(deadlines, "fetch_page", return_value=("<p>Apply in fall</p>", "")), \
         patch.object(deadlines, "judge_change") as mock_judge:
        digest = deadlines.run_watch()
    assert digest["updates"] == []
    assert digest["errors"] == []
    mock_judge.assert_not_called()  # nothing to compare on first run


def test_run_watch_relevant_change_lands_in_digest(db_initialized):
    from app.services import deadlines
    from app.services.deadlines import text_hash, extract_main_text
    from app.db import get_connection
    old_text = extract_main_text("<p>Applications closed.</p>")
    deadlines.sync_watchlist()
    with get_connection() as conn:
        conn.execute("UPDATE program_watch SET last_hash=?, last_text=?", (text_hash(old_text), old_text))
    with patch.object(deadlines, "fetch_page", return_value=("<p>Applications OPEN, due Oct 1.</p>", "")), \
         patch.object(deadlines, "judge_change",
                      return_value={"relevant": True, "what_changed": "Apps opened, due Oct 1."}):
        digest = deadlines.run_watch()
    assert len(digest["updates"]) == len(deadlines.WATCHLIST)
    assert digest["updates"][0]["what_changed"] == "Apps opened, due Oct 1."


def test_run_watch_irrelevant_change_no_alert_but_snapshot_advances(db_initialized):
    from app.services import deadlines
    from app.services.deadlines import text_hash, extract_main_text
    from app.db import get_connection
    deadlines.sync_watchlist()
    old_text = extract_main_text("<p>v1</p>")
    with get_connection() as conn:
        conn.execute("UPDATE program_watch SET last_hash=?, last_text=?", (text_hash(old_text), old_text))
    with patch.object(deadlines, "fetch_page", return_value=("<p>v2</p>", "")), \
         patch.object(deadlines, "judge_change",
                      return_value={"relevant": False, "what_changed": "cosmetic"}):
        digest = deadlines.run_watch()
    assert digest["updates"] == []
    # snapshot advanced: hash now matches v2, so next run sees no change
    new_text = extract_main_text("<p>v2</p>")
    with get_connection() as conn:
        hashes = {row[0] for row in conn.execute("SELECT last_hash FROM program_watch").fetchall()}
    assert hashes == {text_hash(new_text)}


def test_run_watch_fetch_error_lands_in_errors(db_initialized):
    from app.services import deadlines
    with patch.object(deadlines, "fetch_page", return_value=("", "403 Forbidden")):
        digest = deadlines.run_watch()
    assert digest["updates"] == []
    assert len(digest["errors"]) == len(deadlines.WATCHLIST)
    assert "403" in digest["errors"][0]["error"]


def test_build_digest_html_contains_updates_and_errors():
    from app.services.deadlines import build_digest_html
    html = build_digest_html(
        updates=[{"name": "IMC Prosperity", "url": "https://prosperity.imc.com/",
                  "what_changed": "Registration opened for 2027."}],
        errors=[{"name": "Citadel", "url": "https://x.example", "error": "403"}],
    )
    assert "IMC Prosperity" in html
    assert "Registration opened for 2027." in html
    assert "https://prosperity.imc.com/" in html
    assert "Couldn" in html  # "Couldn't check" section
    assert "Citadel" in html


def test_run_and_alert_sends_when_updates(db_initialized):
    from app.services import deadlines
    with patch.object(deadlines, "run_watch",
                      return_value={"updates": [{"name": "A", "url": "u", "what_changed": "opened"}],
                                    "errors": []}), \
         patch.object(deadlines, "send_email", return_value={"id": "m1"}) as mock_send:
        result = deadlines.run_and_alert()
    assert result["sent"] is True
    subject = mock_send.call_args.args[0]
    assert "1 program update" in subject


def test_run_and_alert_sends_when_only_errors(db_initialized):
    from app.services import deadlines
    with patch.object(deadlines, "run_watch",
                      return_value={"updates": [], "errors": [{"name": "A", "url": "u", "error": "403"}]}), \
         patch.object(deadlines, "send_email", return_value={"id": "m1"}) as mock_send:
        result = deadlines.run_and_alert()
    assert result["sent"] is True
    mock_send.assert_called_once()


def test_run_and_alert_silent_when_nothing(db_initialized):
    from app.services import deadlines
    with patch.object(deadlines, "run_watch", return_value={"updates": [], "errors": []}), \
         patch.object(deadlines, "send_email") as mock_send:
        result = deadlines.run_and_alert()
    assert result["sent"] is False
    mock_send.assert_not_called()


def test_run_watch_dry_mode_does_not_advance_snapshots(db_initialized):
    """advance_snapshots=False must leave last_hash/last_text untouched so a
    preview run cannot swallow the next scheduled run's alert."""
    from app.services import deadlines
    from app.services.deadlines import text_hash, extract_main_text
    from app.db import get_connection
    deadlines.sync_watchlist()
    old_text = extract_main_text("<p>v1</p>")
    with get_connection() as conn:
        conn.execute("UPDATE program_watch SET last_hash=?, last_text=?", (text_hash(old_text), old_text))
    with patch.object(deadlines, "fetch_page", return_value=("<p>v2 apps open</p>", "")), \
         patch.object(deadlines, "judge_change",
                      return_value={"relevant": True, "what_changed": "opened"}):
        digest = deadlines.run_watch(advance_snapshots=False)
    # change still detected and reported
    assert len(digest["updates"]) == len(deadlines.WATCHLIST)
    # but snapshots NOT advanced
    with get_connection() as conn:
        hashes = {row[0] for row in conn.execute("SELECT last_hash FROM program_watch").fetchall()}
    assert hashes == {text_hash(old_text)}


def test_build_digest_html_includes_reminders():
    from app.services.deadlines import build_digest_html
    html = build_digest_html(
        updates=[], errors=[{"name": "X", "url": "https://x.example", "error": "403"}],
        reminders=["Sign up for the Jane Street INSIGHT notification form"],
    )
    assert "Reminders" in html
    assert "Jane Street INSIGHT notification form" in html


def test_run_and_alert_email_carries_reminders(db_initialized):
    """When an email goes out, the standing REMINDERS list rides along."""
    from app.services import deadlines
    with patch.object(deadlines, "run_watch",
                      return_value={"updates": [{"name": "A", "url": "u", "what_changed": "opened"}],
                                    "errors": []}), \
         patch.object(deadlines, "REMINDERS", ["Do the thing"]), \
         patch.object(deadlines, "send_email", return_value={"id": "m1"}) as mock_send:
        deadlines.run_and_alert()
    body = mock_send.call_args.args[1]
    assert "Reminders" in body
    assert "Do the thing" in body
