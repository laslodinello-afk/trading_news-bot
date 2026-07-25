"""
Configuration centrale de l'agent. Toutes les valeurs sensibles viennent du .env,
tout ce qui est "réglage de trading" est modifiable ici sans toucher au reste du code.
"""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# --- Secrets (.env) ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Optionnelles (fallback calendrier + résultats réels + breaking news)
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# Canal Telegram séparé (optionnel) : les alertes "contenu" (résumé, avant/après
# news, breaking news) y sont aussi envoyées en plus du chat perso. Les messages
# opérationnels (démarrage, erreurs) restent perso uniquement. Le paywall lui-même
# (abonnement Stars) se configure entièrement côté app Telegram, pas ici.
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

# URL brute du cache calendrier maintenu par la GitHub Action (.github/workflows/
# refresh-calendar.yml), qui contourne le blocage de l'IP Render sur ForexFactory.
# Vide par défaut (source ignorée) ; à définir une fois le dépôt GitHub en place, ex :
# https://raw.githubusercontent.com/<user>/<repo>/main/calendar_cache.json
GITHUB_CALENDAR_CACHE_URL = os.getenv("GITHUB_CALENDAR_CACHE_URL", "")
GITHUB_CACHE_MAX_AGE_HOURS = 12  # au-delà, le cache est jugé trop vieux (Action en panne ?)

# --- Modèle IA ---------------------------------------------------------------
# Gemini Flash a un palier gratuit permanent (pas un essai limité dans le temps),
# largement suffisant pour cet agent. gemini-3.1-flash-lite est choisi précisément
# parce qu'il n'a PAS de "raisonnement interne" (thinking) : les modèles
# "flash-latest"/"3.5-flash" consomment des tokens invisibles pour réfléchir avant
# de répondre, ce qui peut tronquer le JSON avant qu'il soit complet. Si ce modèle
# venait à changer de nom côté Google, ajuste juste GEMINI_MODEL dans .env
# (voir README, section dépannage) — en testant qu'il retourne bien un JSON complet.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# --- Fuseau horaire & sessions -----------------------------------------------
TIMEZONE = ZoneInfo("Europe/Brussels")

LONDON_KILLZONE = ("09:00", "12:00")
NEWYORK_KILLZONE = ("14:30", "17:00")
# Les alertes tournent H24 7/7, ces plages ne servent qu'à annoter les messages.

# --- Paires suivies (configurable) -------------------------------------------
TRADING_PAIRS = [
    p.strip().upper()
    for p in os.getenv(
        "TRADING_PAIRS",
        "XAUUSD,EURUSD,GBPUSD,US30,BTCUSD,ETHUSD,DAX,SP500,NASDAQ,BRENT,CAC40",
    ).split(",")
    if p.strip()
]

# Devises qui impactent chaque paire (utilisé pour cibler le biais IA). Le crypto,
# les indices US et le pétrole n'ont pas de "devise" propre au sens calendrier
# économique : ils sont mappés sur la devise dont les news macro les font le plus
# bouger (le dollar, dans la quasi-totalité des cas). Si tu ajoutes une paire dans
# TRADING_PAIRS, ajoute aussi son mapping ici, sinon elle ne recevra jamais de biais IA.
PAIR_CURRENCIES = {
    "XAUUSD": {"USD"},
    "EURUSD": {"USD", "EUR"},
    "GBPUSD": {"USD", "GBP"},
    "US30": {"USD"},
    "BTCUSD": {"USD"},
    "ETHUSD": {"USD"},
    "DAX": {"EUR"},
    "SP500": {"USD"},
    "NASDAQ": {"USD"},
    "BRENT": {"USD"},  # + les news "Crude Oil Inventories" / OPEP (voir breaking news)
    "CAC40": {"EUR"},
}


def pairs_for_currency(currency: str) -> list[str]:
    """Paires suivies concernées par une devise donnée."""
    return [
        pair
        for pair in TRADING_PAIRS
        if currency in PAIR_CURRENCIES.get(pair, set())
    ]


# --- Filtre calendrier économique --------------------------------------------
WATCHED_CURRENCIES = {"USD", "EUR", "GBP"}
WATCHED_IMPACTS = {"High", "Medium"}  # rouge + orange uniquement

# --- Alertes -------------------------------------------------------------
DAILY_SUMMARY_HOUR = 8
DAILY_SUMMARY_MINUTE = 0

ALERT_BEFORE_MINUTES = 30  # rappel avant news (impact High uniquement)
NO_TRADE_WINDOW_MINUTES = 15  # conseil "éviter le spread / ne pas trader X min après"
AFTER_ALERT_MAX_AGE_MINUTES = 60  # au-delà, une news publiée est considérée trop vieille pour être alertée (évite un backlog au premier démarrage)

CALENDAR_CHECK_INTERVAL_MINUTES = 5  # fréquence de vérification avant/après
CALENDAR_REFRESH_HOURS = 6  # fréquence de re-téléchargement du calendrier complet

BREAKING_NEWS_INTERVAL_MINUTES = 15  # fréquence de veille GDELT/NewsAPI
BREAKING_NEWS_LOOKBACK_MINUTES = 20  # fenêtre de recherche à chaque passage

# Mots-clés de veille "breaking news" (tweet choc, conflit, discours banque centrale,
# régulation crypto, choc pétrolier...)
BREAKING_NEWS_KEYWORDS = [
    kw.strip()
    for kw in os.getenv(
        "BREAKING_NEWS_KEYWORDS",
        "Trump,White House,Federal Reserve,Fed chair,ECB,Lagarde,Bank of England,"
        "tariff,sanctions,war,ceasefire,missile,invasion,explosion,attack,shooting,"
        "rate hike,rate cut,emergency meeting,resignation,default,bankruptcy,"
        # Crypto
        "Bitcoin ETF,SEC crypto,SEC Chair,CFTC crypto,Binance,Coinbase,crypto regulation,"
        "stablecoin,exchange hack,crypto exchange collapse,Bitcoin halving,"
        # Pétrole / matières premières
        "OPEC,OPEC+,oil production cut,Saudi Arabia oil,Strait of Hormuz",
    ).split(",")
    if kw.strip()
]

# --- Divers -------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "alerts.db"))
LOG_PATH = os.getenv("LOG_PATH", os.path.join(os.path.dirname(__file__), "agent.log"))

# Port HTTP du petit serveur "keep-alive" (Render/Railway l'imposent via $PORT)
PORT = int(os.getenv("PORT", "8080"))

# Ne renvoyer une alerte d'erreur Telegram identique qu'une fois par heure max
ERROR_ALERT_THROTTLE_MINUTES = 60


def validate() -> list[str]:
    """Retourne la liste des variables obligatoires manquantes."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    return missing
