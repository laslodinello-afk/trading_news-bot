"""
message_log.py : journal durable des messages Telegram (Turso). Aucun réseau
réel ici (message_log._connect mocké) — la connexion réelle a été vérifiée à
la main contre la vraie base Turso au moment de construire ce module.
"""
from datetime import date
from unittest.mock import Mock, patch

import config
import message_log


def test_log_message_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    message_log.log_message("perso", "peu importe")  # ne doit pas lever, aucune connexion tentée


def test_connect_returns_none_when_url_missing(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    assert message_log._connect() is None


def test_connect_returns_none_when_token_missing(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "")
    assert message_log._connect() is None


def test_connect_returns_none_on_connection_error(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    with patch("turso.lib_sync.connect_sync", side_effect=ConnectionError("injoignable")):
        assert message_log._connect() is None


def test_log_message_inserts_commits_and_pushes(monkeypatch):
    fake_conn = Mock()
    with patch("message_log._connect", return_value=fake_conn):
        message_log.log_message("perso", "🟢 Message de test")

    fake_conn.execute.assert_called_once()
    args, _ = fake_conn.execute.call_args
    assert "INSERT INTO telegram_messages" in args[0]
    assert args[1][1:] == ("perso", "🟢 Message de test")
    fake_conn.commit.assert_called_once()
    fake_conn.push.assert_called_once()


def test_log_message_swallows_exceptions(monkeypatch):
    fake_conn = Mock()
    fake_conn.execute.side_effect = RuntimeError("panne réseau")
    with patch("message_log._connect", return_value=fake_conn):
        message_log.log_message("perso", "ne doit jamais lever")  # ne doit pas lever


def test_log_message_noop_when_connect_returns_none(monkeypatch):
    with patch("message_log._connect", return_value=None):
        message_log.log_message("perso", "rien ne doit se passer")  # ne doit pas lever


def test_get_messages_for_day_returns_empty_when_not_configured(monkeypatch):
    with patch("message_log._connect", return_value=None):
        result = message_log.get_messages_for_day(date(2026, 8, 1))
    assert result == []


def test_get_messages_for_day_pulls_and_returns_rows(monkeypatch):
    fake_conn = Mock()
    fake_conn.execute.return_value.fetchall.return_value = [
        {"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "perso", "raw_text": "Bonjour"},
    ]
    with patch("message_log._connect", return_value=fake_conn):
        result = message_log.get_messages_for_day(date(2026, 8, 1))

    fake_conn.pull.assert_called_once()
    assert result == [{"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "perso", "raw_text": "Bonjour"}]

    query_args = fake_conn.execute.call_args.args
    assert "WHERE sent_at >= ? AND sent_at < ?" in query_args[0]
    assert query_args[1][0] < query_args[1][1]


def test_get_messages_for_day_returns_empty_on_exception():
    fake_conn = Mock()
    fake_conn.pull.side_effect = RuntimeError("panne réseau")
    with patch("message_log._connect", return_value=fake_conn):
        result = message_log.get_messages_for_day(date(2026, 8, 1))
    assert result == []
