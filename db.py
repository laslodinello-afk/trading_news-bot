"""
Couche SQLite : cache du calendrier économique + déduplication des alertes déjà
envoyées (sinon un redémarrage ou un tick de scheduler renverrait tout en double).

Persistance : Turso (SQLite hébergé, gratuit, ne s'efface jamais) si
TURSO_DATABASE_URL/TURSO_AUTH_TOKEN sont configurés — sinon repli sur SQLite
purement local. Sans Turso, alerts.db repart de zéro à chaque redéploiement
Render (disque éphémère) : la mémoire anti-doublons est perdue, une alerte
déjà envoyée peut être renvoyée si un redéploiement survient peu après
(constaté en conditions réelles). Avec Turso, get_conn() tire l'état le plus
récent avant chaque connexion (pull) et publie ses écritures immédiatement
(push) — la mémoire anti-doublons survit à n'importe quel redémarrage. Voir
message_log.py pour le même principe appliqué au journal des messages
Telegram (table séparée, même base Turso).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone

import config

logger = logging.getLogger("db")

# APScheduler exécute les jobs sur des threads séparés (avant/après/breaking
# news peuvent tourner en même temps, constaté dans les logs). Le moteur de
# synchronisation Turso n'est pas conçu pour que plusieurs connexions
# accèdent à la MÊME réplique locale en parallèle : ça corrompt le suivi de
# génération ("protocol error: target_pull_gen > source_pull_gen"), et fait
# planter TOUS les jobs qui touchent la base — constaté en production. Ce
# verrou sérialise tout accès à get_conn() ; le coût est négligeable vu le
# faible volume de cet agent (quelques requêtes toutes les 5 min).
_conn_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_fr TEXT,
    currency TEXT NOT NULL,
    impact TEXT NOT NULL,
    event_dt_utc TEXT NOT NULL,
    forecast TEXT,
    previous TEXT,
    actual TEXT,
    speech_summary TEXT,
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

-- Une seule ligne (id=1) : horodatage du dernier debrief du soir envoye, pour
-- que le suivant couvre uniquement la periode ecoulee depuis le dernier
-- debrief, pas une journee calendaire fixe (voir main.py job_evening_debrief).
-- NB: pas de caracteres accentues ni d apostrophes dans ce commentaire. Le
-- tokenizer Turso plante quand un commentaire SQL situe apres une autre
-- instruction du script contient un caractere UTF-8 multi-octets ou une
-- apostrophe.
CREATE TABLE IF NOT EXISTS debrief_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_sent_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    """
    Turso si configuré et joignable, sinon SQLite purement local — jamais
    d'exception qui remonterait à l'appelant si Turso est indisponible (même
    philosophie que le reste de l'agent : une source externe qui tombe ne
    doit jamais bloquer le fonctionnement de base, voir message_log.py).

    Tout le corps tourne sous _conn_lock (voir plus haut) : un seul accès à
    la fois, même entre threads différents.
    """
    with _conn_lock:
        conn = None
        use_turso = False
        if config.TURSO_DATABASE_URL and config.TURSO_AUTH_TOKEN:
            try:
                from turso.lib import Row
                from turso.lib_sync import connect_sync

                conn = connect_sync(
                    config.DB_PATH,
                    remote_url=config.TURSO_DATABASE_URL,
                    auth_token=config.TURSO_AUTH_TOKEN,
                )
                conn.row_factory = Row
                conn.pull()
                use_turso = True
            except Exception as exc:  # noqa: BLE001 - repli local, jamais bloquant
                logger.warning("Turso indisponible (%s), repli sur SQLite local pour cette connexion.", exc)
                conn = None

        if conn is None:
            conn = sqlite3.connect(config.DB_PATH)
            conn.row_factory = sqlite3.Row

        try:
            yield conn
            conn.commit()
            if use_turso:
                conn.push()
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
    if "title_fr" not in existing_events:
        conn.execute("ALTER TABLE events ADD COLUMN title_fr TEXT")
    if "speech_summary" not in existing_events:
        conn.execute("ALTER TABLE events ADD COLUMN speech_summary TEXT")


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
    upserté par main.py. Mais un appelant qui ne suit pas cette discipline
    (ex. generate_debrief.sh, qui upserte tout ce que renvoie
    calendar_fetcher.refresh_calendar() sans repasser par l'IA) peut très bien
    rappeler upsert_event() avec l'impact natif "Low" pour un event déjà
    upgradé — constaté en conditions réelles : un event alerté sur Telegram en
    "Medium" (ai_reclassified=1) s'est fait redescendre en "Low" par un simple
    rafraîchissement local du calendrier, le faisant disparaître de
    get_events_for_day() (filtrée sur High/Medium) alors qu'il avait bien été
    communiqué. D'où la protection ci-dessous, dans le SQL lui-même plutôt que
    dans la discipline de l'appelant : un event déjà ai_reclassified=1 en base
    ne peut jamais être redescendu en impact ni "dé-reclassifié" par un upsert
    qui n'apporte pas lui-même une reclassification (ai_reclassified=1).

    "title_fr" (optionnel) : traduction affichée dans les messages, résolue
    par main.py (via get_title_translation/ai_analyzer.translate_event_titles)
    avant l'appel — jamais None sur un titre déjà traduit une fois, COALESCE
    ci-dessous pour ne jamais écraser une traduction existante par NULL si un
    refresh ultérieur omet le champ.
    """
    with get_conn() as conn:
        # Paramètres positionnels (?), pas nommés (:xxx) : le client Turso ne
        # supporte pas les paramètres nommés (constaté, ProgrammingError) —
        # contrairement à sqlite3 qui accepte les deux, donc jamais remarqué
        # avant de tester pour de vrai contre Turso.
        conn.execute(
            """
            INSERT INTO events (event_key, title, title_fr, currency, impact, event_dt_utc,
                                 forecast, previous, actual, ai_reclassified, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO UPDATE SET
                title=excluded.title,
                title_fr=COALESCE(excluded.title_fr, events.title_fr),
                currency=excluded.currency,
                impact=CASE
                    WHEN events.ai_reclassified = 1 AND excluded.ai_reclassified = 0 THEN events.impact
                    ELSE excluded.impact
                END,
                event_dt_utc=excluded.event_dt_utc,
                forecast=excluded.forecast,
                previous=excluded.previous,
                actual=COALESCE(excluded.actual, events.actual),
                ai_reclassified=MAX(events.ai_reclassified, excluded.ai_reclassified),
                updated_at=excluded.updated_at
            """,
            (
                event["event_key"],
                event["title"],
                event.get("title_fr"),
                event["currency"],
                event["impact"],
                event["event_dt_utc"],
                event.get("forecast"),
                event.get("previous"),
                event.get("actual"),
                int(bool(event.get("ai_reclassified", False))),
                _now_iso(),
            ),
        )


