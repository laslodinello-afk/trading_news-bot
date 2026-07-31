"""
Toute l'intelligence "analyse marché" passe par ici, via l'API Google Gemini
(palier gratuit permanent, contrairement à l'API Anthropic — voir README).

Principe de robustesse : une panne ou une réponse mal formée de l'IA ne doit
JAMAIS empêcher l'envoi de l'alerte factuelle (prévision/précédent/actual).
Chaque fonction publique renvoie None (ou [] pour filter_breaking_news) en cas
d'échec plutôt que de lever une exception qui remonterait jusqu'au scheduler —
c'est à l'appelant (main.py) d'afficher "analyse IA indisponible" le cas échéant.
"""
from __future__ import annotations

import json
import logging

from google import genai
from google.genai import types

import config

logger = logging.getLogger("ai_analyzer")

_client = genai.Client(api_key=config.GEMINI_API_KEY) if config.GEMINI_API_KEY else None

DANGER_LABELS = {
    "ok": "🟢 OK",
    "prudence": "🟠 Prudence",
    "danger": "🔴 Ne pas trader",
}

BIAS_EMOJI = {
    "haussier": "📈 Haussier",
    "baissier": "📉 Baissier",
    "neutre": "➖ Neutre",
}

_SYSTEM_PROMPT = (
    "Tu es un analyste marché spécialisé forex, indices, matières premières et crypto "
    f"({', '.join(config.TRADING_PAIRS)}) pour un daytrader qui utilise les Smart Money "
    "Concepts (SMC). Réponds en français, sois concis, concret, et évite le jargon inutile."
)

# Schéma générique "biais par paire" : un tableau plutôt qu'un objet à clés
# dynamiques, pour rester compatible avec le mode JSON structuré de Gemini.
_BIAS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "required": ["paire", "direction"],
        "properties": {
            "paire": {"type": "STRING"},
            "direction": {"type": "STRING"},
        },
    },
}

_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "required": ["resume", "biais", "raisonnement", "danger"],
    "properties": {
        "resume": {"type": "STRING"},
        "biais": _BIAS_SCHEMA,
        "raisonnement": {"type": "STRING"},
        "danger": {"type": "STRING"},
    },
}

_DAILY_OVERVIEW_SCHEMA = {
    "type": "OBJECT",
    "required": ["apercu"],
    "properties": {"apercu": {"type": "STRING"}},
}

_EVENING_DEBRIEF_SCHEMA = {
    "type": "OBJECT",
    "required": ["recap"],
    "properties": {"recap": {"type": "STRING"}},
}

_BREAKING_NEWS_SCHEMA = {
    "type": "OBJECT",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["index", "importance", "titre_fr", "resume", "biais", "raisonnement", "danger"],
                "properties": {
                    "index": {"type": "INTEGER"},
                    "importance": {"type": "INTEGER"},
                    "titre_fr": {"type": "STRING"},
                    "resume": {"type": "STRING"},
                    "biais": _BIAS_SCHEMA,
                    "raisonnement": {"type": "STRING"},
                    "danger": {"type": "STRING"},
                },
            },
        }
    },
}

IMPORTANCE_STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐"}


def _format_importance(value) -> str:
    try:
        return IMPORTANCE_STARS.get(int(value), "⭐⭐")
    except (TypeError, ValueError):
        return "⭐⭐"


def call_gemini(
    user_prompt: str,
    response_schema: dict,
    max_tokens: int = 500,
    system_prompt: str | None = None,
) -> dict | None:
    """
    Point d'entrée public partagé pour tout appel structuré à Gemini. system_prompt
    permet à un autre module (ex. video_scripts.py) de fournir ses propres règles
    éditoriales sans toucher à _SYSTEM_PROMPT (réservé aux analyses de marché
    ci-dessous). Ne réessaie jamais : voir le principe de robustesse en tête de
    fichier — c'est à l'appelant de décider s'il retente.
    """
    if _client is None:
        logger.warning("GEMINI_API_KEY absente, analyse IA ignorée.")
        return None
    try:
        response = _client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt or _SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_json_schema=response_schema,
            ),
        )
        text = response.text
        if not text:
            logger.error("Réponse Gemini vide (probablement coupée par max_output_tokens).")
            return None
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Réponse IA non-JSON: %s | texte reçu: %r", exc, text[:300] if text else "")
        return None
    except Exception as exc:
        logger.error("Appel Gemini échoué: %s", exc)
        return None


