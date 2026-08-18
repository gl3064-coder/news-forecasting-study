"""Tests for cron sync failure visibility.

Covers the 2026-07-14 incident class: sync_newsletters raising on every cron
tick (wrong Gmail account -> label not found) while nothing surfaced anywhere.
The fix: [cron]-prefixed logging, a consecutive-failure counter in app_state,
and a warning on /health.
"""

import json

import pytest
from fastapi.testclient import TestClient


class TestCronHealthService:
    def test_starts_at_zero_failures(self, db_initialized):
        from app.services import cron_health
        state = cron_health.sync_health()
        assert state["consecutive_failures"] == 0

    def test_failures_increment_and_success_resets(self, db_initialized):
        from app.services import cron_health
        cron_health.record_sync_failure('Gmail label "Pulse" was not found')
        cron_health.record_sync_failure('Gmail label "Pulse" was not found')
        state = cron_health.sync_health()
        assert state["consecutive_failures"] == 2
        assert "was not found" in state["last_error"]
        assert state["last_failure_at"]

        cron_health.record_sync_success()
        state = cron_health.sync_health()
        assert state["consecutive_failures"] == 0
        assert state["last_success_at"]
        # last_error is kept for post-mortems, but the counter is what warns
        assert "was not found" in state["last_error"]

    def test_state_persists_in_app_state_table(self, db_initialized):
        from app.db import get_connection
        from app.services import cron_health
        cron_health.record_sync_failure("boom")
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE key = 'sync_health'"
            ).fetchone()
        assert row is not None
        assert json.loads(row[0])["consecutive_failures"] == 1


class TestScheduledSync:
    @pytest.fixture
    def main_quiet(self, db_initialized, monkeypatch):
        """app.main with the LLM background chain stubbed out."""
        import app.main as main
        monkeypatch.setattr(main, "_background_summarize", lambda: None)
        monkeypatch.setattr(main, "purge_old_newsletters", lambda days: None)
        monkeypatch.setattr(main, "auto_label_newsletters", lambda: {})
        monkeypatch.setattr(main, "sync_newsletters", lambda force=False: {})
        return main

    def test_sync_failure_logged_with_cron_prefix_and_recorded(
        self, main_quiet, monkeypatch, capsys
    ):
        def boom(force=False):
            raise RuntimeError('Gmail label "Pulse" was not found')
        monkeypatch.setattr(main_quiet, "sync_newsletters", boom)

        main_quiet._scheduled_sync()  # must not raise — cron stays resilient

        out = capsys.readouterr().out
        assert "[cron]" in out
        assert "sync_newsletters" in out
        assert "was not found" in out

        from app.services import cron_health
        assert cron_health.sync_health()["consecutive_failures"] == 1

    def test_purge_and_label_failures_logged_but_not_counted(
        self, main_quiet, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            main_quiet, "purge_old_newsletters",
            lambda days: (_ for _ in ()).throw(RuntimeError("purge broke")),
        )
        monkeypatch.setattr(
            main_quiet, "auto_label_newsletters",
            lambda: (_ for _ in ()).throw(RuntimeError("label broke")),
        )

        main_quiet._scheduled_sync()

        out = capsys.readouterr().out
        assert "purge broke" in out
        assert "label broke" in out
        # Only sync_newsletters drives the health counter — and it succeeded
        from app.services import cron_health
        assert cron_health.sync_health()["consecutive_failures"] == 0

    def test_sync_success_resets_counter(self, main_quiet, capsys):
        from app.services import cron_health
        cron_health.record_sync_failure("old failure")
        main_quiet._scheduled_sync()
        assert cron_health.sync_health()["consecutive_failures"] == 0


class TestSyncFailureAlertEmail:
    """Repeated cron sync failures trigger ONE alert email via send_email
    (the deadline-watcher helper). Threshold is 2 consecutive failures so a
    single transient blip stays quiet; recovery re-arms the alert."""

    @pytest.fixture
    def main_alert(self, db_initialized, monkeypatch):
        import app.main as main
        monkeypatch.setattr(main, "_background_summarize", lambda: None)
        monkeypatch.setattr(main, "purge_old_newsletters", lambda days: None)
        monkeypatch.setattr(main, "auto_label_newsletters", lambda: {})
        sent: list[dict] = []
        monkeypatch.setattr(
            main, "send_email",
            lambda subject, html_body, to=None: sent.append(
                {"subject": subject, "html_body": html_body}
            ) or {},
        )
        def failing_sync(force=False):
            raise RuntimeError('Gmail label "Pulse" was not found')
        monkeypatch.setattr(main, "sync_newsletters", failing_sync)
        return main, sent

    def test_no_alert_on_first_failure(self, main_alert):
        main, sent = main_alert
        main._scheduled_sync()
        assert sent == []

    def test_alert_sent_once_threshold_reached(self, main_alert):
        main, sent = main_alert
        main._scheduled_sync()
        main._scheduled_sync()
        assert len(sent) == 1
        assert "sync" in sent[0]["subject"].lower()
        assert "was not found" in sent[0]["html_body"]

    def test_no_repeat_alert_while_still_failing(self, main_alert):
        main, sent = main_alert
        for _ in range(4):
            main._scheduled_sync()
        assert len(sent) == 1

    def test_realerts_after_recovery(self, main_alert, monkeypatch):
        main, sent = main_alert
        main._scheduled_sync()
        main._scheduled_sync()
        assert len(sent) == 1
        # Sync recovers once, then breaks again
        from app.services import cron_health
        cron_health.record_sync_success()
        main._scheduled_sync()
        main._scheduled_sync()
        assert len(sent) == 2

    def test_alert_email_failure_does_not_crash_cron(
        self, main_alert, monkeypatch, capsys
    ):
        main, sent = main_alert
        def broken_send(subject, html_body, to=None):
            raise RuntimeError("send blew up too")
        monkeypatch.setattr(main, "send_email", broken_send)
        main._scheduled_sync()
        main._scheduled_sync()  # threshold tick; email raises; must not propagate
        out = capsys.readouterr().out
        assert "alert email failed" in out
        # Failure still recorded despite the broken email path
        from app.services import cron_health
        assert cron_health.sync_health()["consecutive_failures"] == 2


class TestHealthEndpoint:
    @pytest.fixture
    def client(self, db_initialized):
        from app.main import app
        return TestClient(app)

    def test_ok_with_no_failures(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["sync"]["consecutive_failures"] == 0
        assert "warning" not in body["sync"]

    def test_degraded_with_warning_after_failures(self, client):
        from app.services import cron_health
        cron_health.record_sync_failure('Gmail label "Pulse" was not found')
        cron_health.record_sync_failure('Gmail label "Pulse" was not found')

        body = client.get("/health").json()
        assert body["status"] == "degraded"
        assert body["sync"]["consecutive_failures"] == 2
        assert "was not found" in body["sync"]["last_error"]
        assert "warning" in body["sync"]

    def test_recovers_to_ok_after_success(self, client):
        from app.services import cron_health
        cron_health.record_sync_failure("boom")
        cron_health.record_sync_success()

        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "warning" not in body["sync"]

    def test_manual_sync_route_resets_counter(self, client, monkeypatch):
        import app.main as main
        from app.services import cron_health
        cron_health.record_sync_failure("stale failure")
        monkeypatch.setattr(main, "sync_newsletters", lambda force=False: {})
        monkeypatch.setattr(main, "_background_summarize", lambda: None)

        r = client.post("/newsletters/sync")
        assert r.status_code == 200
        assert cron_health.sync_health()["consecutive_failures"] == 0
