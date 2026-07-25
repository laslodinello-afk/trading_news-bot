"""
Veille "breaking news" : tout ce qui n'est PAS dans le calendrier économique
mais qui peut faire bouger le marché (tweet choc, discours surprise, conflit
armé, attentat...).

Il n'existe pas d'API gratuite et fiable en vrai temps réel pour ça (l'API
Twitter/X est payante). On combine donc deux sources gratuites en best-effort :

- GDELT (aucune clé, gratuit, illimité, mais 5-15 min de délai)
- NewsAPI.org (clé gratuite, 100 req/jour, quelques minutes de délai)

Les résultats bruts sont ensuite filtrés par l'IA (voir ai_analyzer.py) pour
ne garder que ce qui a un vrai impact potentiel sur le trading — sinon le
volume de faux positifs rendrait les alertes inutilisables.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import requests

import config

logger = logging.getLogger("news_watcher")

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"
HEADERS = {"User-Agent": "Mozilla/5.0 (trading-news-agent; +https://github.com)"}
TIMEOUT = 15


def _news_key(url: str) -> str:
    return hashlib.md5(url.strip().lower().encode()).hexdigest()


def _build_keyword_query(keywords: list[str]) -> str:
    parts = [f'"{kw}"' if " " in kw else kw for kw in keywords]
    return "(" + " OR ".join(parts) + ")"


def _fetch_gdelt(lookback_minutes: int) -> list[dict]:
    query = _build_keyword_query(config.BREAKING_NEWS_KEYWORDS)
    params = {
        "query": f"{query} sourcelang:english",
        "mode": "ArtList",
        "maxrecords": 50,
        "format": "json",
        "sort": "DateDesc",
        "timespan": f"{max(lookback_minutes, 15)}min",
    }
    try:
        resp = requests.get(GDELT_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("GDELT indisponible: %s", exc)
        return []

    articles = data.get("articles", []) if isinstance(data, dict) else []
    results = []
    for art in articles:
        url = art.get("url")
        title = art.get("title")
        if not url or not title:
            continue
        results.append(
            {
                "title": title.strip(),
                "url": url.strip(),
                "source": art.get("domain") or "GDELT",
                "published_at": art.get("seendate"),
            }
        )
    return results


def _fetch_newsapi(lookback_minutes: int) -> list[dict]:
    if not config.NEWSAPI_KEY:
        return []
    query = " OR ".join(f'"{kw}"' if " " in kw else kw for kw in config.BREAKING_NEWS_KEYWORDS)
    since = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    params = {
        "q": query,
        "from": since,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 30,
        "apiKey": config.NEWSAPI_KEY,
    }
    try:
        resp = requests.get(NEWSAPI_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(data.get("message", "réponse NewsAPI inattendue"))
    except Exception as exc:
        logger.warning("NewsAPI indisponible: %s", exc)
        return []

    results = []
    for art in data.get("articles", []):
        url = art.get("url")
        title = art.get("title")
        if not url or not title:
            continue
        source = (art.get("source") or {}).get("name") or "NewsAPI"
        results.append(
            {
                "title": title.strip(),
                "url": url.strip(),
                "source": source,
                "published_at": art.get("publishedAt"),
            }
        )
    return results


def fetch_candidates() -> list[dict]:
    """
    Récupère les articles récents des deux sources et dédupe par URL.
    Ne fait AUCUN jugement de pertinence ni de filtrage "déjà envoyé" :
    ça reste au niveau de main.py (dédup DB) et ai_analyzer (pertinence).
    """
    lookback = config.BREAKING_NEWS_LOOKBACK_MINUTES
    combined = _fetch_gdelt(lookback) + _fetch_newsapi(lookback)

    seen_urls = set()
    deduped = []
    for art in combined:
        key = _news_key(art["url"])
        if key in seen_urls:
            continue
        seen_urls.add(key)
        art["news_key"] = key
        deduped.append(art)

    logger.info("Veille breaking news: %d articles bruts, %d uniques", len(combined), len(deduped))
    return deduped