def _format_bias(bias_raw: list) -> dict:
    formatted = {}
    for item in bias_raw or []:
        pair = item.get("paire")
        direction = item.get("direction", "")
        if not pair:
            continue
        formatted[pair] = BIAS_EMOJI.get(str(direction).lower(), f"➖ {direction}")
    return formatted


def _format_danger(danger_raw: str) -> str:
    return DANGER_LABELS.get(str(danger_raw).lower(), "🟠 Prudence")


def _format_analysis(result: dict | None) -> dict | None:
    if not result:
        return None
    return {
        "resume": result.get("resume", ""),
        "biais": _format_bias(result.get("biais", [])),
        "raisonnement": result.get("raisonnement", ""),
        "danger": _format_danger(result.get("danger", "prudence")),
    }


# --- Résumé quotidien (08h00) ---------------------------------------------------

def daily_overview(events: list[dict]) -> str | None:
    if not events:
        return None
    lines = "\n".join(
        f"- {e['time_local']} | {e['currency']} | {e['impact']} | {e['title']}" for e in events
    )
    prompt = f"""Voici les news économiques du jour (heure Bruxelles) pour un daytrader SMC
qui suit ces paires : {", ".join(config.TRADING_PAIRS)}.

{lines}

Donne un aperçu de la journée en maximum 4 lignes courtes en français (champ "apercu")."""
    result = call_gemini(prompt, _DAILY_OVERVIEW_SCHEMA, max_tokens=300)
    if not result:
        return None
    return result.get("apercu")


# --- Reclassification des events "Low" -------------------------------------------

_RECLASSIFY_SCHEMA = {
    "type": "OBJECT",
    "required": ["evaluations"],
    "properties": {
        "evaluations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["index", "importance"],
                "properties": {
                    "index": {"type": "INTEGER"},
                    "importance": {"type": "STRING"},
                    "raison": {"type": "STRING"},
                },
            },
        }
    },
}

_IMPORTANCE_TO_IMPACT = {"moyenne": "Medium", "elevee": "High"}
# Une semaine chargée peut compter 40+ events "Low" (constaté) : marge large pour
# ne pas en tronquer silencieusement une partie hors évaluation.
MAX_RECLASSIFY_BATCH = 60


def reclassify_low_impact(events: list[dict]) -> list[dict]:
    """
    Fait juger par l'IA les events tagués "Low" par ForexFactory : certains
    indicateurs (Ifo, Durable Goods...) sont couramment sous-évalués par ce tag
    pour un day trader, alors que d'autres (M3 Money Supply, ECOFIN...) le
    méritent vraiment. Renvoie uniquement ceux que l'IA upgrade en Medium/High
    (impact modifié + ai_reclassified=True) ; les autres sont ignorés (jamais
    stockés en base, comme avant). Renvoie [] en cas d'échec IA — on ne prend
    pas de risque à l'aveugle sur un event que ForexFactory juge déjà mineur.
    """
    if not events:
        return []
    batch = events[:MAX_RECLASSIFY_BATCH]
    if len(events) > MAX_RECLASSIFY_BATCH:
        logger.warning(
            "%d events Low reçus, seuls les %d premiers sont soumis à l'IA (MAX_RECLASSIFY_BATCH).",
            len(events), MAX_RECLASSIFY_BATCH,
        )
    liste = "\n".join(
        f"{i+1}. {e['currency']} | {e['title']} | prévision={e.get('forecast') or 'N/A'} précédent={e.get('previous') or 'N/A'}"
        for i, e in enumerate(batch)
    )
    prompt = f"""ForexFactory classe ces événements économiques comme impact FAIBLE (Low). Pour un
daytrader forex/indices/crypto/matières premières ({", ".join(config.TRADING_PAIRS)}),
évalue si ce classement est juste ou sous-estimé, en te basant sur le potentiel réel de
surprise et de mouvement de prix à la publication (pas juste l'importance économique
générale au sens large) :

{liste}

Pour CHAQUE événement, renvoie "index" (son numéro ci-dessus) et "importance" :
"faible" (le tag Low est juste, on l'ignore), "moyenne" ou "elevee" (sous-évalué, à
surveiller). N'upgrade que si tu es réellement confiant, pas par excès de prudence —
la plupart des events Low doivent rester "faible"."""

    result = call_gemini(prompt, _RECLASSIFY_SCHEMA, max_tokens=3000)
    if not result:
        return []

    upgraded = []
    for item in result.get("evaluations", []):
        idx = item.get("index")
        if not idx or not (1 <= idx <= len(batch)):
            continue
        new_impact = _IMPORTANCE_TO_IMPACT.get(str(item.get("importance", "")).lower())
        if not new_impact:
            continue
        event = dict(batch[idx - 1])
        event["impact"] = new_impact
        event["ai_reclassified"] = True
        upgraded.append(event)
    return upgraded


