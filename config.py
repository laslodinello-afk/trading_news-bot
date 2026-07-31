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

# Résultats réels USD (NFP, CPI, Durable Goods...) : gratuite sur
# https://www.alphavantage.co/support/#api-key (25 requêtes/jour, mise en cache
# côté agent pour ne jamais approcher cette limite — voir calendar_fetcher.py).
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")
ALPHAVANTAGE_CACHE_MAX_AGE_HOURS = 20

# Résultats réels pétrole (Crude/Gasoline/Distillate/Cushing stocks) : gratuite
# sur https://www.eia.gov/opendata/register.php (agence gouvernementale US,
# données hebdomadaires officielles — voir calendar_fetcher.py).
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
EIA_CACHE_MAX_AGE_HOURS = 20

# Optionnelle (fond vidéo pour --render) : gratuite sur https://www.pexels.com/api/
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Canal Telegram séparé (optionnel) : les alertes "contenu" (résumé, avant/après
# news, breaking news) y sont aussi envoyées en plus du chat perso. Les messages
# opérationnels (démarrage, erreurs) restent perso uniquement. Le paywall lui-même
# (abonnement Stars) se configure entièrement côté app Telegram, pas ici.
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

# URL brute du cache calendrier maintenu par la GitHub Action (.github/workflows/
# refresh-calendar.yml), qui contourne le blocage de l'IP Render sur ForexFactory.
# Vide par défaut (source ignorée) ; à définir une fois le dépôt GitHub en place, ex :
# https://raw.githubusercontent.com/<user>/<repo>/cache-data/calendar_cache.json
GITHUB_CALENDAR_CACHE_URL = os.getenv("GITHUB_CALENDAR_CACHE_URL", "")
GITHUB_CACHE_MAX_AGE_HOURS = 12  # au-delà, le cache est jugé trop vieux (Action en panne ?)

# Même principe pour FXStreet (.github/workflows/refresh-fxstreet.yml) : Render
# reçoit un 403 direct (IP partagée bloquée), la GitHub Action non. Vide par
# défaut (repli sur l'appel direct, qui échouera depuis Render) ; à définir ex :
# https://raw.githubusercontent.com/<user>/<repo>/cache-data/fxstreet_cache.json
FXSTREET_CACHE_URL = os.getenv("FXSTREET_CACHE_URL", "")
FXSTREET_CACHE_MAX_AGE_MINUTES = 15  # au-delà, le cache est jugé trop vieux (Action en panne ?) — l'Action tourne toutes les 5 min, donc 15 min = 2-3 passages ratés d'affilée

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

# Débrief du soir : récap de la journée (news publiées + breaking news), calé
# sur la clôture de la session de New York.
EVENING_DEBRIEF_HOUR = 23
EVENING_DEBRIEF_MINUTE = 0

ALERT_BEFORE_MINUTES = 30  # rappel avant news (impact High + Medium)
NO_TRADE_WINDOW_MINUTES = 15  # conseil "éviter le spread / ne pas trader X min après"
AFTER_ALERT_MAX_AGE_MINUTES = 60  # au-delà, une news publiée est considérée trop vieille pour être alertée (évite un backlog au premier démarrage)

# Délai de grâce avant d'envoyer l'alerte "après" même sans résultat réel trouvé
# (sinon "indisponible") : les sources gratuites (Alpha Vantage, et surtout les
# titres RSS ForexLive/FXStreet, voir calendar_fetcher.fetch_actual_from_news)
# peuvent mettre jusqu'à ~20-30 min à publier le chiffre après l'event — mieux
# vaut attendre un peu que spammer "indisponible" trop vite. Doit rester < que
# AFTER_ALERT_MAX_AGE_MINUTES ci-dessus.
ACTUAL_RESULT_GRACE_MINUTES = 45

CALENDAR_CHECK_INTERVAL_MINUTES = 5  # fréquence de vérification avant/après
CALENDAR_REFRESH_HOURS = 6  # fréquence de re-téléchargement du calendrier complet

BREAKING_NEWS_INTERVAL_MINUTES = 5  # fréquence de veille GDELT/RSS (le plus court possible sans gaspiller de quota, voir NewsAPI ci-dessous)
BREAKING_NEWS_LOOKBACK_MINUTES = 20  # fenêtre de recherche à chaque passage (marge de sécurité ~4x l'intervalle si un tick est manqué)

# NewsAPI a ~24h de délai sur son plan gratuit (constaté, voir news_watcher.py) :
# l'interroger aussi souvent que GDELT/RSS ne le rendrait pas plus frais, juste
# plus gourmand en quota (100 requêtes/jour). On le vérifie donc séparément,
# une fois par heure (24 appels/jour, grosse marge), avec sa propre fenêtre de
# recherche assortie (60 + marge de sécurité si un passage est manqué).
NEWSAPI_POLL_INTERVAL_MINUTES = 60
NEWSAPI_LOOKBACK_MINUTES = 90

# En dessous de ce seuil d'étoiles (1-3, voir ai_analyzer.filter_breaking_news),
# une news jugée pertinente n'est PAS envoyée en alerte individuelle — trop
# mineure pour spammer en temps réel. Elle reste quand même enregistrée pour
# apparaître dans le débrief du soir (voir main.py job_check_breaking_news).
BREAKING_NEWS_MIN_STARS_FOR_ALERT = 3

# Mots-clés de veille "breaking news" (tweet choc, conflit, discours banque centrale,
# régulation crypto, choc pétrolier... + actualité économique plus "ordinaire" mais
# non planifiée, voir catégorie "Économie générale" ci-dessous)
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
        "OPEC,OPEC+,oil production cut,Saudi Arabia oil,Strait of Hormuz,"
        # Économie générale (non planifiée, impact plus modéré mais réel)
        "trade deal,trade war,export ban,layoffs,hiring freeze,job cuts,"
        "recession,economic slowdown,GDP forecast,stock market rally,stock selloff,"
        "market crash,market volatility,supply chain,chip shortage,energy crisis,"
        "government shutdown,debt ceiling,stimulus package,hawkish,dovish,"
        "consumer confidence,housing market",
    ).split(",")
    if kw.strip()
]