def set_event_actual(event_key: str, actual: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET actual=?, updated_at=? WHERE event_key=?",
            (actual, _now_iso(), event_key),
        )


def set_event_speech_summary(event_key: str, summary: str) -> None:
    """Jamais écrit par upsert_event (le calendrier source ne fournit pas ce
    champ) : uniquement rempli ici par job_check_after_alerts une fois le
    résumé du discours trouvé (voir ai_analyzer.search_speech_summary)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET speech_summary=?, updated_at=? WHERE event_key=?",
            (summary, _now_iso(), event_key),
        )


def get_title_translations(titles_en: list[str]) -> dict[str, str]:
    """
    Traductions déjà connues pour ces titres (n'importe quel event passé,
    peu importe la semaine — un titre comme "Non-Farm Payrolls (NFP)" revient
    identique chaque mois, pas la peine de le retraduire par l'IA à chaque
    fois). Ne renvoie que les titres effectivement trouvés.
    """
    if not titles_en:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in titles_en)
        rows = conn.execute(
            f"SELECT DISTINCT title, title_fr FROM events WHERE title IN ({placeholders}) AND title_fr IS NOT NULL",
            titles_en,
        )
        return {row["title"]: row["title_fr"] for row in rows}


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


# --- débrief du soir : couverture "depuis le dernier envoi" --------------------

def get_last_debrief_sent_at() -> datetime | None:
    """Horodatage d'envoi du tout dernier débrief du soir, ou None si jamais
    envoyé (premier lancement). Sert de borne de départ pour le débrief
    suivant plutôt qu'un jour calendaire fixe (minuit) : sinon, le créneau
    entre l'heure du débrief et minuit ne serait jamais couvert par aucun
    débrief (ni celui du soir même, déjà envoyé avant minuit, ni celui du
    lendemain, dont la fenêtre commence à minuit)."""
    with get_conn() as conn:
        row = conn.execute("SELECT last_sent_at FROM debrief_state WHERE id=1").fetchone()
    return datetime.fromisoformat(row["last_sent_at"]) if row else None


def set_last_debrief_sent_at() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO debrief_state (id, last_sent_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_sent_at=excluded.last_sent_at
            """,
            (_now_iso(),),
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