# --- Débrief du soir (23h, clôture NY) -------------------------------------------

def evening_debrief(events: list[dict], news_items: list[dict]) -> str | None:
    if not events and not news_items:
        return None

    sections = []
    if events:
        lines = "\n".join(
            f"- {e['time_local']} | {e['currency']} | {e['title']} | "
            f"réel={e.get('actual') or 'N/A'} prévision={e.get('forecast') or 'N/A'} précédent={e.get('previous') or 'N/A'}"
            for e in events
        )
        sections.append(f"News économiques publiées aujourd'hui :\n{lines}")
    if news_items:
        lines = "\n".join(f"- {n['title']} — {n.get('resume', '')}" for n in news_items)
        sections.append(f"Breaking news du jour :\n{lines}")

    prompt = f"""Voici le récapitulatif brut de la journée de trading qui se termine (clôture de la
session de New York) pour un daytrader SMC qui suit : {", ".join(config.TRADING_PAIRS)}.

{chr(10).join(sections)}

Fais un débrief de fin de journée en français, maximum 5-6 lignes courtes : comment la
journée s'est globalement déroulée pour ces paires, quels ont été les principaux moteurs,
et un point de vigilance pour la suite. Champ "recap"."""
    result = call_gemini(prompt, _EVENING_DEBRIEF_SCHEMA, max_tokens=600)
    if not result:
        return None
    return result.get("recap")


# --- Alerte "30 min avant" -------------------------------------------------------

def analyze_before(event: dict, concerned_pairs: list[str]) -> dict | None:
    prompt = f"""News économique à venir dans {config.ALERT_BEFORE_MINUTES} minutes :
- Titre : {event['title']}
- Devise : {event['currency']}
- Impact : {event['impact']}
- Prévision : {event.get('forecast') or 'N/A'}
- Précédent : {event.get('previous') or 'N/A'}
- Paires concernées : {", ".join(concerned_pairs)}

Réponds avec :
- "resume" : 1 phrase courte expliquant l'enjeu de cette news pour un trader
- "biais" : pour CHAQUE paire concernée, un objet {{"paire": "...", "direction": "haussier" | "baissier" | "neutre"}}
- "raisonnement" : 1 phrase expliquant le biais le plus probable et pourquoi
- "danger" : "ok" | "prudence" | "danger" """
    result = call_gemini(prompt, _ANALYSIS_SCHEMA, max_tokens=400)
    return _format_analysis(result)


# --- Alerte "juste après publication" --------------------------------------------

def analyze_after(event: dict, concerned_pairs: list[str], actual: str | None) -> dict | None:
    prompt = f"""News économique qui vient d'être publiée :
- Titre : {event['title']}
- Devise : {event['currency']}
- Impact : {event['impact']}
- Réel : {actual or "pas encore disponible"}
- Prévision : {event.get('forecast') or 'N/A'}
- Précédent : {event.get('previous') or 'N/A'}
- Paires concernées : {", ".join(concerned_pairs)}

Analyse la surprise (réel vs prévision) et son effet probable. Réponds avec :
- "resume" : résumé de la surprise et de son sens (meilleur/pire que prévu, etc.), maximum 4 lignes courtes en français
- "biais" : pour CHAQUE paire concernée, un objet {{"paire": "...", "direction": "haussier" | "baissier" | "neutre"}}
- "raisonnement" : 1 phrase expliquant le biais
- "danger" : "ok" | "prudence" | "danger" """
    result = call_gemini(prompt, _ANALYSIS_SCHEMA, max_tokens=400)
    return _format_analysis(result)


