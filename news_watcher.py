"""
Veille "breaking news" : tout ce qui n'est PAS dans le calendrier économique
mais qui peut faire bouger le marché (tweet choc, discours surprise, conflit
armé, attentat, actualité économique plus ordinaire...).

Sources combinées en best-effort :
- Flux RSS spécialisés forex/trading (InvestingLive, FXStreet) : gratuits,
  sans clé, GENUINEMENT temps réel (25-60 min de délai constaté, contre ~24h
  pour NewsAPI gratuit — voir plus bas). Déjà centrés sur le trading, donc
  moins de bruit à filtrer que les recherches par mot-clé ci-dessous.
  FXStreet bloque spécifiquement l'IP partagée de Render (403 constaté) : on
  passe donc par le cache maintenu par .github/workflows/refresh-fxstreet.yml
  (voir FXSTREET_CACHE_URL dans config.py), avec repli sur l'appel direct.
- GDELT (aucune clé, gratuit, illimité) : rate-limite l'IP partagée de Render
  en pratique (constaté), résultats peu fiables depuis ce type d'hébergeur.
- NewsAPI.org (clé gratuite, 100 req/jour) : le plan gratuit a ~24h de délai
  sur les articles disponibles (constaté, non documenté clairement par
  NewsAPI) — quasi inutile pour du "breaking" mais gardé en secours. Interrogé
  une fois par heure seulement (voir NEWSAPI_POLL_INTERVAL_MINUTES) : vu son
  délai propre de 24h, le vérifier plus souvent ne le rendrait pas plus frais,
  juste plus gourmand en quota.

Les résultats bruts sont ensuite filtrés par l'IA (voir ai_analyzer.py) pour
ne garder que ce qui a un vrai impact potentiel sur le trading — sinon le
volume de faux positifs rendrait les alertes inutilisables.
"""
from __future__ import annotations

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import config

logger = logging.getLogger("news_watcher")

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"
HEADERS = {"User-Agent": "Mozilla/5.0 (trading-news-agent; +https://github.com)"}
TIMEOUT = 15

