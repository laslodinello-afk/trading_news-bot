"""
Point d'entrée de l'agent.

Lance :
- un petit serveur HTTP "keep-alive" (nécessaire pour tourner gratuitement 24/7
  sur Render sans que le service ne se mette en veille, voir README.md)
- un scheduler APScheduler avec 6 jobs : rafraîchissement du calendrier,
  résumé quotidien 8h, débrief du soir 23h, alertes avant/après publication,
  veille breaking news.

Usage :
    python main.py            # lance l'agent en continu
    python main.py --test     # envoie des messages de démo sur Telegram puis quitte
"""
from __future__ import annotations

import argparse
import difflib
import hmac
import json
import logging
import sys
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import ai_analyzer
import calendar_fetcher
import config
import db
import news_watcher
import telegram_bot
import video_scripts

logger = logging.getLogger("main")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        ],
    )


# --- serveur keep-alive (Render a besoin d'un port ouvert qui répond) -----------
# + endpoint /sync (voir render_sync.py) : donne accès en lecture aux vraies
# données du jour (events + breaking news réellement captés/envoyés par CE
# service) à un script local, pour que la génération DEBRIEF locale reflète ce
# qui a vraiment été publié sur Telegram plutôt qu'une reconstruction partielle
# à partir des sources brutes. Protégé par SYNC_API_KEY (comparaison à temps
# constant) : sans clé configurée ou avec la mauvaise clé, toujours 401.

def build_sync_response(query_string: str, auth_header: str | None) -> tuple[int, str, bytes]:
    """Logique pure (aucun I/O réseau/socket) derrière /sync, isolée pour être
    testable sans ouvrir de vrai serveur HTTP. Renvoie (status_code,
    content_type, body_bytes)."""
    if not config.SYNC_API_KEY or not auth_header or not hmac.compare_digest(auth_header, config.SYNC_API_KEY):
        return 401, "text/plain", b"unauthorized"

    params = urllib.parse.parse_qs(query_string)
    date_str = (params.get("date") or [None])[0]
    try:
        target_date = date.fromisoformat(date_str) if date_str else datetime.now(config.TIMEZONE).date()
    except ValueError:
        return 400, "text/plain", b"invalid date (attendu YYYY-MM-DD)"

    day_start_utc, day_end_utc = db.local_day_bounds_utc(target_date)
    events = [dict(row) for row in db.get_events_for_day(day_start_utc, day_end_utc)]
    news = [dict(row) for row in db.get_news_for_day(day_start_utc, day_end_utc)]
    payload = {"date": target_date.isoformat(), "events": events, "news": news}
    return 200, "application/json", json.dumps(payload).encode("utf-8")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/sync":
            status, content_type, body = build_sync_response(parsed.query, self.headers.get("X-Sync-Key"))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):  # noqa: A002 - signature imposée par BaseHTTPRequestHandler
        pass  # évite un log par ping de service de keep-alive


