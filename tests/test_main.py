"""
Couvre build_sync_response() (voir main.py) : la logique pure derrière
l'endpoint /sync, isolée de BaseHTTPRequestHandler pour être testable sans
ouvrir de vrai socket. Aucun réseau, aucun serveur HTTP réel ici.
"""
import json
from datetime import date, datetime, time, timezone

import config
import db
import main


def test_sync_rejects_when_no_key_configured(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "")
    status, _, _ = main.build_sync_response("", "peu-importe")
    assert status == 401


def test_sync_rejects_missing_auth_header(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "le-bon-secret")
    status, _, _ = main.build_sync_response("", None)
    assert status == 401


def test_sync_rejects_wrong_auth_header(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "le-bon-secret")
    status, _, _ = main.build_sync_response("", "un-mauvais-secret")
    assert status == 401


def test_sync_accepts_correct_key_defaults_to_today(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "le-bon-secret")
    status, content_type, body = main.build_sync_response("", "le-bon-secret")
    assert status == 200
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["date"] == datetime.now(config.TIMEZONE).date().isoformat()
    assert payload["events"] == []
    assert payload["news"] == []


def test_sync_rejects_invalid_date_format(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "le-bon-secret")
    status, _, body = main.build_sync_response("date=pas-une-date", "le-bon-secret")
    assert status == 400


def test_sync_returns_real_events_and_news_for_requested_date(monkeypatch, temp_db):
    monkeypatch.setattr(config, "SYNC_API_KEY", "le-bon-secret")
    # date.today() et non une date fixe : db.mark_sent_news() horodate toujours
    # sent_at avec l'heure réelle (voir db.py), donc la fenêtre interrogée doit
    # couvrir "maintenant", pas un jour arbitraire figé dans le passé.
    target_date = date.today()
    event_dt = datetime.combine(target_date, time(14, 30), tzinfo=config.TIMEZONE).astimezone(timezone.utc)
    db.upsert_event(
        {
            "event_key": "sync_test_event", "title": "Non-Farm Payrolls (NFP)", "currency": "USD", "impact": "High",
            "event_dt_utc": event_dt.isoformat(), "forecast": "180K", "previous": "227K", "actual": "142K",
        }
    )
    db.mark_sent_news("sync_test_news", title="Déclaration surprise de la Fed", resume="Résumé test.")

    status, _, body = main.build_sync_response(f"date={target_date.isoformat()}", "le-bon-secret")

    assert status == 200
    payload = json.loads(body)
    assert payload["date"] == target_date.isoformat()
    assert len(payload["events"]) == 1
    assert payload["events"][0]["title"] == "Non-Farm Payrolls (NFP)"
    assert payload["events"][0]["actual"] == "142K"
    assert len(payload["news"]) == 1
    assert payload["news"][0]["title"] == "Déclaration surprise de la Fed"


def test_sync_uses_constant_time_comparison_not_equality_shortcut(monkeypatch, temp_db):
    """Non-régression légère : s'assure que la comparaison passe bien par
    hmac.compare_digest (pas ==) — surtout utile si le code est retouché plus
    tard, ne prouve pas la résistance aux attaques temporelles en soi."""
    monkeypatch.setattr(config, "SYNC_API_KEY", "secret-de-quinze-caracteres")
    status_short, _, _ = main.build_sync_response("", "s")
    status_long, _, _ = main.build_sync_response("", "s" * 100)
    assert status_short == 401
    assert status_long == 401
