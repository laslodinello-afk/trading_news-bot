"""
Journal durable des messages Telegram réellement envoyés (voir telegram_bot.
broadcast). Contrairement à alerts.db (SQLite local, remis à zéro à chaque
redéploiement Render — voir "Limites honnêtes" dans le README), ce journal vit
sur Turso (SQLite hébergé, gratuit, ne s'efface jamais) : la copie locale
(MESSAGE_LOG_REPLICA_PATH) n'est qu'une réplique jetable, la vraie donnée
durable est sur Turso Cloud.

Best-effort partout, comme le reste de l'agent face à une source externe
optionnelle : TURSO_DATABASE_URL/TURSO_AUTH_TOKEN vides, ou service
injoignable -> le journal est simplement désactivé, ne lève jamais, ne bloque
jamais l'envoi Telegram lui-même (voir telegram_bot.py).
"""
from __future__ import annotations

import logging
from datetime import date, timezone
from datetime import datetime as dt

import config
import db

logger = logging.getLogger("message_log")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    chat_target TEXT NOT NULL,
    raw_text TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return dt.now(timezone.utc).isoformat()


def _connect():
    """Renvoie une connexion Turso prête à l'emploi (schéma déjà créé), ou None
    si le journal n'est pas configuré/joignable — jamais d'exception."""
    if not config.TURSO_DATABASE_URL or not config.TURSO_AUTH_TOKEN:
        return None
    try:
        from turso.lib import Row
        from turso.lib_sync import connect_sync

        conn = connect_sync(
            config.MESSAGE_LOG_REPLICA_PATH,
            remote_url=config.TURSO_DATABASE_URL,
            auth_token=config.TURSO_AUTH_TOKEN,
        )
        conn.row_factory = Row
        conn.executescript(_SCHEMA)
        return conn
    except Exception as exc:  # noqa: BLE001 - journal optionnel, jamais bloquant
        logger.warning("Journal Turso indisponible (%s), message non archivé.", exc)
        return None


def log_message(chat_target: str, raw_text: str) -> None:
    """Archive un message réellement envoyé sur Telegram. `chat_target` :
    "perso" ou "canal" (voir telegram_bot.broadcast). Ne lève jamais."""
    conn = _connect()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO telegram_messages (sent_at, chat_target, raw_text) VALUES (?, ?, ?)",
            (_now_iso(), chat_target, raw_text),
        )
        conn.commit()
        conn.push()
    except Exception as exc:  # noqa: BLE001 - ne doit jamais faire échouer l'envoi Telegram
        logger.warning("Échec d'archivage du message dans le journal Turso: %s", exc)


def get_messages_for_day(target_date: date) -> list[dict]:
    """Messages réellement envoyés ce jour-là, triés chronologiquement. []
    si le journal n'est pas configuré/joignable ou vide — jamais d'exception."""
    conn = _connect()
    if conn is None:
        return []
    try:
        conn.pull()
        day_start_utc, day_end_utc = db.local_day_bounds_utc(target_date)
        rows = conn.execute(
            "SELECT * FROM telegram_messages WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at ASC",
            (day_start_utc.isoformat(), day_end_utc.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - source optionnelle, jamais bloquante
        logger.warning("Lecture du journal Turso impossible: %s", exc)
        return []
