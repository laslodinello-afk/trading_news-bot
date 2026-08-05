"""
Couvre upsert_event() : la protection contre la "redescente" d'un event déjà
reclassifié par l'IA (voir db.py, upsert_event — bug réel constaté : un
rafraîchissement local naïf, comme celui de generate_debrief.sh, écrasait
l'impact "Medium" upgradé par un impact "Low" natif, faisant disparaître
l'event de get_events_for_day() alors qu'il avait bien été alerté sur
Telegram).
"""
from datetime import date, datetime, time, timezone

import config
import db


def _event(event_key: str, impact: str, ai_reclassified: bool, **overrides) -> dict:
    event_dt = datetime.combine(date(2026, 8, 4), time(14, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc)
    base = {
        "event_key": event_key,
        "title": "API Weekly Statistical Bulletin",
        "currency": "USD",
        "impact": impact,
        "event_dt_utc": event_dt.isoformat(),
        "forecast": None,
        "previous": None,
        "actual": None,
        "ai_reclassified": ai_reclassified,
    }
    base.update(overrides)
    return base


def test_upsert_event_protects_reclassified_event_from_naive_downgrade(temp_db):
    db.upsert_event(_event("evt_1", "Medium", True))
    # Simule un refresh naïf (calendar_fetcher brut, sans IA) qui renvoie
    # l'event avec son impact natif "Low" et sans reclassification.
    db.upsert_event(_event("evt_1", "Low", False))

    day_start_utc, day_end_utc = db.local_day_bounds_utc(date(2026, 8, 4))
    rows = db.get_events_for_day(day_start_utc, day_end_utc)
    assert len(rows) == 1
    assert rows[0]["impact"] == "Medium"
    assert rows[0]["ai_reclassified"] == 1


def test_upsert_event_allows_genuine_upgrade_this_cycle(temp_db):
    db.upsert_event(_event("evt_2", "Low", False))
    db.upsert_event(_event("evt_2", "Medium", True))  # vraie reclassification IA ce cycle-ci

    day_start_utc, day_end_utc = db.local_day_bounds_utc(date(2026, 8, 4))
    rows = db.get_events_for_day(day_start_utc, day_end_utc)
    assert len(rows) == 1
    assert rows[0]["impact"] == "Medium"
    assert rows[0]["ai_reclassified"] == 1


def test_upsert_event_normal_refresh_without_prior_reclassification(temp_db):
    """Un event jamais reclassifié doit continuer à suivre normalement son
    impact natif d'un refresh à l'autre (pas de protection qui bloque à tort)."""
    db.upsert_event(_event("evt_3", "Medium", False, forecast="1.0%"))
    db.upsert_event(_event("evt_3", "High", False, forecast="1.2%"))

    day_start_utc, day_end_utc = db.local_day_bounds_utc(date(2026, 8, 4))
    rows = db.get_events_for_day(day_start_utc, day_end_utc)
    assert len(rows) == 1
    assert rows[0]["impact"] == "High"
    assert rows[0]["forecast"] == "1.2%"


def test_upsert_event_reclassified_flag_is_sticky(temp_db):
    """Une fois ai_reclassified=1, un upsert qui n'apporte pas lui-même une
    reclassification ne doit jamais remettre le flag à 0."""
    db.upsert_event(_event("evt_4", "Medium", True))
    db.upsert_event(_event("evt_4", "Medium", False))

    day_start_utc, day_end_utc = db.local_day_bounds_utc(date(2026, 8, 4))
    rows = db.get_events_for_day(day_start_utc, day_end_utc)
    assert rows[0]["ai_reclassified"] == 1
