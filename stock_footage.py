"""
Recherche + téléchargement + cache local de clips vidéo libres de droit (Pexels)
pour servir de fond animé au rendu vidéo (video_renderer.py, --render). Optionnel :
sans PEXELS_API_KEY, toutes les fonctions publiques renvoient [] plutôt que de
lever — l'appelant se rabat alors sur un fond uni, même principe que les clés FMP/
NewsAPI déjà optionnelles dans ce projet (voir config.py).
"""
from __future__ import annotations

import logging
import os
import re

import requests

import config

logger = logging.getLogger("stock_footage")

_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
_TIMEOUT = 20


def _theme_slug(theme_query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", theme_query.lower()).strip("_")


def _cached_clips(theme_query: str) -> list[str]:
    theme_dir = os.path.join(config.STOCK_FOOTAGE_DIR, _theme_slug(theme_query))
    if not os.path.isdir(theme_dir):
        return []
    return sorted(
        os.path.join(theme_dir, name) for name in os.listdir(theme_dir) if name.endswith(".mp4")
    )


def _pick_video_file(video_files: list[dict]) -> dict | None:
    """Choisit le fichier mp4 le plus proche de 1080px de large — inutile de
    télécharger de la 4K pour une vidéo verticale destinée au mobile."""
    candidates = [f for f in video_files if f.get("file_type") == "video/mp4" and f.get("width")]
    if not candidates:
        return None
    return min(candidates, key=lambda f: abs(f["width"] - 1080))


def search_and_download(theme_query: str, count: int = 5) -> list[str]:
    """Cherche et télécharge jusqu'à `count` clips Pexels (orientation portrait)
    pour ce thème, dans STOCK_FOOTAGE_DIR/<theme_slug>/. Renvoie les chemins
    téléchargés (peut être < count). Ne lève jamais : [] sur tout échec (clé
    absente, réseau, réponse inattendue) — jamais bloquant pour l'appelant."""
    if not config.PEXELS_API_KEY:
        return []

    slug = _theme_slug(theme_query)
    theme_dir = os.path.join(config.STOCK_FOOTAGE_DIR, slug)

    try:
        resp = requests.get(
            _SEARCH_URL,
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": theme_query, "orientation": "portrait", "per_page": count},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("videos", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Recherche Pexels échouée pour '%s': %s", theme_query, exc)
        return []

    if not results:
        logger.info("Aucun résultat Pexels pour le thème '%s'.", theme_query)
        return []

    os.makedirs(theme_dir, exist_ok=True)
    downloaded = []
    for video in results[:count]:
        video_file = _pick_video_file(video.get("video_files", []))
        if not video_file:
            continue
        out_path = os.path.join(theme_dir, f"{slug}_{video.get('id')}.mp4")
        if os.path.exists(out_path):
            downloaded.append(out_path)
            continue
        try:
            with requests.get(video_file["link"], timeout=_TIMEOUT, stream=True) as media_resp:
                media_resp.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in media_resp.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            downloaded.append(out_path)
        except requests.RequestException as exc:
            logger.warning("Téléchargement Pexels échoué (id=%s): %s", video.get("id"), exc)
            if os.path.exists(out_path):
                os.remove(out_path)  # pas de fichier partiel/corrompu en cache

    logger.info("Thème '%s' : %d clip(s) téléchargé(s).", theme_query, len(downloaded))
    return downloaded


def ensure_theme_cached(theme_query: str, minimum: int = 3, fetch_count: int | None = None) -> list[str]:
    """Renvoie les clips déjà en cache pour ce thème ; complète via Pexels si moins
    de `minimum` sont disponibles. [] si PEXELS_API_KEY absente ou si tout échoue
    — l'appelant (video_renderer._background_clip) se rabat alors sur un fond uni.

    `fetch_count` : nombre de clips à télécharger si le cache doit être complété.
    Par défaut `max(minimum, 5)` — pensé pour un thème générique réutilisé souvent
    (fond de secours). Un appelant passant un mot-clé de contenu ponctuel (généré
    par l'IA pour un bloc précis, qui ne reviendra peut-être jamais) devrait passer
    une petite valeur explicite pour éviter de télécharger inutilement beaucoup de
    clips pour un thème à usage unique (voir config.STOCK_FOOTAGE_CONTENT_CLIP_COUNT)."""
    cached = _cached_clips(theme_query)
    if len(cached) >= minimum or not config.PEXELS_API_KEY:
        return cached
    search_and_download(theme_query, count=fetch_count or max(minimum, 5))
    return _cached_clips(theme_query)