# --- Scripts vidéo courts (TikTok/Reels/Shorts) -------------------------------
# Génération quotidienne automatique de DEBRIEF (le débrief vidéo du soir),
# calée après le débrief texte (23h00). Les autres formats (REACTION/POURQUOI/
# PEDAGO/FACTCHECK/SEMAINE) restent disponibles à la demande via
# `python video_scripts.py --format ...` (voir README) mais ne sont plus générés
# automatiquement.
VIDEO_SCRIPTS_HOUR = 23
VIDEO_SCRIPTS_MINUTE = 30

VIDEO_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "video_output")

# Formulation fixe, redite à l'identique sur chaque script.
VIDEO_CTA_TEXT = "Retrouve toutes les news de trading en direct, le calendrier économique et les breaking news : le lien est en bio."

# Accroche courte affichée en grand sur la carte de fin (--render), au-dessus de
# VIDEO_CTA_TEXT affiché plus petit en dessous — jamais parlée séparément (le
# texte parlé reste VIDEO_CTA_TEXT seul, pour ne pas répéter deux fois le CTA à
# l'oral).
VIDEO_CTA_HEADLINE = "Le lien est en bio"

# Titre affiché ET dit en ouverture de chaque vidéo (--render), suivi de la date
# du jour ("{VIDEO_INTRO_TITLE}, du 26 juillet 2026.").
VIDEO_INTRO_TITLE = "Récap news éco & trading"

# Ajouté en fin de légende de chaque script (voir README, section "Monétiser",
# pour le contexte réglementaire derrière cette formulation).
VIDEO_DISCLAIMER = "Contenu informatif, pas un conseil personnalisé, aucune garantie."

# (secondes_min, secondes_max) par format — seul POURQUOI couvre 2 paliers de durée.
VIDEO_FORMAT_DURATIONS = {
    "REACTION": (45, 45),
    "POURQUOI": (45, 60),
    "PEDAGO": (30, 30),
    "FACTCHECK": (45, 45),
    "SEMAINE": (60, 60),
    "DEBRIEF": (75, 90),  # débrief vidéo du soir : événements du jour + breaking news
}

VIDEO_WORDS_PER_SECOND_FR = 2.5

