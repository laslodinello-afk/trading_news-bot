"""
message_log.py : journal durable des messages Telegram (Turso, avec repli
SQLite local si Turso est injoignable). Aucun réseau réel ici (message_log.
_connect mocké) — la connexion réelle a été vérifiée à la main contre la
vraie base Turso au moment de construire ce module.
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
    assert message_log._connect() == (None, False)


def test_connect_returns_none_when_token_missing(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "")
    assert message_log._connect() == (None, False)


def test_connect_falls_back_to_local_sqlite_on_turso_error(monkeypatch, tmp_path):
    # Turso configuré mais injoignable (403, panne réseau...) : doit retomber
    # sur une vraie connexion SQLite locale utilisable, pas juste None — voir
    # le raisonnement en tête de fichier (données déjà commitées localement
    # à ne pas jeter).
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    monkeypatch.setattr(config, "MESSAGE_LOG_REPLICA_PATH", str(tmp_path / "message_log.db"))
    with patch("turso.lib_sync.connect_sync", side_effect=ConnectionError("injoignable")):
        conn, is_turso = message_log._connect()
    try:
        assert is_turso is False
        assert conn is not None
        conn.execute("SELECT 1")  # vraie connexion SQLite utilisable
    finally:
        if conn is not None:
            conn.close()


def test_connect_returns_none_when_both_turso_and_local_fail(monkeypatch):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    # Chemin de fichier invalide (dossier inexistant) pour faire échouer aussi le repli local.
    monkeypatch.setattr(config, "MESSAGE_LOG_REPLICA_PATH", "/chemin/inexistant/message_log.db")
    with patch("turso.lib_sync.connect_sync", side_effect=ConnectionError("injoignable")):
        assert message_log._connect() == (None, False)


def test_log_message_inserts_commits_and_pushes_when_turso(monkeypatch):
    fake_conn = Mock()
    with patch("message_log._connect", return_value=(fake_conn, True)):
        message_log.log_message("perso", "🟢 Message de test")

    fake_conn.execute.assert_called_once()
    args, _ = fake_conn.execute.call_args
    assert "INSERT INTO telegram_messages" in args[0]
    assert args[1][1:] == ("perso", "🟢 Message de test")
    fake_conn.commit.assert_called_once()
    fake_conn.push.assert_called_once()


def test_log_message_commits_without_push_on_local_fallback(monkeypatch):
    # Repli SQLite pur : pas de .push() à appeler (n'existe pas sur une
    # connexion sqlite3 standard, l'appeler lèverait AttributeError).
    fake_conn = Mock(spec=["execute", "commit"])
    with patch("message_log._connect", return_value=(fake_conn, False)):
        message_log.log_message("perso", "🟢 Message de test")

    fake_conn.commit.assert_called_once()
    fake_conn.execute.assert_called_once()


def test_log_message_swallows_exceptions(monkeypatch):
    fake_conn = Mock()
    fake_conn.execute.side_effect = RuntimeError("panne réseau")
    with patch("message_log._connect", return_value=(fake_conn, True)):
        message_log.log_message("perso", "ne doit jamais lever")  # ne doit pas lever


def test_log_message_noop_when_connect_returns_none(monkeypatch):
    with patch("message_log._connect", return_value=(None, False)):
        message_log.log_message("perso", "rien ne doit se passer")  # ne doit pas lever


def test_get_messages_for_day_returns_empty_when_not_configured(monkeypatch):
    with patch("message_log._connect", return_value=(None, False)):
        result = message_log.get_messages_for_day(date(2026, 8, 1))
    assert result == []


def test_get_messages_for_day_pulls_and_returns_rows(monkeypatch):
    # Force un pull() considéré comme "pas fait depuis longtemps", sinon ce
    # test dépendrait de l'ordre d'exécution et du timing des autres tests
    # partageant le même état module-level (_last_pull_monotonic, voir le
    # throttle anti-quota Turso ci-dessous).
    monkeypatch.setattr(message_log, "_last_pull_monotonic", None)
    fake_conn = Mock()
    fake_conn.execute.return_value.fetchall.return_value = [
        {"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "perso", "raw_text": "Bonjour"},
    ]
    with patch("message_log._connect", return_value=(fake_conn, True)):
        result = message_log.get_messages_for_day(date(2026, 8, 1))

    fake_conn.pull.assert_called_once()
    assert result == [{"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "perso", "raw_text": "Bonjour"}]

    query_args = fake_conn.execute.call_args.args
    assert "WHERE sent_at >= ? AND sent_at < ?" in query_args[0]
    assert query_args[1][0] < query_args[1][1]


def test_get_messages_for_day_returns_empty_on_exception(monkeypatch):
    monkeypatch.setattr(message_log, "_last_pull_monotonic", None)
    fake_conn = Mock()
    fake_conn.pull.side_effect = RuntimeError("panne réseau")
    with patch("message_log._connect", return_value=(fake_conn, True)):
        result = message_log.get_messages_for_day(date(2026, 8, 1))
    assert result == []


def test_get_messages_for_day_works_on_local_fallback_without_pull(monkeypatch):
    # Repli SQLite pur : pas de .pull() à appeler du tout (n'existe pas sur
    # cette connexion) — doit quand même lire/retourner les lignes locales.
    fake_conn = Mock(spec=["execute"])
    fake_conn.execute.return_value.fetchall.return_value = [
        {"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "canal", "raw_text": "🚨 Test"},
    ]
    with patch("message_log._connect", return_value=(fake_conn, False)):
        result = message_log.get_messages_for_day(date(2026, 8, 1))

    assert result == [{"id": 1, "sent_at": "2026-08-01T10:00:00+00:00", "chat_target": "canal", "raw_text": "🚨 Test"}]


# --- Throttle anti-quota Turso (voir commentaire au-dessus de _PULL_MIN_INTERVAL_SECONDS) ---

def test_get_messages_for_day_skips_pull_within_throttle_window(monkeypatch):
    monkeypatch.setattr(message_log, "_last_pull_monotonic", message_log.time_module.monotonic())
    fake_conn = Mock()
    fake_conn.execute.return_value.fetchall.return_value = []
    with patch("message_log._connect", return_value=(fake_conn, True)):
        message_log.get_messages_for_day(date(2026, 8, 1))

    fake_conn.pull.assert_not_called()


def test_get_messages_for_day_pulls_again_after_throttle_window(monkeypatch):
    monkeypatch.setattr(
        message_log, "_last_pull_monotonic", message_log.time_module.monotonic() - message_log._PULL_MIN_INTERVAL_SECONDS
    )
    fake_conn = Mock()
    fake_conn.execute.return_value.fetchall.return_value = []
    with patch("message_log._connect", return_value=(fake_conn, True)):
        message_log.get_messages_for_day(date(2026, 8, 1))

    fake_conn.pull.assert_called_once()
