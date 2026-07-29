"""
Couche SQLite : cache du calendrier économique + déduplication des alertes déjà
envoyées (sinon un redémarrage ou un tick de scheduler renverrait tout en double).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    currency TEXT NOT NULL,
    impact TEXT NOT NULL,
    event_dt_utc TEXT NOT NULL,
    forecast TEXT,
    previous TEXT,
    actual TEXT,
    ai_reclassified INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_alerts (
    event_key TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (event_key, alert_type)
);

CREATE TABLE IF NOT EXISTS sent_news (
    news_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL,
    title TEXT,
    resume TEXT
);

CREATE TABLE IF NOT EXISTS error_alerts (
    error_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alphavantage_cache (
    function TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes sur une base existante (CREATE TABLE IF NOT
    EXISTS ne modifie pas une table déjà présente)."""
    existing_news = {row["name"] for row in conn.execute("PRAGMA table_info(sent_news)")}
    if "title" not in existing_news:
        conn.execute("ALTER TABLE sent_news ADD COLUMN title TEXT")
    if "resume" not in existing_news:
        conn.execute("ALTER TABLE sent_news ADD COLUMN resume TEXT")

    existing_events = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "ai_reclassified" not in existing_events:
        conn.execute("ALTER TABLE events ADD COLUMN ai_reclassified INTEGER NOT NULL DEFAULT 0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_day_bounds_utc(local_date: date) -> tuple[datetime, datetime]:
    """Bornes UTC [début, fin) d'une journée calendaire locale (fuseau config.TIMEZONE),
    pour les requêtes get_events_for_day / get_news_for_day."""
    day_start_local = datetime.combine(local_date, time.min, tzinfo=config.TIMEZONE)
    day_end_local = day_start_local + timedelta(days=1)
    return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)


# --- events (cache calendrier) ------------------------------------------------

def upsert_event(event: dict) -> None:
    """
    event peut inclure "ai_reclassified" (bool) : main.py n'appelle cette
    fonction que pour les events retenus (High/Medium natifs, ou Low upgradés
    par l'IA ce cycle-ci) — un event Low non retenu n'est simplement jamais
    upserté, donc un event déjà upgradé ne peut pas être "redescendu" par un
    refresh ultérieur qui le reverrait Low sans le ré-upgrader.
    """
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (event_key, title, currency, impact, event_dt_utc,
                                 forecast, previous, actual, ai_reclassified, updated_at)
            VALUES (:event_key, :title, :currency, :impact, :event_dt_utc,
                    :forecast, :previous, :actual, :ai_reclassified, :updated_at)
            ON CONFLICT(event_key) DO UPDATE SET
                title=excluded.title,
                currency=excluded.currency,
                impact=excluded.impact,
                event_dt_utc=excluded.event_dt_utc,
                forecast=excluded.forecast,
                previous=excluded.previous,
                actual=COALESCE(excluded.actual, events.actual),
                ai_reclassified=excluded.ai_reclassified,
                updated_at=excluded.updated_at
            """,
            {**event, "ai_reclassified": int(bool(event.get("ai_reclassified", False))), "updated_at": _now_iso()},
        )


def set_event_actual(event_key: str, actual: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET actual=?, updated_at=? WHERE event_key=?",
            (actual, _now_iso(), event_key),
        )


def get_events_for_day(day_start_utc: datetime, day_end_utc: datetime) -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT * FROM events
            WHERE event_dt_utc >= ? AND event_dt_utc < ?
              AND impact IN ('High', 'Medium')
            ORDER BY event_dt_utc ASC
            """,
            (day_start_utc.isoformat(), day_end_utc.isoformat()),
        )
        return cur.fetchall()


def get_events_needing_before_alert(window_start_utc: datetime, window_end_utc: datetime) -> list[sqlite3.Row]:
    """News High/Medium dont le déclenchement tombe dans la fenêtre donnée."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT e.* FROM events e
            LEFT JOIN sent_alerts sa ON sa.event_key = e.event_key AND sa.alert_type = 'before'
            WHERE e.impact IN ('High', 'Medium')
              AND e.event_dt_utc >= ? AND e.event_dt_utc < ?
              AND sa.event_key IS NULL
            ORDER BY e.event_dt_utc ASC
            """,
            (window_start_utc.isoformat(), window_end_utc.isoformat()),
        )
        return cur.fetchall()