# --- Extraction du résultat réel depuis des titres RSS ---------------------------

_ACTUAL_FROM_NEWS_SCHEMA = {
    "type": "OBJECT",
    "required": ["actual"],
    "properties": {
        "actual": {"type": "STRING"},
    },
}

_ACTUAL_NOT_FOUND = "INTROUVABLE"


def extract_actual_from_headlines(event: dict, headlines: list[dict]) -> str | None:
    """
    Dernier recours pour le "résultat réel" (voir calendar_fetcher.fetch_actual_result) :
    ForexLive/FXStreet publient souvent le chiffre brut d'une publication économique
    en quelques minutes (constaté, ex: "Conference Board Consumer Confidence for July
    90.8 versus 92.3 estimate"), mais sous un intitulé parfois différent de celui de
    ForexFactory (ex: "Conference Board Consumer Confidence" = "CB Consumer
    Confidence") — d'où l'IA plutôt qu'une comparaison de texte figée. Le sentinel
    _ACTUAL_NOT_FOUND (plutôt qu'un champ nullable, mal supporté par le schéma JSON
    structuré de Gemini) force l'IA à expliciter l'absence de match plutôt que de
    deviner.
    """
    if not headlines:
        return None
    titles_block = "\n".join(f"- {h['title']}" for h in headlines)
    prompt = f"""Événement économique déjà publié :
- Titre : {event['title']}
- Devise : {event['currency']}
- Prévision : {event.get('forecast') or 'N/A'}
- Précédent : {event.get('previous') or 'N/A'}

Titres d'articles récents (flux RSS forex) :
{titles_block}

Un de ces titres rapporte-t-il le résultat réel (chiffre publié) de CET événement
précis ? Les flux utilisent parfois un intitulé différent pour le même indicateur
(ex: "Conference Board Consumer Confidence" = "CB Consumer Confidence") : base-toi
sur le sens, pas juste la ressemblance du texte. Si un titre correspond avec
certitude, renvoie "actual" = le chiffre exact tel qu'écrit dans ce titre (avec son
unité/signe). Si aucun titre ne correspond avec certitude à CET événement précis,
renvoie "actual" = "{_ACTUAL_NOT_FOUND}" — ne devine jamais et ne confonds pas avec
un autre indicateur, même du même pays."""

    result = call_gemini(prompt, _ACTUAL_FROM_NEWS_SCHEMA, max_tokens=200)
    if not result:
        return None
    actual = (result.get("actual") or "").strip()
    if not actual or actual.upper() == _ACTUAL_NOT_FOUND:
        return None
    return actual


# --- Filtrage des breaking news --------------------------------------------------

MAX_CANDIDATES_PER_BATCH = 25