# Fourchette de mots "texte oral" attendue par palier de durée. Un format à cheval
# sur 2 paliers (POURQUOI 45-60s, DEBRIEF 75-90s) prend l'union : (min du palier
# bas, max du palier haut).
VIDEO_DURATION_WORD_RANGES = {
    30: (70, 85),
    45: (105, 125),
    60: (140, 160),
    75: (172, 203),
    90: (207, 243),
}

VIDEO_RECOMMENDED_POST_TIME = {
    "REACTION": "le soir même, 18h-20h",
    "POURQUOI": "le soir même, 18h-20h",
    "PEDAGO": "en semaine, 12h-14h",
    "FACTCHECK": "dès génération (réactivité)",
    "SEMAINE": "dimanche, 18h-20h",
    "DEBRIEF": "chaque soir, dès que tu as validé le rendu",
}

# --- Rendu vidéo (local uniquement, à la demande via --render) ---------------
# Jamais utilisé par le job automatique du soir (Render) : dépendances lourdes,
# voir requirements-video.txt. `edge-tts --list-voices` liste les voix dispo.
VIDEO_TTS_VOICE = "fr-FR-HenriNeural"
# Vitesse de la voix, format edge-tts ("+0%" = normale, "+10%" = 10% plus vite).
VIDEO_TTS_RATE = "+8%"
VIDEO_RENDER_RESOLUTION = (1080, 1920)  # 9:16, TikTok/Reels/Shorts
VIDEO_RENDER_FPS = 30
VIDEO_RENDER_BG_COLOR = (15, 20, 30)  # fond uni sombre par défaut (repli sans clé Pexels)
VIDEO_RENDER_FONT_PATH = None  # None = auto-détection (polices macOS système)

# Carte d'intro/de fin (--render) : dégradé + liseré, pour un habillage plus
# soigné qu'un simple fond uni. Le bleu accent reprend la couleur "Réel" du
# graphique (voir video_renderer.build_chart_image) pour rester cohérent.
VIDEO_CARD_GRADIENT_TOP = (10, 14, 22)
VIDEO_CARD_GRADIENT_BOTTOM = (30, 41, 59)
VIDEO_CARD_ACCENT_COLOR = "#3b82f6"

# Sous-titres dynamiques : nombre de mots affichés ensemble à l'écran, synchros
# avec la voix (timing réel via edge-tts, pas une approximation).
VIDEO_CAPTION_MAX_WORDS_PER_CHUNK = 3

# Structure du DEBRIEF : deux sections distinctes (événements macro publiés vs
# breaking news), voir video_templates/debrief.txt et video_scripts._assemble_script
# (qui regroupe et trie les blocs par section — jamais mélangés). Utilisé pour la
# légende affichée en haut de chaque bloc corps (--render) et pour la petite carte
# de transition entre les deux sections quand les deux sont présentes ce soir-là.
VIDEO_SECTION_LABEL_EVENEMENT = "ÉVÉNEMENTS DU JOUR"
VIDEO_SECTION_LABEL_BREAKING = "BREAKING NEWS"
VIDEO_SECTION_BREAKING_ACCENT_COLOR = "#ef4444"  # rouge, distinct du bleu VIDEO_CARD_ACCENT_COLOR
VIDEO_SECTION_TRANSITION_SECONDS = 1.3  # durée de la carte de transition (silencieuse)

# Fond vidéo en boucle (optionnel, nécessite PEXELS_API_KEY ci-dessus). Thème de
# recherche Pexels par format — modifiable librement, pas besoin de toucher au code.
STOCK_FOOTAGE_DIR = os.path.join(os.path.dirname(__file__), "stock_footage")
STOCK_FOOTAGE_THEME_BY_FORMAT = {
    "REACTION": "stock market finance",
    "POURQUOI": "stock market finance",
    "FACTCHECK": "newspaper finance",
    "PEDAGO": "office work business",
    "SEMAINE": "office work business",
    "DEBRIEF": "stock market finance",
}
# Nombre de clips téléchargés au premier passage pour un thème de secours
# (format-level, réutilisé souvent) vs. un mot-clé de contenu ponctuel généré par
# l'IA pour un bloc précis (voir video_renderer._background_clip) — pas besoin
# d'une grosse réserve pour un mot-clé qui ne reviendra peut-être jamais.
STOCK_FOOTAGE_FALLBACK_CLIP_COUNT = 5
STOCK_FOOTAGE_CONTENT_CLIP_COUNT = 2

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