def start_keepalive_server() -> None:
    server = HTTPServer(("0.0.0.0", config.PORT), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Serveur keep-alive démarré sur le port %d (endpoints / et /sync)", config.PORT)


# --- gestion d'erreurs centralisée ----------------------------------------------

def notify_error(source: str, exc: Exception) -> None:
    logger.exception("Erreur dans %s", source)
    error_key = f"{source}:{type(exc).__name__}"
    if db.error_recently_sent(error_key):
        return
    telegram_bot.send(telegram_bot.format_error_alert(source, str(exc)))
    db.mark_error_sent(error_key)


def _event_row_to_dict(row) -> dict:
    return {
        "event_key": row["event_key"],
        "title": row["title"],
        "currency": row["currency"],
        "impact": row["impact"],
        "event_dt_utc": row["event_dt_utc"],
        "forecast": row["forecast"],
        "previous": row["previous"],
        "actual": row["actual"],
        "ai_reclassified": bool(row["ai_reclassified"]),
    }


# --- jobs -------------------------------------------------------------------------

def job_refresh_calendar() -> None:
    try:
        events, source = calendar_fetcher.refresh_calendar()
        high_medium = [e for e in events if e["impact"] in ("High", "Medium")]
        low = [e for e in events if e["impact"] == "Low"]

        upgraded = ai_analyzer.reclassify_low_impact(low) if low else []

        for event in high_medium + upgraded:
            db.upsert_event(event)

        logger.info(
            "Calendrier rafraîchi (%d High/Medium + %d Low upgradés par l'IA sur %d Low soumis, source=%s)",
            len(high_medium), len(upgraded), len(low), source,
        )
    except Exception as exc:
        notify_error("calendrier économique", exc)


def job_daily_summary() -> None:
    try:
        day_start_utc, day_end_utc = db.local_day_bounds_utc(datetime.now(config.TIMEZONE).date())
        rows = db.get_events_for_day(day_start_utc, day_end_utc)

        events = [_event_row_to_dict(r) for r in rows]
        for e in events:
            e["time_local"] = datetime.fromisoformat(e["event_dt_utc"]).astimezone(config.TIMEZONE).strftime("%Hh%M")

        overview = ai_analyzer.daily_overview(events) if events else None
        telegram_bot.broadcast(telegram_bot.format_daily_summary(events, overview))
        logger.info("Résumé quotidien envoyé (%d events)", len(events))
    except Exception as exc:
        notify_error("résumé quotidien", exc)


def job_evening_debrief() -> None:
    try:
        day_start_utc, day_end_utc = db.local_day_bounds_utc(datetime.now(config.TIMEZONE).date())

        event_rows = db.get_events_for_day(day_start_utc, day_end_utc)
        events = [_event_row_to_dict(r) for r in event_rows]
        for e in events:
            e["time_local"] = datetime.fromisoformat(e["event_dt_utc"]).astimezone(config.TIMEZONE).strftime("%Hh%M")

        news_rows = db.get_news_for_day(day_start_utc, day_end_utc)
        news_items = [{"title": r["title"], "resume": r["resume"]} for r in news_rows]

        recap = ai_analyzer.evening_debrief(events, news_items) if (events or news_items) else None
        telegram_bot.broadcast(telegram_bot.format_evening_debrief(events, news_items, recap))
        logger.info("Débrief du soir envoyé (%d events, %d breaking news)", len(events), len(news_items))
    except Exception as exc:
        notify_error("débrief du soir", exc)


def job_generate_video_scripts() -> None:
    try:
        video_scripts.generate_daily_batch(datetime.now(config.TIMEZONE).date())
        logger.info("Script du débrief vidéo quotidien généré.")
    except Exception as exc:
        notify_error("scripts vidéo", exc)


def job_check_before_alerts() -> None:
    try:
        now = datetime.now(timezone.utc)
        # Fenêtre large [maintenant, +30min[ plutôt qu'un créneau étroit autour de
        # +30min : reste correct même après un redémarrage ou un tick manqué,
        # la dédup en base empêche tout doublon.
        rows = db.get_events_needing_before_alert(now, now + timedelta(minutes=config.ALERT_BEFORE_MINUTES))
        for row in rows:
            event = _event_row_to_dict(row)
            concerned_pairs = config.pairs_for_currency(event["currency"])
            ai = ai_analyzer.analyze_before(event, concerned_pairs)
            if telegram_bot.broadcast(telegram_bot.format_before_alert(event, concerned_pairs, ai)):
                db.mark_sent(event["event_key"], "before")
                logger.info("Alerte 'avant' envoyée: %s", event["title"])
            else:
                logger.warning("Échec d'envoi de l'alerte 'avant' pour %s, nouvel essai au prochain tick.", event["title"])
    except Exception as exc:
        notify_error("alerte avant-news", exc)


def job_check_after_alerts() -> None:
    try:
        now = datetime.now(timezone.utc)
        rows = db.get_events_needing_after_alert(now)
        for row in rows:
            event = _event_row_to_dict(row)
            event_dt = datetime.fromisoformat(event["event_dt_utc"])
            age_minutes = (now - event_dt).total_seconds() / 60

            actual = event["actual"]
            if not actual:
                actual = calendar_fetcher.fetch_actual_result(
                    event["currency"], event["title"], event_dt,
                    event.get("forecast"), event.get("previous"),
                )
                if actual:
                    db.set_event_actual(event["event_key"], actual)

            if not actual and age_minutes < config.ACTUAL_RESULT_GRACE_MINUTES:
                continue  # laisse une chance au(x) prochain(s) tick(s) de trouver le résultat réel

            concerned_pairs = config.pairs_for_currency(event["currency"])
            ai = ai_analyzer.analyze_after(event, concerned_pairs, actual)
            if telegram_bot.broadcast(telegram_bot.format_after_alert(event, actual, concerned_pairs, ai)):
                db.mark_sent(event["event_key"], "after")
                logger.info("Alerte 'après' envoyée: %s (actual=%s)", event["title"], actual)
            else:
                logger.warning("Échec d'envoi de l'alerte 'après' pour %s, nouvel essai au prochain tick.", event["title"])
    except Exception as exc:
        notify_error("alerte après-publication", exc)


# Un même flash est parfois republié par la source sous une URL différente
# (constaté : ForexLive republie un titre quasi identique à quelques minutes
# d'intervalle) — ça contourne le dédup par URL ci-dessus. Filet de sécurité
# supplémentaire : si le TITRE ressemble fortement à une news déjà envoyée
# récemment, on l'ignore aussi, même si son URL est nouvelle.
BREAKING_NEWS_DUPLICATE_TITLE_THRESHOLD = 0.85
BREAKING_NEWS_DUPLICATE_LOOKBACK_HOURS = 6


def _is_duplicate_title(title: str, recent_titles: list[str]) -> bool:
    return any(
        difflib.SequenceMatcher(None, title.lower(), other.lower()).ratio() >= BREAKING_NEWS_DUPLICATE_TITLE_THRESHOLD
        for other in recent_titles
    )


def job_check_breaking_news() -> None:
    try:
        candidates = news_watcher.fetch_candidates()
        new_candidates = [c for c in candidates if not db.already_sent_news(c["news_key"])]
        if not new_candidates:
            return

        recent_titles = list(db.get_recently_sent_titles(BREAKING_NEWS_DUPLICATE_LOOKBACK_HOURS))
        deduped_candidates = []
        for c in new_candidates:
            if _is_duplicate_title(c["title"], recent_titles):
                logger.info("Breaking news ignorée (titre quasi identique à une news déjà envoyée): %s", c["title"])
                db.mark_sent_news(c["news_key"])  # vu, jamais renvoyé ni re-soumis à l'IA
                continue
            recent_titles.append(c["title"])  # évite aussi un doublon entre 2 candidats du même lot
            deduped_candidates.append(c)
        if not deduped_candidates:
            return

        day_start_utc, day_end_utc = db.local_day_bounds_utc(datetime.now(config.TIMEZONE).date())
        known_calendar_titles = [row["title"] for row in db.get_events_for_day(day_start_utc, day_end_utc)]

        relevant = ai_analyzer.filter_breaking_news(deduped_candidates, known_calendar_titles)
        relevant_by_key = {a["news_key"]: a for a in relevant}
        sent_keys = set()
        for article in relevant:
            if article["importance"].count("⭐") < config.BREAKING_NEWS_MIN_STARS_FOR_ALERT:
                continue  # trop mineur pour une alerte individuelle, gardé quand même pour le débrief du soir (voir plus bas)
            if telegram_bot.broadcast(telegram_bot.format_breaking_news_alert(article)):
                sent_keys.add(article["news_key"])
                logger.info("Alerte breaking news envoyée: %s", article["title"])
            else:
                logger.warning("Échec d'envoi breaking news pour %s, nouvel essai au prochain tick.", article["title"])

        # On marque comme "vus" : les candidats écartés par l'IA (jamais re-soumis à
        # l'IA ensuite, title/resume=NULL) + ceux retenus par l'IA, qu'ils aient été
        # envoyés en alerte OU seulement gardés pour le débrief du soir faute
        # d'importance suffisante (title/resume renseignés dans les deux cas). Un
        # candidat retenu ET au-dessus du seuil dont l'envoi Telegram a échoué reste
        # "non vu" pour être retenté au prochain tick.
        for c in deduped_candidates:
            article = relevant_by_key.get(c["news_key"])
            if article is None:
                db.mark_sent_news(c["news_key"])
            elif c["news_key"] in sent_keys or article["importance"].count("⭐") < config.BREAKING_NEWS_MIN_STARS_FOR_ALERT:
                db.mark_sent_news(c["news_key"], title=article["title"], resume=article.get("resume"))
    except Exception as exc:
        notify_error("veille breaking news", exc)


# --- mode --test --------------------------------------------------------------------

def run_test_mode() -> None:
    print("=== Test de configuration de l'agent ===\n")
    missing = config.validate()
    if missing:
        print(f"⚠️  Variables manquantes dans .env : {', '.join(missing)}")
        print("   (le test continue quand même avec ce qui est disponible)\n")

    sample_event = {
        "title": "Non-Farm Payrolls (NFP)",
        "currency": "USD",
        "impact": "High",
        "event_dt_utc": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "forecast": "180K",
        "previous": "227K",
    }
    sample_event["time_local"] = (
        datetime.fromisoformat(sample_event["event_dt_utc"]).astimezone(config.TIMEZONE).strftime("%Hh%M")
    )
    concerned_pairs = config.pairs_for_currency("USD")
    results = {}

    print("1/7 — Message de connexion Telegram...")
    results["connexion"] = telegram_bot.send(
        "🧪 *Test de l'agent trading news*\nSi tu vois ce message, la connexion Telegram fonctionne !"
    )
    print("   ✅ OK" if results["connexion"] else "   ❌ Échec — vérifie TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env")

    if config.TELEGRAM_CHANNEL_ID:
        print(f"   ℹ️  Canal payant configuré ({config.TELEGRAM_CHANNEL_ID}) — les alertes ci-dessous y seront aussi envoyées.")

    print("2/7 — Résumé quotidien (exemple)...")
    results["resume"] = telegram_bot.broadcast(
        telegram_bot.format_daily_summary([sample_event], "Exemple d'aperçu généré par l'IA chaque matin à 8h.")
    )
    print("   ✅ OK" if results["resume"] else "   ❌ Échec")

    print("3/7 — Appel réel à Gemini (vérifie GEMINI_API_KEY)...")
    ai_result = ai_analyzer.analyze_before(sample_event, concerned_pairs)
    print("   ✅ Gemini a répondu correctement" if ai_result else "   ❌ Pas de réponse IA — vérifie GEMINI_API_KEY")

    print("4/7 — Alerte 'avant news' (exemple)...")
    results["avant"] = telegram_bot.broadcast(telegram_bot.format_before_alert(sample_event, concerned_pairs, ai_result))
    print("   ✅ OK" if results["avant"] else "   ❌ Échec")

    print("5/7 — Alerte 'après publication' (exemple)...")
    results["apres"] = telegram_bot.broadcast(
        telegram_bot.format_after_alert(sample_event, "206K", concerned_pairs, ai_result)
    )
    print("   ✅ OK" if results["apres"] else "   ❌ Échec")

    print("6/7 — Alerte breaking news (exemple)...")
    sample_article = {
        "title": "Exemple : déclaration surprise d'un responsable de la Fed sur les taux",
        "source": "Exemple News",
        "url": "https://example.com",
        "importance": "⭐⭐⭐",
    }
    if ai_result:
        sample_article.update(ai_result)
    results["breaking"] = telegram_bot.broadcast(telegram_bot.format_breaking_news_alert(sample_article))
    print("   ✅ OK" if results["breaking"] else "   ❌ Échec")

    print("7/7 — Débrief du soir (exemple, 23h)...")
    sample_debrief_event = dict(sample_event, actual="206K")
    sample_news_items = [{"title": sample_article["title"], "resume": sample_article.get("resume", "")}]
    debrief_recap = ai_analyzer.evening_debrief([sample_debrief_event], sample_news_items)
    results["debrief"] = telegram_bot.broadcast(
        telegram_bot.format_evening_debrief([sample_debrief_event], sample_news_items, debrief_recap)
    )
    print("   ✅ OK" if results["debrief"] else "   ❌ Échec")

    telegram_bot.send(
        "✅ *Test terminé.* Si tu as reçu tous les messages ci-dessus, l'agent est prêt à tourner "
        "en continu (voir README.md, section déploiement 24/7)."
    )

    print("\n=== Résultat ===")
    if all(results.values()) and ai_result:
        print("✅ Tout fonctionne ! Tu peux déployer l'agent (voir README.md, section déploiement).")
    elif all(results.values()):
        print("⚠️  Telegram fonctionne mais pas l'IA — vérifie ta clé GEMINI_API_KEY dans .env.")
        sys.exit(1)
    else:
        print("❌ Certains messages n'ont pas pu être envoyés — vérifie ton .env et le fichier agent.log.")
        sys.exit(1)


# --- entrypoint ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Agent de veille news économiques pour trading.")
    parser.add_argument("--test", action="store_true", help="Envoie des messages de démo sur Telegram puis quitte.")
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    if args.test:
        run_test_mode()
        return

    missing = config.validate()
    if missing:
        logger.error("Variables .env manquantes : %s — l'agent ne peut pas démarrer.", ", ".join(missing))
        sys.exit(1)

    start_keepalive_server()

    job_refresh_calendar()
    telegram_bot.send(telegram_bot.format_startup_message())

    scheduler = BlockingScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        job_refresh_calendar, IntervalTrigger(hours=config.CALENDAR_REFRESH_HOURS), id="refresh_calendar"
    )
    scheduler.add_job(
        job_daily_summary,
        CronTrigger(hour=config.DAILY_SUMMARY_HOUR, minute=config.DAILY_SUMMARY_MINUTE, timezone=config.TIMEZONE),
        id="daily_summary",
    )
    scheduler.add_job(
        job_evening_debrief,
        CronTrigger(hour=config.EVENING_DEBRIEF_HOUR, minute=config.EVENING_DEBRIEF_MINUTE, timezone=config.TIMEZONE),
        id="evening_debrief",
    )
    scheduler.add_job(
        job_generate_video_scripts,
        CronTrigger(hour=config.VIDEO_SCRIPTS_HOUR, minute=config.VIDEO_SCRIPTS_MINUTE, timezone=config.TIMEZONE),
        id="video_scripts",
    )
    scheduler.add_job(
        job_check_before_alerts,
        IntervalTrigger(minutes=config.CALENDAR_CHECK_INTERVAL_MINUTES),
        id="check_before",
    )
    scheduler.add_job(
        job_check_after_alerts,
        IntervalTrigger(minutes=config.CALENDAR_CHECK_INTERVAL_MINUTES),
        id="check_after",
    )
    scheduler.add_job(
        job_check_breaking_news,
        IntervalTrigger(minutes=config.BREAKING_NEWS_INTERVAL_MINUTES),
        id="breaking_news",
    )

    logger.info("Scheduler démarré — agent actif H24 7/7 (heure Bruxelles actuelle: %s).", datetime.now(config.TIMEZONE))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Arrêt de l'agent demandé.")


if __name__ == "__main__":
    main()