def filter_breaking_news(candidates: list[dict], known_calendar_titles: list[str] | None = None) -> list[dict]:
    """
    Prend une liste brute d'articles (title/url/source) et ne renvoie que ceux
    jugés réellement pertinents pour le trading, enrichis de l'analyse IA.
    Renvoie [] (jamais None) en cas d'échec IA : on préfère rater une news
    "choc" plutôt que spammer sur un batch entier non filtré.

    known_calendar_titles : titres des events du calendrier économique du jour
    (voir main.py) — sert à exclure les articles qui ne font que rapporter le
    résultat chiffré d'un event déjà couvert par les alertes avant/après,
    pour éviter d'envoyer la même information deux fois sous deux formats
    différents (constaté : un article "chiffre brut" pour Crude Oil
    Inventories peut aussi remonter comme breaking news).
    """
    if not candidates:
        return []
    batch = candidates[:MAX_CANDIDATES_PER_BATCH]
    articles_block = "\n".join(f"{i+1}. [{a['source']}] {a['title']}" for i, a in enumerate(batch))
    pairs = ", ".join(config.TRADING_PAIRS)

    exclusion_block = ""
    if known_calendar_titles:
        titles_list = "\n".join(f"- {t}" for t in known_calendar_titles)
        exclusion_block = f"""

IMPORTANT — À EXCLURE SYSTÉMATIQUEMENT : les événements suivants sont déjà
couverts aujourd'hui par des alertes calendrier dédiées (avant/après
publication). Si un article ne fait QUE rapporter le résultat chiffré de l'un
de ces événements précis (même avec un nom légèrement différent), ignore-le
complètement — même s'il semble pertinent — pour ne pas envoyer deux fois la
même information sous deux formats différents. Ne l'exclus que s'il s'agit
vraiment de CE résultat précis, pas d'un article qui en parle dans un contexte
plus large (ex: un article qui analyse plusieurs événements de la journée
reste pertinent).
Événements déjà couverts aujourd'hui :
{titles_list}"""

    prompt = f"""Voici des titres d'articles de presse récents. Ne garde QUE les vrais chocs susceptibles
d'avoir un impact réel sur les marchés suivis ({pairs}) — sois sélectif, pas exhaustif :

- Déclarations ou tweets de responsables politiques/banques centrales US-UE-UK sur l'économie, les taux ou les droits de douane
- Conflits armés, attentats, événements géopolitiques majeurs (y compris zones de production pétrolière)
- Démission/limogeage de dirigeant économique clé, défaut de paiement souverain
- Crypto : décision SEC/CFTC sur un ETF ou une régulation, hack ou faillite d'exchange, dépeg d'un stablecoin majeur
- Pétrole : décision OPEP/OPEP+, coupure de production, tensions dans une zone de production/transit majeure

Ignore : l'économie "ordinaire" non planifiée (licenciements, commentaires hawkish/dovish isolés,
mouvements de bourse sans être un krach, craintes de récession générales, tensions commerciales
mineures...) — seuls les VRAIS chocs listés ci-dessus comptent maintenant. Ignore aussi : sport,
people, faits divers purement locaux, analyses techniques, opinions, articles qui mentionnent
juste un mot-clé en passant sans lien réel avec ces sujets.{exclusion_block}

Articles :
{articles_block}

Renvoie un tableau "items" (vide si rien de pertinent). Pour chaque article retenu :
- "index" : son numéro dans la liste ci-dessus
- "importance" : note de 1 à 3 sur la portée de la news pour un daytrader —
  1 = mineur, juste bon à savoir, sans effet notable attendu ; 2 = modérément
  important, peut créer un peu de mouvement ; 3 = fort impact attendu, à
  surveiller de près. Ne mets pas systématiquement 3, sois discriminant.
- "titre_fr" : le titre de l'article (souvent en anglais, ces flux sont anglophones)
  traduit en français, fidèle et naturel — pas une paraphrase ni un résumé,
  une vraie traduction du titre
- "resume" : 1 phrase en français qui résume la news elle-même
- "biais" : un objet {{"paire": "...", "direction": "haussier"|"baissier"|"neutre"}} par paire concernée
- "raisonnement" : 1 phrase CONCRÈTE sur ce qui va probablement se passer sur les marchés
  dans les prochaines heures à cause de cette news (pas juste "pourquoi", mais "et donc quoi
  ensuite" — ex : "Le dollar devrait s'affaiblir à court terme, les indices US pourraient
  monter sur fond d'anticipation de baisse des taux.")
- "danger" : "ok"|"prudence"|"danger" — "ok" si le choc est confirmé mais sans urgence de trading
  immédiate, ne force pas "prudence" par défaut."""

    result = call_gemini(prompt, _BREAKING_NEWS_SCHEMA, max_tokens=1500)
    items = (result or {}).get("items", [])

    enriched = []
    for item in items:
        idx = item.get("index")
        if not idx or not (1 <= idx <= len(batch)):
            continue
        article = dict(batch[idx - 1])
        article["importance"] = _format_importance(item.get("importance"))
        article["titre_fr"] = item.get("titre_fr") or article["title"]
        article["resume"] = item.get("resume", "")
        article["biais"] = _format_bias(item.get("biais", []))
        article["raisonnement"] = item.get("raisonnement", "")
        article["danger"] = _format_danger(item.get("danger", "prudence"))
        enriched.append(article)
    return enriched
