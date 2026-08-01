"""
Envoi des messages Telegram + mise en forme des 3 templates d'alerte.
Utilise directement l'API HTTP Telegram (pas de lib dédiée) pour rester léger.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

import config
import message_log

logger = logging.getLogger("telegram_bot")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 15
MAX_RETRIES = 3

IMPACT_EMOJI = {"High": "🔴", "Medium": "🟠"}


def escape_md(text: str) -> str:
    """Échappe les caractères spéciaux du Markdown Telegram (mode legacy)."""
    if not text:
        return ""
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


def _post(payload: dict) -> requests.Response | None:
    url = API_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    try:
        return requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Échec réseau envoi Telegram: %s", exc)
        return None


def send(text: str, chat_id: str | None = None) -> bool:
    """Envoie à un chat_id donné (message perso par défaut)."""
    target = chat_id or config.TELEGRAM_CHAT_ID
    if not config.TELEGRAM_BOT_TOKEN or not target:
        logger.error("TELEGRAM_BOT_TOKEN ou chat_id manquant, message non envoyé.")
        return False

    base_payload = {
        "chat_id": target,
        "text": text,
        "disable_web_page_preview": True,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        resp = _post({**base_payload, "parse_mode": "Markdown"})
        if resp is not None and resp.status_code == 200:
            return True

        if resp is not None and resp.status_code == 400 and "parse" in resp.text.lower():
            # Un caractère dans une news externe a cassé le Markdown : on renvoie
            # en texte brut plutôt que de perdre l'alerte.
            logger.warning("Markdown invalide, repli en texte brut: %s", resp.text[:300])
            plain_resp = _post(base_payload)
            if plain_resp is not None and plain_resp.status_code == 200:
                return True
            break  # inutile de réessayer, le texte brut a déjà échoué

        if resp is not None:
            logger.warning("Telegram a répondu %s (tentative %d/%d): %s", resp.status_code, attempt, MAX_RETRIES, resp.text[:300])
        if attempt < MAX_RETRIES:
            time.sleep(2 * attempt)

    logger.error("Échec définitif de l'envoi Telegram.")
    return False


def broadcast(text: str) -> bool:
    """
    Envoie une alerte "contenu" (résumé, avant/après news, breaking news) au
    chat perso ET au canal payant si TELEGRAM_CHANNEL_ID est configuré. Les
    messages opérationnels (démarrage, erreurs) doivent utiliser send()
    directement pour rester perso uniquement.
    Renvoie True si l'envoi perso a réussi (le canal est secondaire : son échec
    est loggé mais ne doit pas faire perdre l'alerte perso).

    Archive aussi le message dans le journal durable (voir message_log.py) si
    l'envoi perso a réussi — une fois ici, pas à chaque appel de send(), pour
    ne pas dupliquer l'entrée quand le canal payant reçoit le même texte.
    """
    ok = send(text)
    if config.TELEGRAM_CHANNEL_ID:
        if not send(text, chat_id=config.TELEGRAM_CHANNEL_ID):
            logger.warning("Échec de la diffusion vers le canal payant (l'envoi perso a été tenté séparément).")
    if ok:
        message_log.log_message("perso", text)
    return ok


def _time_local(event_dt_utc_iso: str) -> str:
    dt = datetime.fromisoformat(event_dt_utc_iso).astimezone(config.TIMEZONE)
    return dt.strftime("%Hh%M")


def _minutes_until(event_dt_utc_iso: str) -> int:
    dt = datetime.fromisoformat(event_dt_utc_iso)
    delta = dt - datetime.now(timezone.utc)
    return max(0, round(delta.total_seconds() / 60))


def _reclassified_marker(event: dict) -> str:
    """Marque discrètement un event que ForexFactory classait "Low" mais que
    l'IA a jugé plus important — pour que l'utilisateur puisse distinguer une
    évaluation ForexFactory native d'un jugement IA, et calibrer sa confiance."""
    return " 🤖" if event.get("ai_reclassified") else ""