def get_events_needing_after_alert(published_before_utc: datetime) -> list[sqlite3.Row]:
    """
    News High/Medium déjà publiées (event_dt_utc <= maintenant) sans alerte 'after'.
    Bornée à AFTER_ALERT_MAX_AGE_MINUTES pour ne jamais alerter sur de vieilles news
    (typiquement au tout premier démarrage, quand le calendrier contient des jours passés).
    """
    oldest_allowed = published_before_utc - timedelta(minutes=config.AFTER_ALERT_MAX_AGE_MINUTES)
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT e.* FROM events e
            LEFT JOIN sent_alerts sa ON sa.event_key = e.event_key AND sa.alert_type = 'after'
            WHERE e.impact IN ('High', 'Medium')
              AND e.event_dt_utc <= ? AND e.event_dt_utc > ?
              AND sa.event_key IS NULL
            ORDER BY e.event_dt_utc ASC
            """,
            (published_before_utc.isoformat(), oldest_allowed.isoformat()),
        )
        return cur.fetchall()


# --- dédup alertes calendrier --------------------------------------------------

def already_sent(event_key: str, alert_type: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM sent_alerts WHERE event_key=? AND alert_type=?",
            (event_key, alert_type),
        )
        return cur.fetchone() is not None


def mark_sent(event_key: str, alert_type: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_alerts (event_key, alert_type, sent_at) VALUES (?, ?, ?)",
            (event_key, alert_type, _now_iso()),
        )


# --- dédup breaking news --------------------------------------------------------

def already_sent_news(news_key: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM sent_news WHERE news_key=?", (news_key,))
        return cur.fetchone() is not None


def mark_sent_news(news_key: str, title: str | None = None, resume: str | None = None) -> None:
    """
    title/resume ne sont renseignés que pour les articles jugés pertinents par
    l'IA (ceux réellement envoyés) : ça permet au débrief du soir de les
    récapituler. Les candidats écartés restent avec title/resume=NULL.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_news (news_key, sent_at, title, resume) VALUES (?, ?, ?, ?)",
            (news_key, _now_iso(), title, resume),
        )


def get_recently_sent_titles(within_hours: int) -> list[str]:
    """
    Titres des breaking news réellement envoyées récemment (title non NULL).
    Sert à repérer un article quasi identique déjà envoyé sous une URL
    différente (ex: un flash republié par la source avec un nouveau lien) —
    voir main.py, _is_duplicate_title.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT title FROM sent_news WHERE sent_at >= ? AND title IS NOT NULL",
            (cutoff,),
        )
        return [row["title"] for row in cur.fetchall()]


def get_news_for_day(day_start_utc: datetime, day_end_utc: datetime) -> list[sqlite3.Row]:
    """Breaking news réellement envoyées (title non NULL) dans la fenêtre donnée."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT * FROM sent_news
            WHERE sent_at >= ? AND sent_at < ? AND title IS NOT NULL
            ORDER BY sent_at ASC
            """,
            (day_start_utc.isoformat(), day_end_utc.isoformat()),
        )
        return cur.fetchall()


# --- throttle des alertes d'erreur ---------------------------------------------

def error_recently_sent(error_key: str, within_minutes: int = None) -> bool:
    within_minutes = within_minutes or config.ERROR_ALERT_THROTTLE_MINUTES
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT sent_at FROM error_alerts WHERE error_key=?",
            (error_key,),
        )
        row = cur.fetchone()
        if not row:
            return False
        last = datetime.fromisoformat(row["sent_at"])
        return datetime.now(timezone.utc) - last < timedelta(minutes=within_minutes)


def mark_error_sent(error_key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO error_alerts (error_key, sent_at) VALUES (?, ?)
            ON CONFLICT(error_key) DO UPDATE SET sent_at=excluded.sent_at
            """,
            (error_key, _now_iso()),
        )


# --- cache Alpha Vantage (résultats réels USD) ----------------------------------

def get_alphavantage_cache(function: str, max_age_hours: float) -> list | None:
    """None si absent ou plus vieux que max_age_hours (force un nouvel appel API)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT data_json, fetched_at FROM alphavantage_cache WHERE function=?", (function,)
        ).fetchone()
    if not row:
        return None
    age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return json.loads(row["data_json"])


def set_alphavantage_cache(function: str, data: list) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO alphavantage_cache (function, data_json, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(function) DO UPDATE SET data_json=excluded.data_json, fetched_at=excluded.fetched_at
            """,
            (function, json.dumps(data), _now_iso()),
        )
