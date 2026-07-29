"""
Récupération du calendrier économique.

Source primaire : le flux JSON public de ForexFactory (utilisé par de nombreux
robots/EA MT4-MT5, gratuit, sans clé, mis à jour en continu). Il donne le
titre, la devise, l'heure exacte, l'impact, la prévision et le précédent —
mais jamais le résultat réel une fois publié.

Source de secours : Financial Modeling Prep (FMP), utilisée si ForexFactory
est injoignable (le calendrier lui-même — le "résultat réel" n'est plus
accessible sur son plan gratuit, constaté en production).

"Résultat réel" (actual) après publication, dans l'ordre : Alpha Vantage (USD
uniquement, quelques indicateurs headline reconnus — NFP, CPI, Durable Goods,
Retail Sales, Unemployment Rate), EIA (stocks pétroliers hebdo — Crude/Gasoline/
Distillate/Cushing, données officielles gouvernementales US), FMP (généralement
indisponible, gardé au cas où leur politique changerait), puis les titres RSS
ForexLive/FXStreet (voir news_watcher.py) passés à l'IA : ces flux publient
souvent le chiffre brut en quelques minutes après une publication (constaté,
ex: "Conference Board Consumer Confidence for July 90.8 versus 92.3 estimate")
— ça couvre aussi EUR/GBP et les indicateurs hors Alpha Vantage/EIA.

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

import ai_analyzer
import config
import db
import news_watcher

logger = logging.getLogger("calendar_fetcher")

# Fenêtre de recherche des titres RSS pour le résultat réel : large (le grace
# period avant envoi de l'alerte "après" est court, voir main.py), pour ne rien
# rater même si ForexLive/FXStreet publient leur article un peu tard.
NEWS_ACTUAL_LOOKBACK_MINUTES = 180

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


# --- Alpha Vantage (résultats réels USD : NFP, CPI, Durable Goods...) -----------
#
# ForexFactory ne fournit jamais l'"actual", et FMP a fermé cet accès sur son
# plan gratuit (constaté). Alpha Vantage expose en clair quelques séries macro
# US, gratuitement mais avec un quota serré (25 requêtes/jour) — d'où le cache
# DB (1 appel/jour/indicateur maximum, largement dans le quota pour 5 séries).
#
# Portée volontairement limitée : uniquement USD, et uniquement les titres
# HEADLINE (jamais "Core ...") car Alpha Vantage ne distingue pas la version
# "core" (hors alimentation/énergie ou hors transport) de la version globale —
# mieux vaut ne rien afficher que d'afficher le mauvais chiffre.
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"

# titre ForexFactory (normalisé, minuscules, sans "core") -> (fonction Alpha Vantage, transformation)
_ALPHAVANTAGE_MAPPING = {
    "non-farm employment change": ("NONFARM_PAYROLL", "change"),
    "nonfarm payrolls": ("NONFARM_PAYROLL", "change"),
    "durable goods orders m/m": ("DURABLES", "pct_mm"),
    "cpi m/m": ("CPI", "pct_mm"),
    "cpi y/y": ("CPI", "pct_yy"),
    "retail sales m/m": ("RETAIL_SALES", "pct_mm"),
    "unemployment rate": ("UNEMPLOYMENT", "level"),
}


def _match_alphavantage_series(title: str) -> tuple[str, str] | None:
    normalized = title.strip().lower()
    if "core" in normalized:
        return None  # série "core" non distinguée par Alpha Vantage, on ne devine pas
    return _ALPHAVANTAGE_MAPPING.get(normalized)


_alphavantage_last_call_at: float = 0.0
_ALPHAVANTAGE_MIN_INTERVAL_SECONDS = 1.2  # limite gratuite documentée : 1 req/s


def _fetch_alphavantage_series(function: str) -> list[dict]:
    cached = db.get_alphavantage_cache(function, config.ALPHAVANTAGE_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return cached

    # Si plusieurs indicateurs différents doivent être récupérés le même jour
    # (plusieurs "cache miss" dans le même passage), on respecte la limite
    # "1 requête/seconde" plutôt que de les envoyer en rafale.
    global _alphavantage_last_call_at
    elapsed = time.monotonic() - _alphavantage_last_call_at
    if elapsed < _ALPHAVANTAGE_MIN_INTERVAL_SECONDS:
        time.sleep(_ALPHAVANTAGE_MIN_INTERVAL_SECONDS - elapsed)

    resp = requests.get(
        ALPHAVANTAGE_URL,
        params={"function": function, "apikey": config.ALPHAVANTAGE_API_KEY},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    _alphavantage_last_call_at = time.monotonic()
    resp.raise_for_status()
    payload = resp.json()
    if "data" not in payload:
        raise RuntimeError(payload.get("Information") or payload.get("Error Message") or str(payload)[:200])

    data = payload["data"]
    db.set_alphavantage_cache(function, data)
    return data


def _compute_alphavantage_actual(data: list[dict], transform: str) -> str | None:
    if transform == "level":
        if len(data) < 1:
            return None
        return f"{float(data[0]['value']):g}%"

    if transform == "change":
        if len(data) < 2:
            return None
        delta = float(data[0]["value"]) - float(data[1]["value"])
        return f"{delta:+.0f}K"

    if transform == "pct_mm":
        if len(data) < 2:
            return None
        latest, previous = float(data[0]["value"]), float(data[1]["value"])
        if previous == 0:
            return None
        return f"{(latest - previous) / previous * 100:+.1f}%"

    if transform == "pct_yy":
        if len(data) < 13:
            return None
        latest, year_ago = float(data[0]["value"]), float(data[12]["value"])
        if year_ago == 0:
            return None
        return f"{(latest - year_ago) / year_ago * 100:+.1f}%"

    return None


def _expected_reference_month(event_dt_utc: datetime) -> tuple[int, int]:
    """
    NFP/CPI/Durable Goods/Retail Sales/Unemployment rapportent tous le mois
    CALENDAIRE PRÉCÉDENT leur publication (ex : publié le 27 juillet -> donnée
    de juin). Renvoie (année, mois) du mois attendu.
    """
    year, month = event_dt_utc.year, event_dt_utc.month - 1
    if month == 0:
        year, month = year - 1, 12
    return (year, month)


def _latest_point_covers_expected_month(data: list[dict], event_dt_utc: datetime) -> bool:
    """
    Bug réel constaté : Alpha Vantage peut avoir 1 mois de retard sur la
    publication ForexFactory (ex : Durable Goods encore sur mai alors que la
    publication du jour concerne juin). Sans cette vérification, on calculerait
    une variation sur le MAUVAIS mois et l'afficherait comme si c'était le bon
    résultat — mieux vaut "indisponible" qu'un chiffre confiant mais faux.
    """
    if not data:
        return False
    latest = datetime.fromisoformat(data[0]["date"])
    expected_year, expected_month = _expected_reference_month(event_dt_utc)
    return (latest.year, latest.month) >= (expected_year, expected_month)


def fetch_actual_from_alphavantage(currency: str, title: str, event_dt_utc: datetime) -> str | None:
    if currency != "USD" or not config.ALPHAVANTAGE_API_KEY:
        return None
    match = _match_alphavantage_series(title)
    if not match:
        return None
    function, transform = match
    try:
        data = _fetch_alphavantage_series(function)
        if not _latest_point_covers_expected_month(data, event_dt_utc):
            expected = _expected_reference_month(event_dt_utc)
            logger.warning(
                "Alpha Vantage pas encore à jour pour %s (%s) : dernier point %s, attendu %04d-%02d",
                title, function, data[0]["date"] if data else "aucun", expected[0], expected[1],
            )
            return None
        return _compute_alphavantage_actual(data, transform)
    except Exception as exc:
        logger.warning("Alpha Vantage indisponible pour %s (%s): %s", title, function, exc)
        return None


# --- EIA (stocks pétroliers hebdomadaires) ---------------------------------------

EIA_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"

# Series IDs vérifiés manuellement contre l'API EIA (voir eia.gov/opendata) —
# la recherche par mot-clé est volontairement souple (substring) plutôt qu'une
# correspondance exacte de titre : ForexFactory formule ces events de façons
# légèrement différentes selon les périodes.
_EIA_CUSHING_SERIES = "W_EPC0_SAX_YCUOK_MBBL"
_EIA_CRUDE_SERIES = "WCESTUS1"
_EIA_GASOLINE_SERIES = "WGTSTUS1"
_EIA_DISTILLATE_SERIES = "WDISTUS1"


def _match_eia_series(title: str) -> str | None:
    normalized = title.strip().lower()
    if "cushing" in normalized:
        return _EIA_CUSHING_SERIES
    if "crude" in normalized and "oil" in normalized:
        return _EIA_CRUDE_SERIES
    if "gasoline" in normalized:
        return _EIA_GASOLINE_SERIES
    if "distillate" in normalized:
        return _EIA_DISTILLATE_SERIES
    return None


def _fetch_eia_series(series: str) -> list[dict]:
    cache_key = f"EIA_{series}"
    cached = db.get_alphavantage_cache(cache_key, config.EIA_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return cached

    resp = requests.get(
        EIA_URL,
        params={
            "api_key": config.EIA_API_KEY,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": series,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 3,
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("response", {}).get("data", [])
    if not rows:
        raise RuntimeError("Réponse EIA vide")
    db.set_alphavantage_cache(cache_key, rows)
    return rows


def _expected_eia_period(event_dt_utc: datetime) -> str:
    """
    Les stats hebdo EIA couvrent la semaine se terminant le VENDREDI précédent
    leur publication (généralement un mercredi). Renvoie cette date (YYYY-MM-DD,
    même format que "period" dans la réponse EIA) pour la vérification de
    fraîcheur ci-dessous.
    """
    days_since_friday = (event_dt_utc.weekday() - 4) % 7  # 4 = vendredi
    if days_since_friday == 0:
        days_since_friday = 7
    return (event_dt_utc.date() - timedelta(days=days_since_friday)).isoformat()


def fetch_actual_from_eia(title: str, event_dt_utc: datetime) -> str | None:
    """
    Comme pour Alpha Vantage (voir plus haut) : l'API EIA elle-même peut mettre
    plus d'une journée à refléter une publication toute fraîche — mieux vaut
    "indisponible" qu'un chiffre calculé sur une semaine plus ancienne présenté
    comme si c'était le résultat du jour.
    """
    if not config.EIA_API_KEY:
        return None
    series = _match_eia_series(title)
    if not series:
        return None
    try:
        rows = _fetch_eia_series(series)
        expected_period = _expected_eia_period(event_dt_utc)
        if rows[0]["period"] < expected_period:
            logger.warning(
                "EIA pas encore à jour pour %s (%s) : dernier point %s, attendu %s",
                title, series, rows[0]["period"], expected_period,
            )
            return None
        if len(rows) < 2:
            return None
        delta_thousand_barrels = float(rows[0]["value"]) - float(rows[1]["value"])
        return f"{delta_thousand_barrels / 1000:+.1f}M"
    except Exception as exc:
        logger.warning("EIA indisponible pour %s (%s): %s", title, series, exc)
        return None


def _fetch_actual_from_fmp(currency: str, title: str, event_dt_utc: datetime) -> str | None:
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


def fetch_actual_from_news(
    currency: str,
    title: str,
    event_dt_utc: datetime,
    forecast: str | None = None,
    previous: str | None = None,
) -> str | None:
    """
    Dernier recours : cherche le chiffre réel dans les titres RSS ForexLive/
    FXStreet récents via l'IA (voir ai_analyzer.extract_actual_from_headlines).
    Ne garde que les titres publiés après l'event (ceux d'avant ne peuvent pas
    rapporter son résultat) avant de les soumettre — évite un faux match sur un
    article antérieur qui parlerait du même indicateur (prévision, mois passé...).
    """
    try:
        headlines = news_watcher.fetch_rss_headlines(NEWS_ACTUAL_LOOKBACK_MINUTES)
    except Exception as exc:
        logger.warning("Impossible de récupérer les titres RSS pour le résultat réel: %s", exc)
        return None

    published_after = [
        h for h in headlines
        if h.get("published_at") and datetime.fromisoformat(h["published_at"]) >= event_dt_utc
    ]
    if not published_after:
        return None

    event = {"title": title, "currency": currency, "forecast": forecast, "previous": previous}
    return ai_analyzer.extract_actual_from_headlines(event, published_after)


def fetch_actual_result(
    currency: str,
    title: str,
    event_dt_utc: datetime,
    forecast: str | None = None,
    previous: str | None = None,
) -> str | None:
    """
    Cherche le résultat réel ("actual") d'une news déjà publiée. Essaie dans
    l'ordre : Alpha Vantage (USD, titres headline reconnus — voir plus haut),
    EIA (stocks pétroliers hebdo), FMP (secours, généralement indisponible en
    pratique), puis les titres RSS ForexLive/FXStreet (couvre aussi EUR/GBP et
    les indicateurs hors Alpha Vantage/EIA). Retourne None si aucune source ne
    peut répondre (l'appelant doit gérer ce cas proprement plutôt que d'échouer).
    """
    actual = fetch_actual_from_alphavantage(currency, title, event_dt_utc)
    if actual:
        return actual

    actual = fetch_actual_from_eia(title, event_dt_utc)
    if actual:
        return actual

    actual = _fetch_actual_from_fmp(currency, title, event_dt_utc)
    if actual:
        return actual

    return fetch_actual_from_news(currency, title, event_dt_utc, forecast, previous)