def _ai_block(ai: dict | None) -> str:
    if not ai:
        return "💡 _Analyse IA indisponible pour cette news._"
    lines = [f"💡 {ai['resume']}"]
    if ai.get("biais"):
        # Une paire par ligne plutôt qu'une liste séparée par virgules : reste
        # lisible sur mobile même quand une news USD concerne 8-9 paires
        # (XAUUSD, indices, BTC/ETH...) d'un coup.
        lines.append("📐 Biais :")
        lines.extend(f"   {pair} {label}" for pair, label in ai["biais"].items())
    if ai.get("raisonnement"):
        lines.append(f"🧠 {ai['raisonnement']}")
    if ai.get("danger"):
        lines.append(ai["danger"])
    return "\n".join(lines)


# --- Templates --------------------------------------------------------------------

def format_daily_summary(events: list[dict], overview: str | None) -> str:
    today_str = datetime.now(config.TIMEZONE).strftime("%d/%m/%Y")
    if not events:
        body = "Aucune news à impact fort/moyen (USD/EUR/GBP) prévue aujourd'hui."
    else:
        lines = []
        for e in events:
            emoji = IMPACT_EMOJI.get(e["impact"], "⚪")
            lines.append(
                f"{emoji} {_time_local(e['event_dt_utc'])} — {escape_md(e['title'])} ({e['currency']}){_reclassified_marker(e)}"
            )
        body = f"{len(events)} news à surveiller aujourd'hui (heure Bruxelles) :\n\n" + "\n".join(lines)

    msg = f"☀️ *Résumé du jour — {today_str}*\n\n{body}"
    if overview:
        msg += f"\n\n💡 {overview}"
    return msg


def format_evening_debrief(events: list[dict], news_items: list[dict], recap: str | None) -> str:
    today_str = datetime.now(config.TIMEZONE).strftime("%d/%m/%Y")
    parts = [f"🌙 *Débrief du soir — {today_str}*", "_Clôture de la session de New York_"]

    if events:
        parts.append(f"\n📊 {len(events)} news publiées aujourd'hui :")
        for e in events:
            emoji = IMPACT_EMOJI.get(e["impact"], "⚪")
            actual = e.get("actual") or "indisponible"
            forecast = e.get("forecast") or "N/A"
            previous = e.get("previous") or "N/A"
            parts.append(
                f"{emoji} {_time_local(e['event_dt_utc'])} — {escape_md(e['title'])} ({e['currency']}){_reclassified_marker(e)} : "
                f"réel {actual} | prévision {forecast} | précédent {previous}"
            )
    else:
        parts.append("\n📊 Aucune news à impact fort/moyen publiée aujourd'hui.")

    if news_items:
        parts.append(f"\n🚨 {len(news_items)} breaking news aujourd'hui :")
        for n in news_items:
            parts.append(f"• {escape_md(n['title'])}")

    if recap:
        parts.append(f"\n💡 {recap}")

    return "\n".join(parts)


def format_before_alert(event: dict, concerned_pairs: list[str], ai: dict | None) -> str:
    emoji = IMPACT_EMOJI.get(event["impact"], "🔴")
    minutes = _minutes_until(event["event_dt_utc"])
    header = f"{emoji} *{escape_md(event['title'])} — {event['currency']}*{_reclassified_marker(event)}"
    meta = f"🕒 {_time_local(event['event_dt_utc'])} (Bruxelles) — dans {minutes} min"
    forecast = event.get("forecast") or "N/A"
    previous = event.get("previous") or "N/A"
    data_line = f"📊 Prévision : {forecast} | Précédent : {previous}"
    # Formulation volontairement neutre (pas d'action imposée type "ferme tes
    # positions") : le canal touche aussi bien des intraday que des swing
    # traders, et "ferme les positions en cours" n'a pas de sens pour ces
    # derniers — on signale le risque, chacun adapte selon son propre style.
    risk_line = "⚠️ Publication à risque imminente — vigilance sur tes positions ouvertes et le spread"
    pairs_line = f"📌 Paires concernées : {', '.join(concerned_pairs)}" if concerned_pairs else ""

    parts = [header, meta, data_line, risk_line]
    if pairs_line:
        parts.append(pairs_line)
    parts.append(_ai_block(ai))
    return "\n".join(p for p in parts if p)