# (nom affiché, URL du flux) — flux RSS publics, pas de clé requise.
RSS_FEEDS = [
    ("InvestingLive", "https://www.forexlive.com/feed/news"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
]


def _news_key(url: str) -> str:
    return hashlib.md5(url.strip().lower().encode()).hexdigest()


def _build_keyword_query(keywords: list[str]) -> str:
    parts = [f'"{kw}"' if " " in kw else kw for kw in keywords]
    return "(" + " OR ".join(parts) + ")"


def _fetch_gdelt(lookback_minutes: int) -> list[dict]:
    """
    GDELT rate-limite aussi bien l'IP partagée de Render que celle des
    runners GitHub Actions (testé : les deux reçoivent un 429/réponse non-JSON
    en production) — contrairement à ForexFactory, le relais via GitHub Action
    ne fonctionne pas pour cette source. On se contente donc d'un appel direct
    qui dégrade proprement (retourne []) si GDELT est indisponible ; NewsAPI
    (ci-dessous) reste la source fiable de la veille breaking news.
    """
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


# NewsAPI limite le paramètre "q" à 500 caractères (documenté). Avec la liste de
# mots-clés élargie (crypto, pétrole...), une seule requête OR-jointe dépasse cette
# limite (constaté : 570 caractères -> 400 Bad Request). On répartit donc les
# mots-clés en plusieurs groupes qui tournent au fil des passages : chaque
# mot-clé reste couvert, juste vérifié un peu moins souvent qu'à chaque tick.
NEWSAPI_MAX_QUERY_LEN = 450


def _chunk_keywords(keywords: list[str], max_len: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for kw in keywords:
        piece = f'"{kw}"' if " " in kw else kw
        added_len = len(piece) + (len(" OR ") if current else 0)
        if current and current_len + added_len > max_len:
            chunks.append(current)
            current, current_len = [], 0
            added_len = len(piece)
        current.append(kw)
        current_len += added_len
    if current:
        chunks.append(current)
    return chunks


def _current_keyword_chunk() -> list[str]:
    chunks = _chunk_keywords(config.BREAKING_NEWS_KEYWORDS, NEWSAPI_MAX_QUERY_LEN)
    if len(chunks) <= 1:
        return chunks[0] if chunks else []
    # Rotation stateless basée sur l'heure : pas besoin de persister un index.
    # Calée sur le rythme réel des appels NewsAPI (1x/heure), pas sur la boucle
    # rapide GDELT/RSS.
    slot = int(time.time() // (config.NEWSAPI_POLL_INTERVAL_MINUTES * 60))
    return chunks[slot % len(chunks)]


# Dernier appel NewsAPI réussi (mémoire du process, pas besoin de persister en
# base : au pire un redémarrage déclenche un appel un peu tôt, sans risque pour
# le quota 100/jour vu la marge — voir NEWSAPI_POLL_INTERVAL_MINUTES).
_last_newsapi_call_utc: datetime | None = None


def _fetch_newsapi(lookback_minutes: int) -> list[dict]:
    global _last_newsapi_call_utc
    if not config.NEWSAPI_KEY:
        return []

    now = datetime.now(timezone.utc)
    if _last_newsapi_call_utc is not None:
        elapsed_minutes = (now - _last_newsapi_call_utc).total_seconds() / 60
        if elapsed_minutes < config.NEWSAPI_POLL_INTERVAL_MINUTES:
            return []
    _last_newsapi_call_utc = now

    query = " OR ".join(f'"{kw}"' if " " in kw else kw for kw in _current_keyword_chunk())
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


def _fetch_rss_feed(source_name: str, url: str, lookback_minutes: int) -> list[dict]:
    """
    Parse un flux RSS 2.0 standard (xml.etree, stdlib — pas de dépendance
    supplémentaire) et ne garde que les items publiés dans la fenêtre de
    lookback. Ces flux étant déjà spécialisés forex/trading, pas de filtrage
    par mot-clé ici : tout item récent est un candidat, le tri de pertinence
    se fait plus loin par l'IA (ai_analyzer.filter_breaking_news).
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning("Flux RSS %s indisponible: %s", source_name, exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    results = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = item.findtext("pubDate")
        if not title or not link or not pub_date_raw:
            continue
        try:
            pub_date = parsedate_to_datetime(pub_date_raw)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if pub_date < cutoff:
            continue
        results.append(
            {
                "title": title,
                "url": link,
                "source": source_name,
                "published_at": pub_date.isoformat(),
            }
        )
    return results


def _fetch_fxstreet_from_cache(lookback_minutes: int) -> list[dict]:
    """
    Lit le cache FXStreet maintenu par la GitHub Action (.github/workflows/
    refresh-fxstreet.yml, toutes les 15 min, fenêtre de 180 min côté Action).
    Ce cache est volontairement plus large que lookback_minutes : on refiltre
    ici à la fenêtre réellement voulue, ce qui laisse une marge si l'Action a
    un peu de retard sans jamais faire remonter un article trop vieux.
    Lève une exception si l'URL n'est pas configurée ou le cache trop vieux :
    l'appelant retombe alors sur l'appel RSS direct (qui échouera depuis
    Render mais sert de filet de sécurité).
    """
    if not config.FXSTREET_CACHE_URL:
        raise RuntimeError("FXSTREET_CACHE_URL non configurée.")

    resp = requests.get(config.FXSTREET_CACHE_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    cache = resp.json()

    fetched_at_raw = cache.get("fetched_at") if isinstance(cache, dict) else None
    articles = cache.get("articles") if isinstance(cache, dict) else None
    if not fetched_at_raw or articles is None:
        raise ValueError("Format de cache FXStreet inattendu.")

    fetched_at = datetime.fromisoformat(fetched_at_raw)
    age_minutes = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60
    if age_minutes > config.FXSTREET_CACHE_MAX_AGE_MINUTES:
        raise RuntimeError(f"Cache FXStreet trop vieux ({age_minutes:.1f} min).")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    return [a for a in articles if datetime.fromisoformat(a["published_at"]) >= cutoff]


def _fetch_fxstreet(lookback_minutes: int) -> list[dict]:
    try:
        return _fetch_fxstreet_from_cache(lookback_minutes)
    except Exception as exc:
        logger.warning("Cache FXStreet indisponible (%s), tentative directe.", exc)
        return _fetch_rss_feed("FXStreet", "https://www.fxstreet.com/rss/news", lookback_minutes)


def fetch_candidates() -> list[dict]:
    """
    Récupère les articles récents de toutes les sources et dédupe par URL.
    Ne fait AUCUN jugement de pertinence ni de filtrage "déjà envoyé" :
    ça reste au niveau de main.py (dédup DB) et ai_analyzer (pertinence).
    """
    lookback = config.BREAKING_NEWS_LOOKBACK_MINUTES
    combined = _fetch_gdelt(lookback) + _fetch_newsapi(config.NEWSAPI_LOOKBACK_MINUTES)
    for source_name, url in RSS_FEEDS:
        if source_name == "FXStreet":
            combined += _fetch_fxstreet(lookback)
        else:
            combined += _fetch_rss_feed(source_name, url, lookback)

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
