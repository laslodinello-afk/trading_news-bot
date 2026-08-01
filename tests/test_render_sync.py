"""
render_sync.sync_from_render() : best-effort, jamais de réseau réel ici
(requests.get mocké). Vérifie le repli propre (False) quand la synchro n'est
pas configurée/échoue, et que les données reçues sont bien écrites en local
via les mêmes fonctions db.* que le reste de l'agent.
"""
from datetime import date, datetime, time, timezone
from unittest.mock import Mock, patch

import config
import db
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


def test_sync_from_render_strips_trailing_slash_from_url(monkeypatch, temp_db):
    monkeypatch.setattr(config, "RENDER_SYNC_URL", "https://example.onrender.com/")
    monkeypatch.setattr(config, "SYNC_API_KEY", "une-cle")
    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = {"date": "2026-07-28", "events": [], "news": []}
    with patch("render_sync.requests.get", return_value=fake_resp) as mock_get:
        render_sync.sync_from_render(date(2026, 7, 28))
    assert mock_get.call_args.args[0] == "https://example.onrender.com/sync"


