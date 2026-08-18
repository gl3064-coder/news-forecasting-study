from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch


def test_send_scope_present():
    from app.services.gmail import SCOPES
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES


def test_send_email_builds_mime_and_calls_send(monkeypatch):
    monkeypatch.setenv("PULSE_DEADLINE_ALERT_TO", "lgavin689@gmail.com")
    from app.services import gmail as gmail_mod

    fake_service = MagicMock()
    fake_send = fake_service.users.return_value.messages.return_value.send
    fake_send.return_value.execute.return_value = {"id": "msg123"}

    with patch.object(gmail_mod, "build_gmail_service", return_value=fake_service):
        result = gmail_mod.send_email("Test subject", "<p>Hello</p>")

    assert result == {"id": "msg123"}
    _, kwargs = fake_send.call_args
    assert kwargs["userId"] == "me"
    raw = kwargs["body"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode()).decode()
    assert "Test subject" in decoded
    assert "lgavin689@gmail.com" in decoded
