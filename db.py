"""
Couche SQLite : cache du calendrier économique + déduplication des alertes déjà
envoyées (sinon un redémarrage ou un tick de scheduler renverrait tout en double).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_alerts (
    error_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- events (cache calendrier) ------------------------------------------------

def upsert_event(event: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (event_key, title, currency, impact, event_dt_utc,
                                 forecast, previous, actual, updated_at)
            VALUES (:event_key, :title, :currency, :impact, :event_dt_utc,
                    :forecast, :previous, :actual, :updated_at)
            ON CONFLICT(event_key) DO UPDATE SET
                title=excluded.title,
                currency=excluded.currency,
                impact=excluded.impact,
                event_dt_utc=excluded.event_dt_utc,
                forecast=excluded.forecast,
                previous=excluded.previous,
                actual=COALESCE(excluded.actual, events.actual),
                updated_at=excluded.updated_at
            """,
            {**event, "updated_at": _now_iso()},
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
    """News à fort impact (High) dont le déclenchement tombe dans la fenêtre donnée."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT e.* FROM events e
            LEFT JOIN sent_alerts sa ON sa.event_key = e.event_key AND sa.alert_type = 'before'
            WHERE e.impact = 'High'
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


def mark_sent_news(news_key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_news (news_key, sent_at) VALUES (?, ?)",
            (news_key, _now_iso()),
        )


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
