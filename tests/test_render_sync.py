"""
render_sync.sync_from_render() : best-effort, jamais de réseau réel ici
(requests.get mocké). Vérifie le repli propre (False) quand la synchro n'est
pas configurée/échoue, et que les données reçues sont bien écrites en local
via les mêmes fonctions db.*/message_log.* que le reste de l'agent.
"""
from datetime import date, datetime, time, timezone
from unittest.mock import Mock, patch

import config
import db
import message_log
import render_sync


def test_sync_from_render_skips_when_not_configured(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")
    with patch("render_sync.requests.get") as mock_get:
        result = render_sync.sync_from_render(date(2026, 7, 28))
    assert result is False
    mock_get.assert_not_called()


def test_sync_from_render_skips_when_no_key(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "")
    with patch("render_sync.requests.get") as mock_get:
        result = render_sync.sync_from_render(date(2026, 7, 28))
    assert result is False
    mock_get.assert_not_called()


def test_sync_from_render_returns_false_on_network_error(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")
    with patch("render_sync.requests.get", side_effect=ConnectionError("indisponible")):
        result = render_sync.sync_from_render(date(2026, 7, 28))
    assert result is False


def test_sync_from_render_returns_false_on_http_error(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")
    fake_resp = Mock()
    fake_resp.raise_for_status.side_effect = Exception("401 unauthorized")
    with patch("render_sync.requests.get", return_value=fake_resp):
        result = render_sync.sync_from_render(date(2026, 7, 28))
    assert result is False


def test_sync_from_render_upserts_events_and_news(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")

    # date.today() et non une date fixe : db.mark_sent_news() (appelée par
    # sync_from_render en aval) horodate toujours sent_at avec l'heure réelle,
    # donc la fenêtre interrogée après coup doit couvrir "maintenant".
    target_date = date.today()
    event_dt_utc = datetime.combine(target_date, time(14, 30), tzinfo=timezone.utc)

    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = {
        "date": target_date.isoformat(),
        "events": [
            {
                "event_key": "remote_event", "title": "Non-Farm Payrolls (NFP)", "currency": "USD",
                "impact": "High", "event_dt_utc": event_dt_utc.isoformat(),
                "forecast": "180K", "previous": "227K", "actual": "142K",
            }
        ],
        "news": [
            {"news_key": "remote_news", "sent_at": "peu importe, pas utilisé par sync_from_render",
             "title": "Déclaration surprise de la Fed", "resume": "Résumé test."}
        ],
    }

    with patch("render_sync.requests.get", return_value=fake_resp) as mock_get:
        result = render_sync.sync_from_render(target_date)

    assert result is True
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"] == {"X-Sync-Key": "une-cle"}
    assert call_kwargs["params"] == {"date": target_date.isoformat()}
    assert mock_get.call_args.args[0] == "https://example.onrender.com/sync"

    day_start, day_end = db.local_day_bounds_utc(target_date)
    events = db.get_events_for_day(day_start, day_end)
    assert len(events) == 1
    assert events[0]["actual"] == "142K"
    news = db.get_news_for_day(day_start, day_end)
    assert len(news) == 1
    assert news[0]["title"] == "Déclaration surprise de la Fed"


def test_sync_from_render_replays_messages(monkeypatch, temp_db, tmp_path):
    # Turso "configuré mais injoignable" (pas "non configuré") pour que
    # message_log._connect() tente vraiment le repli SQLite local plutôt que
    # de se désactiver immédiatement (voir message_log.py) — c'est le
    # scénario réel du 07/09 que ce relais est censé couvrir.
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    monkeypatch.setattr(config, "MESSAGE_LOG_REPLICA_PATH", str(tmp_path / "message_log.db"))
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")

    target_date = date(2026, 9, 6)
    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = {
        "date": target_date.isoformat(),
        "events": [],
        "news": [],
        "messages": [
            {
                "sent_at": "2026-09-06T16:00:52.544125+00:00",
                "chat_target": "canal",
                "raw_text": "🚨 *Breaking News* ⭐⭐⭐\n📰 Test relayé depuis Render",
            }
        ],
    }

    with patch("render_sync.requests.get", return_value=fake_resp), \
         patch("turso.lib_sync.connect_sync", side_effect=ConnectionError("injoignable")):
        result = render_sync.sync_from_render(target_date)
    assert result is True

    messages = message_log.get_messages_for_day(target_date)
    assert len(messages) == 1
    assert messages[0]["raw_text"] == "🚨 *Breaking News* ⭐⭐⭐\n📰 Test relayé depuis Render"


def test_sync_from_render_replaying_messages_twice_does_not_duplicate(monkeypatch, temp_db, tmp_path):
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "une-cle")
    monkeypatch.setattr(config, "MESSAGE_LOG_REPLICA_PATH", str(tmp_path / "message_log.db"))
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")

    target_date = date(2026, 9, 6)
    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = {
        "date": target_date.isoformat(),
        "events": [],
        "news": [],
        "messages": [
            {"sent_at": "2026-09-06T16:00:52.544125+00:00", "chat_target": "canal", "raw_text": "Même message"}
        ],
    }

    with patch("render_sync.requests.get", return_value=fake_resp), \
         patch("turso.lib_sync.connect_sync", side_effect=ConnectionError("injoignable")):
        render_sync.sync_from_render(target_date)
        render_sync.sync_from_render(target_date)  # re-synchro (ex: rattrapage) : ne doit pas dupliquer

    messages = message_log.get_messages_for_day(target_date)
    assert len(messages) == 1


def test_sync_from_render_strips_trailing_slash_from_url(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com/")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")
    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = {"date": "2026-07-28", "events": [], "news": []}
    with patch("render_sync.requests.get", return_value=fake_resp) as mock_get:
        render_sync.sync_from_render(date(2026, 7, 28))
    assert mock_get.call_args.args[0] == "https://example.onrender.com/sync"