def format_after_alert(event: dict, actual: str | None, concerned_pairs: list[str], ai: dict | None) -> str:
    emoji = IMPACT_EMOJI.get(event["impact"], "🔴")
    header = f"{emoji} *{escape_md(event['title'])} — {event['currency']}*{_reclassified_marker(event)} (résultat)"
    meta = f"🕒 {_time_local(event['event_dt_utc'])} (Bruxelles)"
    forecast = event.get("forecast") or "N/A"
    previous = event.get("previous") or "N/A"
    actual_str = actual if actual else "indisponible pour l'instant"
    data_line = f"📊 Réel : {actual_str} | Prévision : {forecast} | Précédent : {previous}"
    # Même logique que format_before_alert : on décrit le risque (volatilité,
    # spread élargi) sans dicter une action ("attends X min avant de retrader"
    # ne concerne que l'intraday) — utile aussi bien en intraday qu'en swing.
    risk_line = f"⚠️ Volatilité et spread élargi possibles dans les {config.NO_TRADE_WINDOW_MINUTES} min qui suivent"

    if not actual and ai:
        # Sans résultat réel, un biais "➖ Neutre" ressemblerait à un vrai jugement
        # ("rien ne bouge") alors qu'on n'a en fait aucune donnée pour juger — on
        # l'affiche donc explicitement comme indisponible plutôt que de laisser
        # confondre les deux. Le reste de l'analyse IA (résumé/raisonnement/danger)
        # reste affiché tel quel, il garde de la valeur même sans le chiffre.
        ai = dict(ai)
        ai["biais"] = {pair: "❔ Indisponible" for pair in concerned_pairs}

    parts = [header, meta, data_line, risk_line, _ai_block(ai)]
    return "\n".join(p for p in parts if p)


def format_breaking_news_alert(article: dict) -> str:
    # "importance" (⭐ à ⭐⭐⭐, portée de la news) est distinct de "danger" (dans
    # _ai_block : faut-il trader ou pas) — les deux répondent à une question différente.
    importance = article.get("importance")
    header = f"🚨 *Breaking News* {importance}" if importance else "🚨 *Breaking News*"
    title_line = f"📰 {escape_md(article.get('titre_fr') or article['title'])}"
    source_line = f"🗞️ Source : {escape_md(article['source'])}"
    # Les champs d'analyse IA (resume/biais/...) sont mergés dans le même dict que
    # les métadonnées (title/source/url) : on isole ici ce qui va dans le bloc IA
    # pour que _ai_block affiche correctement le repli si l'IA n'a rien produit.
    ai = {k: article[k] for k in ("resume", "biais", "raisonnement", "danger") if article.get(k)}
    parts = [header, title_line, source_line, _ai_block(ai or None)]
    if article.get("url"):
        parts.append(f"🔗 [Lire l'article]({article['url']})")
    return "\n".join(p for p in parts if p)


def format_error_alert(source: str, message: str) -> str:
    return (
        f"⚠️ *Erreur agent — {escape_md(source)}*\n"
        f"{escape_md(message)}\n\n"
        f"L'agent continue de tourner normalement pour le reste."
    )


def format_startup_message() -> str:
    pairs = ", ".join(config.TRADING_PAIRS)
    return (
        "🟢 *Agent trading news démarré*\n"
        f"Paires suivies : {pairs}\n"
        f"Résumé quotidien : {config.DAILY_SUMMARY_HOUR:02d}h{config.DAILY_SUMMARY_MINUTE:02d} (Bruxelles)\n"
        "Alertes calendrier + breaking news actives H24."
    )
