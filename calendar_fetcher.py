"""
Récupération du calendrier économique.

Source primaire : le flux JSON public de ForexFactory (utilisé par de nombreux
robots/EA MT4-MT5, gratuit, sans clé, mis à jour en continu). Il donne le
titre, la devise, l'heure exacte, l'impact, la prévision et le précédent —
mais jamais le résultat réel une fois publié.

Source de secours : Financial Modeling Prep (FMP), utilisée si ForexFactory
est injoignable, ET utilisée systématiquement pour aller chercher le
"résultat réel" (actual) d'une news après sa publication.

Toute erreur réseau/format est absorbée ici : une source qui tombe ne doit
jamais faire planter l'agent, seulement dégrader (voir main.py pour l'alerte
Telegram envoyée dans ce cas).
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

import config

logger = logging.getLogger("calendar_fetcher")

FF_BASE = "https://nfs.faireconomy.media"
# Seul "thisweek" existe réellement sur ce miroir public (thisweek/lastweek/nextweek
# renvoient 404) : le flux ne couvre donc que la semaine calendaire en cours, et se
# décale généralement le week-end. CALENDAR_REFRESH_HOURS rattrape ça automatiquement.
FF_FEED_URL = f"{FF_BASE}/ff_calendar_thisweek.json"

FMP_CALENDAR_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"

HEADERS = {"User-Agent": "Mozilla/5.0 (trading-news-agent; +https://github.com)"}
TIMEOUT = 15

# FMP renvoie parfois un code pays plutôt qu'une devise : on normalise les deux.
COUNTRY_TO_CURRENCY = {
    "US": "USD", "USA": "USD", "UNITED STATES": "USD",
    "EU": "EUR", "EMU": "EUR", "EUROZONE": "EUR", "EURO AREA": "EUR",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR",
    "GB": "GBP", "UK": "GBP", "UNITED KINGDOM": "GBP",
}


def _normalize_currency(raw: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip().upper()
    if raw in {"USD", "EUR", "GBP"}:
        return raw
    return COUNTRY_TO_CURRENCY.get(raw)


def _make_event_key(currency: str, title: str, event_dt_utc: datetime) -> str:
    slug = hashlib.md5(f"{currency}|{title.strip().lower()}".encode()).hexdigest()[:10]
    return f"{event_dt_utc:%Y%m%d%H%M}_{currency}_{slug}"


def _normalize_impact(raw: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in {"high", "red"}:
        return "High"
    if raw in {"medium", "orange", "moderate"}:
        return "Medium"
    if raw in {"low", "yellow"}:
        return "Low"
    if raw == "holiday":
        return "Holiday"
    return None


# --- ForexFactory (source primaire) --------------------------------------------

_RETRY_DELAYS_SECONDS = (3, 8)  # petits backoffs pour absorber un 429/5xx transitoire


def _fetch_forexfactory_raw() -> list[dict]:
    last_exc = None
    for attempt, delay in enumerate((0, *_RETRY_DELAYS_SECONDS)):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(FF_FEED_URL, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Format ForexFactory inattendu: {type(data)}")
            return data
        except Exception as exc:
            last_exc = exc
            logger.warning("ForexFactory tentative %d échouée: %s", attempt + 1, exc)
    raise last_exc


RELEVANT_IMPACTS = {"High", "Medium", "Low"}  # tout sauf "Holiday" (pas une vraie donnée éco)


def _normalize_forexfactory(raw_events: list[dict]) -> list[dict]:
    """
    Garde High/Medium/Low pour les devises suivies (pas seulement High/Medium) :
    les "Low" sont ensuite soumis à l'IA par main.py (voir ai_analyzer.
    reclassify_low_impact) qui peut les faire remonter si elle les juge plus
    importants que le tag ForexFactory ne le suggère. Ceux qui restent "Low"
    après cette relecture sont filtrés plus tard, jamais stockés en base.
    """
    normalized = []
    for raw in raw_events:
        currency = _normalize_currency(raw.get("country", ""))
        impact = _normalize_impact(raw.get("impact", ""))
        if currency not in config.WATCHED_CURRENCIES or impact not in RELEVANT_IMPACTS:
            continue
        try:
            event_dt = datetime.fromisoformat(raw["date"])
        except (KeyError, ValueError):
            logger.warning("Date ForexFactory illisible, event ignoré: %r", raw.get("date"))
            continue
        event_dt_utc = event_dt.astimezone(timezone.utc)
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "event_key": _make_event_key(currency, title, event_dt_utc),
                "title": title,
                "currency": currency,
                "impact": impact,
                "event_dt_utc": event_dt_utc.isoformat(),
                "forecast": raw.get("forecast") or None,
                "previous": raw.get("previous") or None,
                "actual": None,
                "ai_reclassified": False,
            }
        )
    return normalized


# --- Financial Modeling Prep (secours + source du "résultat réel") -------------

def _fetch_fmp_raw(date_from: str, date_to: str) -> list[dict]:
    if not config.FMP_API_KEY:
        raise RuntimeError("FMP_API_KEY absente : impossible d'utiliser le secours FMP.")
    resp = requests.get(
        FMP_CALENDAR_URL,
        params={"from": date_from, "to": date_to, "apikey": config.FMP_API_KEY},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("Error Message"):
        raise RuntimeError(f"FMP a renvoyé une erreur: {data['Error Message']}")
    if not isinstance(data, list):
        raise ValueError(f"Format FMP inattendu: {type(data)}")
    return data


def _normalize_fmp(raw_events: list[dict]) -> list[dict]:
    normalized = []
    for raw in raw_events:
        currency = _normalize_currency(raw.get("currency") or raw.get("country") or "")
        impact = _normalize_impact(raw.get("impact", ""))
        if currency not in config.WATCHED_CURRENCIES or impact not in RELEVANT_IMPACTS:
            continue
        raw_date = raw.get("date")
        if not raw_date:
            continue
        try:
            # FMP renvoie généralement "YYYY-MM-DD HH:MM:SS" en UTC.
            event_dt = datetime.fromisoformat(raw_date.replace(" ", "T"))
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Date FMP illisible, event ignoré: %r", raw_date)
            continue
        event_dt_utc = event_dt.astimezone(timezone.utc)
        title = (raw.get("event") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "event_key": _make_event_key(currency, title, event_dt_utc),
                "title": title,
                "currency": currency,
                "impact": impact,
                "event_dt_utc": event_dt_utc.isoformat(),
                "forecast": _stringify(raw.get("estimate")),
                "previous": _stringify(raw.get("previous")),
                "actual": _stringify(raw.get("actual")),
                "ai_reclassified": False,
                "_fmp_raw_date": event_dt_utc,
            }
        )
    return normalized


def _stringify(value) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


# --- Cache GitHub Action (contourne le blocage IP de Render sur ForexFactory) ---

def _fetch_github_cache_raw() -> list[dict]:
    if not config.GITHUB_CALENDAR_CACHE_URL:
        raise RuntimeError("GITHUB_CALENDAR_CACHE_URL non configurée.")
    resp = requests.get(config.GITHUB_CALENDAR_CACHE_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    cache = resp.json()
    if not isinstance(cache, dict) or "events" not in cache or "fetched_at" not in cache:
        raise ValueError("Format de cache GitHub inattendu (events/fetched_at manquant).")

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    if age_hours > config.GITHUB_CACHE_MAX_AGE_HOURS:
        raise RuntimeError(
            f"Cache GitHub trop vieux ({age_hours:.1f}h, la GitHub Action semble en panne)."
        )

    events = cache["events"]
    if not isinstance(events, list):
        raise ValueError(f"Format 'events' inattendu dans le cache GitHub: {type(events)}")
    logger.info("Cache GitHub à jour (rafraîchi il y a %.1fh)", age_hours)
    return events


# --- API publique ---------------------------------------------------------------

def refresh_calendar() -> tuple[list[dict], str]:
    """
    Récupère et normalise le calendrier économique de la semaine en cours.
    Ordre des sources : cache GitHub Action (contourne le blocage IP de Render sur
    ForexFactory) -> ForexFactory en direct (utile si l'agent tourne ailleurs que
    sur Render, ou si le blocage se lève) -> FMP (secours historique, souvent
    indisponible sur les plans gratuits actuels).
    Retourne (events, source_utilisée). Lève une exception seulement si
    TOUTES les sources ont échoué (à charge pour l'appelant d'alerter Telegram).
    """
    try:
        raw = _fetch_github_cache_raw()
        events = _normalize_forexfactory(raw)
        logger.info("Calendrier récupéré via cache GitHub (%d events filtrés)", len(events))
        return events, "github_cache"
    except Exception as exc:
        logger.warning("Cache GitHub indisponible (%s), bascule sur ForexFactory direct", exc)

    try:
        raw = _fetch_forexfactory_raw()
        events = _normalize_forexfactory(raw)
        logger.info("Calendrier récupéré via ForexFactory (%d events filtrés)", len(events))
        return events, "forexfactory"
    except Exception as exc:
        logger.warning("ForexFactory indisponible (%s), bascule sur FMP", exc)

    try:
        today = datetime.now(timezone.utc).date()
        date_from = today.isoformat()
        date_to = (today + timedelta(days=13)).isoformat()
        raw = _fetch_fmp_raw(date_from, date_to)
        events = _normalize_fmp(raw)
        logger.info("Calendrier récupéré via FMP (%d events filtrés)", len(events))
        return events, "fmp"
    except Exception as exc:
        logger.error("FMP également indisponible: %s", exc)
        raise RuntimeError(
            f"Impossible de récupérer le calendrier économique (cache GitHub, ForexFactory ET FMP en échec: {exc})"
        ) from exc


def fetch_actual_result(currency: str, title: str, event_dt_utc: datetime) -> str | None:
    """
    Cherche le résultat réel ("actual") d'une news déjà publiée via FMP.
    Retourne None si FMP n'est pas configuré, indisponible, ou si aucune
    correspondance fiable n'est trouvée (l'appelant doit gérer ce cas
    proprement plutôt que d'échouer).
    """
    if not config.FMP_API_KEY:
        return None
    try:
        day = event_dt_utc.date()
        raw = _fetch_fmp_raw((day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat())
        candidates = _normalize_fmp(raw)
    except Exception as exc:
        logger.warning("Impossible d'interroger FMP pour le résultat réel: %s", exc)
        return None

    same_currency = [c for c in candidates if c["currency"] == currency and c.get("actual")]
    if not same_currency:
        return None

    def score(candidate: dict) -> tuple[float, float]:
        time_diff_minutes = abs((candidate["_fmp_raw_date"] - event_dt_utc).total_seconds()) / 60
        title_similarity = difflib.SequenceMatcher(None, candidate["title"].lower(), title.lower()).ratio()
        return (time_diff_minutes, -title_similarity)

    best = min(same_currency, key=score)
    time_diff_minutes = abs((best["_fmp_raw_date"] - event_dt_utc).total_seconds()) / 60
    if time_diff_minutes > 90:
        return None  # pas de correspondance temporelle fiable
    return best["actual"]
