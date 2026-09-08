"""
Journal durable des messages Telegram réellement envoyés (voir telegram_bot.
broadcast). Contrairement à alerts.db (SQLite local, remis à zéro à chaque
redéploiement Render — voir "Limites honnêtes" dans le README), ce journal vit
sur Turso (SQLite hébergé, gratuit, ne s'efface jamais) : la copie locale
(MESSAGE_LOG_REPLICA_PATH) n'est qu'une réplique jetable, la vraie donnée
durable est sur Turso Cloud.

Best-effort partout, comme le reste de l'agent face à une source externe
optionnelle : TURSO_DATABASE_URL/TURSO_AUTH_TOKEN vides -> le journal est
simplement désactivé, ne lève jamais, ne bloque jamais l'envoi Telegram
lui-même (voir telegram_bot.py). Si Turso est configuré mais injoignable
(constaté en conditions réelles le 07/09 : plan gratuit bloqué en lecture),
on retombe sur la réplique locale (MESSAGE_LOG_REPLICA_PATH) en SQLite pur
plutôt que d'abandonner : elle peut très bien contenir des messages déjà
archivés localement par CE process avant que Turso ne bloque (write local
toujours commité avant la tentative de push()), ou par un run précédent sur
cette même machine — les jeter reviendrait à perdre des données réellement
disponibles. Même raisonnement que db.get_conn(), voir ce module pour le
détail.
"""
from __future__ import annotations

import logging
import sqlite3
import time as time_module
from datetime import date, timezone
from datetime import datetime as dt

import config
import db

logger = logging.getLogger("message_log")

# Ce module a son propre pull() indépendant de celui de db.get_conn() — mais
# les deux tapent sur le MÊME compte Turso (même TURSO_DATABASE_URL/
# TURSO_AUTH_TOKEN, juste une réplique locale différente). Constaté en
# conditions réelles (07/09) : le throttle ajouté côté db.get_conn() n'a pas
# suffi à éviter un blocage des lectures Turso qui a duré 4 jours, parce que
# CE pull()-ci, appelé à chaque génération vidéo (et potentiellement par
# Render lui-même pour ses résumés quotidiens), n'était pas concerné et
# continuait de consommer le même quota sans limite. Même logique de
# throttle ici, voir db.py pour le raisonnement complet. Ne s'applique qu'à
# une connexion Turso réelle (voir is_turso ci-dessous) : le repli SQLite pur
# n'a pas de pull() à throttler.
#
# None (jamais 0.0) comme valeur de départ : le point de référence de
# time.monotonic() n'est PAS garanti être "l'allumage de la machine" — sur ce
# Mac, il redémarre en fait proche de zéro à chaque nouveau process (constaté
# en conditions réelles : ~0.05 juste après le démarrage de l'interpréteur).
# Avec 0.0 comme sentinelle, un script court (video_scripts.py tourne en
# quelques secondes) ne voit JAMAIS `now - _last_pull_monotonic >= 60`
# devenir vrai : le throttle bloquait alors silencieusement tout pull() en
# usage CLI normal, même une fois Turso débloqué. None lève l'ambiguïté :
# "jamais pullé" déclenche toujours un premier pull, quelle que soit
# l'échelle absolue de monotonic() sur la plateforme.
_PULL_MIN_INTERVAL_SECONDS = 60
_last_pull_monotonic: float | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    chat_target TEXT NOT NULL,
    raw_text TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_messages_dedup
    ON telegram_messages(sent_at, chat_target, raw_text);
"""


def _now_iso() -> str:
    return dt.now(timezone.utc).isoformat()


def _connect() -> tuple[object | None, bool]:
    """Renvoie (connexion prête à l'emploi, is_turso), ou (None, False) si le
    journal n'est ni configuré ni joignable d'aucune façon — jamais
    d'exception. is_turso indique si la connexion supporte .pull()/.push()
    (repli SQLite pur sinon, sans ces méthodes)."""
    if not config.TURSO_DATABASE_URL or not config.TURSO_AUTH_TOKEN:
        return None, False
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
        return conn, True
    except Exception as exc:  # noqa: BLE001 - on tente le repli local ci-dessous
        logger.warning("Turso indisponible (%s), tentative sur la réplique locale existante.", exc)

    try:
        conn = sqlite3.connect(config.MESSAGE_LOG_REPLICA_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn, False
    except Exception as exc:  # noqa: BLE001 - journal optionnel, jamais bloquant
        logger.warning("Journal local également indisponible (%s), message non archivé.", exc)
        return None, False


def log_message(chat_target: str, raw_text: str) -> None:
    """Archive un message réellement envoyé sur Telegram. `chat_target` :
    "perso" ou "canal" (voir telegram_bot.broadcast). Ne lève jamais."""
    conn, is_turso = _connect()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO telegram_messages (sent_at, chat_target, raw_text) VALUES (?, ?, ?)",
            (_now_iso(), chat_target, raw_text),
        )
        conn.commit()
        if is_turso:
            conn.push()
    except Exception as exc:  # noqa: BLE001 - ne doit jamais faire échouer l'envoi Telegram
        logger.warning("Échec d'archivage du message dans le journal: %s", exc)


def replay_message(sent_at: str, chat_target: str, raw_text: str) -> None:
    """Réinsère un message déjà envoyé, avec son sent_at d'origine (pas
    "maintenant") — utilisé par render_sync.py pour relayer, via /sync, les
    messages captés par Render sans dépendre d'une synchro Turso qui marche
    (voir _connect : Turso a bloqué toutes les lectures 4 jours d'affilée le
    07/09, alors que ces mêmes messages étaient déjà écrits localement côté
    Render). INSERT OR IGNORE + index unique sur (sent_at, chat_target,
    raw_text) en tête de fichier : ré-appeler avec le même message plusieurs
    fois (re-synchros répétées) ne crée pas de doublon. Ne lève jamais."""
    conn, is_turso = _connect()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_messages (sent_at, chat_target, raw_text) VALUES (?, ?, ?)",
            (sent_at, chat_target, raw_text),
        )
        conn.commit()
        if is_turso:
            conn.push()
    except Exception as exc:  # noqa: BLE001 - ne doit jamais bloquer la synchro Render
        logger.warning("Échec de relais d'un message archivé: %s", exc)


def get_messages_for_day(target_date: date) -> list[dict]:
    """Messages réellement envoyés ce jour-là, triés chronologiquement. []
    si le journal n'est pas configuré/joignable ou vide — jamais d'exception."""
    conn, is_turso = _connect()
    if conn is None:
        return []
    global _last_pull_monotonic
    try:
        if is_turso:
            now = time_module.monotonic()
            if _last_pull_monotonic is None or now - _last_pull_monotonic >= _PULL_MIN_INTERVAL_SECONDS:
                conn.pull()
                _last_pull_monotonic = now
        day_start_utc, day_end_utc = db.local_day_bounds_utc(target_date)
        rows = conn.execute(
            "SELECT * FROM telegram_messages WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at ASC",
            (day_start_utc.isoformat(), day_end_utc.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - source optionnelle, jamais bloquante
        logger.warning("Lecture du journal impossible: %s", exc)
        return []
