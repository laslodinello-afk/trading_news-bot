"""
Synchronise la base locale avec les vraies données du jour depuis le service
Render déployé (voir main.py, endpoint /sync) avant une génération DEBRIEF
locale — pour que le script vidéo reflète ce que le bot a réellement capté et
envoyé sur Telegram (events confirmés + breaking news jugées pertinentes),
plutôt qu'une reconstruction partielle à partir des sources brutes en local.

Best-effort, comme le reste de l'agent face à une source externe : ne lève
jamais. RENDER_SYNC_URL ou SYNC_API_KEY vide, service endormi, mauvaise clé,
réseau indisponible -> False, l'appelant se rabat sur le rafraîchissement
local habituel (voir generate_debrief.sh) plutôt que de bloquer.
"""
from __future__ import annotations

import logging
from datetime import date

import requests

import config
import db

logger = logging.getLogger("render_sync")

TIMEOUT = 20  # le service peut être endormi et mettre du temps à se réveiller


def sync_from_render(target_date: date) -> bool:
    """Renvoie True si la synchro a abouti (données éventuellement vides mais
    requête traitée), False si elle n'a pas pu être tentée ou a échoué."""
    if not config.RENDER_SYNC_URL or not config.SYNC_API_KEY:
        return False

    try:
        resp = requests.get(
            f"{config.RENDER_SYNC_URL.rstrip('/')}/sync",
            params={"date": target_date.isoformat()},
            headers={"X-Sync-Key": config.SYNC_API_KEY},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - jamais bloquer sur une source externe optionnelle
        logger.warning("Synchro Render indisponible (%s), on continue avec la base locale.", exc)
        return False

    events = payload.get("events", [])
    for event in events:
        db.upsert_event(event)

    # /sync ne renvoie que des news déjà retenues (voir db.get_news_for_day,
    # filtrée sur title IS NOT NULL côté serveur) : toujours un titre ici.
    news_items = payload.get("news", [])
    for article in news_items:
        db.mark_sent_news(article["news_key"], title=article["title"], resume=article.get("resume"))

    logger.info(
        "Synchro Render OK pour %s : %d evenement(s), %d breaking news.",
        target_date, len(events), len(news_items),
    )
    return True
