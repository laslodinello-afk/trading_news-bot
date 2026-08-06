"""
Génère des scripts vidéo courts (TikTok/Reels/Shorts) à partir des données déjà
collectées par l'agent (calendrier économique en base, breaking news captées).
Ne publie nulle part : écrit des fichiers Markdown + JSON dans
video_output/<date>/<format>.{md,json} — un humain valide et tourne, c'est volontaire.

Formats : DEBRIEF (débrief vidéo du soir, généré automatiquement chaque soir),
REACTION, POURQUOI, PEDAGO, FACTCHECK, SEMAINE (à la demande). Le texte
éditorial de chaque format vit dans video_templates/*.txt, pas dans ce fichier.

Usage CLI :
    python video_scripts.py --format REACTION
    python video_scripts.py --format PEDAGO --concept "Taux directeur" --dry-run
    python video_scripts.py --format ALL --date 2026-07-26
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

import ai_analyzer
import config
import db
import message_log

logger = logging.getLogger("video_scripts")

FORMATS = ["DEBRIEF", "REACTION", "POURQUOI", "PEDAGO", "FACTCHECK", "SEMAINE"]

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "video_templates")
_MAX_LLM_TOKENS = 1500  # DEBRIEF va jusqu'à ~240 mots + un visual_keyword par bloc
_MAX_EVENTS_IN_PROMPT = 15  # évite un prompt géant un jour très chargé en news

# Ordre imposé des sections d'un DEBRIEF (voir video_templates/debrief.txt) :
# les breaking news développées en détail d'abord, puis TOUJOURS en tout
# dernier le récap compact de l'ensemble des news économiques du calendrier
# (confirmées ET en attente) — jamais un événement macro développé comme un
# bloc à part en tête de vidéo, voir debrief.txt pour le raisonnement complet.
_DEBRIEF_SECTION_ORDER = {"BREAKING": 0, "RECAP": 1}

_SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "required": ["hook", "corps", "chute", "legende", "hashtags"],
    "properties": {
        "hook": {"type": "STRING"},
        "corps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["oral", "ecran", "visuel", "visual_keyword"],
                "properties": {
                    "oral": {"type": "STRING"},
                    "ecran": {"type": "STRING"},
                    "visuel": {"type": "STRING"},
                    "visual_keyword": {"type": "STRING"},
                    # Seulement significatif pour DEBRIEF ("EVENEMENT"/"BREAKING",
                    # voir video_templates/debrief.txt) — chaîne vide pour les autres
                    # formats, jamais requis pour ne pas forcer une valeur absurde.
                    "section": {"type": "STRING"},
                },
            },
        },
        "chute": {"type": "STRING"},
        "legende": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
}


# --- chargement des templates (texte éditorial, jamais codé en dur ici) ----------

_system_prompt_cache: str | None = None


def _load_template(filename: str) -> str:
    with open(os.path.join(_TEMPLATES_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def _system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = _load_template("_system.txt")
    return _system_prompt_cache


# --- gathering de données (réutilise db.py, aucune nouvelle source) --------------

def _row_to_event(row) -> dict:
    return {
        "title": row["title"],
        "currency": row["currency"],
        "impact": row["impact"],
        "event_dt_utc": row["event_dt_utc"],
        "forecast": row["forecast"],
        "previous": row["previous"],
        "actual": row["actual"],
    }


def _format_events_block(events: list[dict]) -> str:
    lines = []
    for e in events[:_MAX_EVENTS_IN_PROMPT]:
        local_dt = datetime.fromisoformat(e["event_dt_utc"]).astimezone(config.TIMEZONE)
        lines.append(
            f"- {local_dt.strftime('%a %d/%m %Hh%M')} | {e['currency']} | {e['impact']} | {e['title']} | "
            f"réel={e.get('actual') or 'N/A'} prévision={e.get('forecast') or 'N/A'} précédent={e.get('previous') or 'N/A'}"
        )
    return "\n".join(lines)


def _format_headlines_block(rows) -> str:
    lines = []
    for r in rows[:_MAX_EVENTS_IN_PROMPT]:
        lines.append(f"- {r['title']}" + (f" — {r['resume']}" if r["resume"] else ""))
    return "\n".join(lines)


def _format_telegram_messages_block(messages: list[dict]) -> str:
    """Texte brut déjà envoyé sur Telegram (alertes avant/après, breaking news,
    résumés — voir telegram_bot.broadcast/message_log.py), pour que le DEBRIEF
    puise ses explications (raisonnement, biais déjà publiés) dans ce qui a
    vraiment été communiqué plutôt que de les réinventer de zéro."""
    lines = []
    for m in messages[:_MAX_EVENTS_IN_PROMPT]:
        local_dt = datetime.fromisoformat(m["sent_at"]).astimezone(config.TIMEZONE)
        lines.append(f"--- Message envoyé à {local_dt.strftime('%Hh%M')} ---\n{m['raw_text']}")
    return "\n\n".join(lines)


def _pick_top_event_title(events: list[dict]) -> str:
    """High avant Medium, puis le plus tôt dans la journée."""
    return sorted(events, key=lambda e: (e["impact"] != "High", e["event_dt_utc"]))[0]["title"]


def _gather_data(fmt: str, target_date: date, concept_override: str | None = None) -> dict | None:
    """Renvoie le contexte spécifique au format à injecter dans son template, ou
    None si rien d'exploitable pour cette date (l'appelant logue et passe au
    format suivant plutôt que de générer un script vide)."""
    day_start_utc, day_end_utc = db.local_day_bounds_utc(target_date)

    if fmt == "DEBRIEF":
        rows = db.get_events_for_day(day_start_utc, day_end_utc)
        confirmed = [_row_to_event(r) for r in rows if r["actual"]]
        # Prévus aujourd'hui mais sans résultat confirmé pour l'instant (voir
        # debrief.txt, règle 6) : jamais passés sous silence, jamais un chiffre
        # inventé — juste mentionnés comme "en attente" en toute fin de vidéo.
        pending = [_row_to_event(r) for r in rows if not r["actual"]]
        news_rows = db.get_news_for_day(day_start_utc, day_end_utc)
        if not confirmed and not pending and not news_rows:
            return None
        telegram_messages = message_log.get_messages_for_day(target_date)
        return {
            "events_block": _format_events_block(confirmed) if confirmed else "Aucun événement macro confirmé aujourd'hui.",
            "pending_events_block": _format_events_block(pending) if pending else "Aucun événement en attente de résultat.",
            "headlines_block": _format_headlines_block(news_rows) if news_rows else "Aucune breaking news aujourd'hui.",
            "telegram_context_block": (
                _format_telegram_messages_block(telegram_messages)
                if telegram_messages
                else "Journal Telegram indisponible pour cette date — base-toi uniquement sur les données ci-dessus."
            ),
        }

    if fmt == "REACTION":
        rows = db.get_events_for_day(day_start_utc, day_end_utc)
        events = [_row_to_event(r) for r in rows if r["impact"] == "High" and r["actual"]]
        return {"events_block": _format_events_block(events)} if events else None

    if fmt == "POURQUOI":
        rows = db.get_events_for_day(day_start_utc, day_end_utc)
        events = [_row_to_event(r) for r in rows if r["actual"]]
        return {"events_block": _format_events_block(events)} if events else None

    if fmt == "PEDAGO":
        concept = concept_override
        if not concept:
            rows = db.get_events_for_day(day_start_utc, day_end_utc)
            events = [_row_to_event(r) for r in rows]
            if not events:
                return None
            concept = _pick_top_event_title(events)
        return {"concept": concept}

    if fmt == "FACTCHECK":
        rows = db.get_news_for_day(day_start_utc, day_end_utc)
        return {"headline_block": _format_headlines_block(rows)} if rows else None

    if fmt == "SEMAINE":
        week_start_utc = day_start_utc
        week_end_utc = day_start_utc + timedelta(days=7)
        rows = db.get_events_for_day(week_start_utc, week_end_utc)
        events = [_row_to_event(r) for r in rows]
        return {"events_block": _format_events_block(events)} if events else None

    raise ValueError(f"Format inconnu: {fmt}")


def get_source_event(fmt: str, target_date: date) -> dict | None:
    """Événement 'principal' (impact fort, sinon le plus tôt) pour REACTION/POURQUOI/
    DEBRIEF ce jour-là — utilisé uniquement par video_renderer.py (rendu vidéo
    local, voir --render) pour générer un graphique à partir de vraies données.
    None pour les autres formats ou s'il n'y a rien d'exploitable. N'affecte pas
    generate() : le job automatique du soir n'appelle jamais cette fonction."""
    if fmt not in ("REACTION", "POURQUOI", "DEBRIEF"):
        return None
    day_start_utc, day_end_utc = db.local_day_bounds_utc(target_date)
    rows = db.get_events_for_day(day_start_utc, day_end_utc)
    events = [_row_to_event(r) for r in rows if r["actual"] and (fmt != "REACTION" or r["impact"] == "High")]
    if not events:
        return None
    return sorted(events, key=lambda e: (e["impact"] != "High", e["event_dt_utc"]))[0]


# --- calibrage de durée (2,5 mots/s en français) ----------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


def _word_range_for_format(fmt: str) -> tuple[int, int]:
    """Fourchette de mots totale attendue (hook+corps+chute+CTA). Un format à
    cheval sur 2 paliers (POURQUOI, 45-60s) prend l'union des 2 fourchettes."""
    sec_min, sec_max = config.VIDEO_FORMAT_DURATIONS[fmt]
    return config.VIDEO_DURATION_WORD_RANGES[sec_min][0], config.VIDEO_DURATION_WORD_RANGES[sec_max][1]


def _compute_duration(fmt: str, hook: str, corps: list[dict], chute: str, cta: str) -> tuple[int, int, list[str]]:
    """Retourne (word_count, estimated_seconds, warnings) sur le texte réellement
    dit à l'oral (hook + oral de chaque bloc + chute + CTA — le CTA est parlé,
    donc compté)."""
    warnings: list[str] = []
    total_words = _word_count(hook) + _word_count(chute) + _word_count(cta)
    total_words += sum(_word_count(bloc.get("oral", "")) for bloc in corps)

    estimated_seconds = round(total_words / config.VIDEO_WORDS_PER_SECOND_FR)

    word_min, word_max = _word_range_for_format(fmt)
    if total_words < word_min:
        warnings.append(f"Script trop court : {total_words} mots (fourchette attendue {word_min}-{word_max}).")
    elif total_words > word_max:
        warnings.append(f"Script trop long : {total_words} mots (fourchette attendue {word_min}-{word_max}).")

    hook_words = _word_count(hook)
    if hook_words > 6:
        warnings.append(f"Hook trop long : {hook_words} mots (visez ~5 mots pour tenir en 2 secondes).")

    return total_words, estimated_seconds, warnings


# --- appel LLM avec retry-une-fois (propre à ce module) ---------------------------

def _call_llm_with_retry(user_prompt: str) -> dict | None:
    system_prompt = _system_prompt()
    for attempt in (1, 2):
        result = ai_analyzer.call_gemini(user_prompt, _SCRIPT_SCHEMA, _MAX_LLM_TOKENS, system_prompt)
        if result:
            return result
        logger.warning("Appel LLM échoué (tentative %d/2, script vidéo).", attempt)
    logger.error("Script vidéo non généré après 2 tentatives LLM.")
    return None


# --- construction du prompt, assemblage, rendu ------------------------------------

def _build_prompt(fmt: str, data: dict, target_date: date, extra_notes: str | None = None) -> str:
    template = _load_template(f"{fmt.lower()}.txt")
    word_min, word_max = _word_range_for_format(fmt)
    sec_min, sec_max = config.VIDEO_FORMAT_DURATIONS[fmt]
    duration_label = f"{sec_min} secondes" if sec_min == sec_max else f"{sec_min} à {sec_max} secondes"

    # Le budget de mots donné au LLM exclut le CTA (fixe, injecté après coup) pour
    # que le total final (LLM + CTA) retombe dans la vraie fourchette du format.
    cta_words = _word_count(config.VIDEO_CTA_TEXT)
    llm_word_min = max(10, word_min - cta_words)
    llm_word_max = max(llm_word_min + 5, word_max - cta_words)

    # Remarque libre de l'utilisateur (ex. via le raccourci bureau) à prendre en
    # compte pour CE script précis — vide si rien fourni, jamais un texte éditorial
    # codé en dur (voir video_templates/*.txt pour où ça atterrit dans le prompt).
    extra_notes_block = f"Remarque de l'utilisateur à prendre en compte pour ce script : {extra_notes.strip()}" if extra_notes and extra_notes.strip() else ""

    context = {
        "date_str": target_date.strftime("%d/%m/%Y"),
        "pairs": ", ".join(config.TRADING_PAIRS),
        "word_min": llm_word_min,
        "word_max": llm_word_max,
        "duration_label": duration_label,
        "extra_notes_block": extra_notes_block,
        **data,
    }
    return template.format(**context)


def _trim_debrief_to_duration_cap(corps: list[dict], hook: str, chute: str, cta: str) -> list[dict]:
    """Le LLM dépasse parfois largement son budget de mots malgré la consigne
    (constaté en conditions réelles : jusqu'à +58 mots sur une fourchette à
    155) — un simple mot dans le prompt ne suffit pas à garantir le plafond
    dur de 65s (1min05) demandé par l'utilisateur. Coupe donc, après coup, les
    blocs "BREAKING" les moins prioritaires (les derniers de la section — voir
    debrief.txt règle 1 : "du plus important au plus secondaire") un par un
    jusqu'à repasser sous le plafond, sans jamais toucher au bloc "RECAP"
    (toujours le dernier bloc) : les news économiques du jour ne doivent
    jamais disparaître, contrairement aux breaking news secondaires.

    Va jusqu'à supprimer TOUS les blocs "BREAKING" si nécessaire (constaté :
    un unique bloc BREAKING trop long peut à lui seul dépasser le plafond —
    le garder coûte que coûte a déjà produit une vidéo à 65,46s, 0,46s
    au-dessus du plafond demandé) : le plafond dur prime sur la présence de
    breaking news, jamais sur la présence du récap éco."""
    max_seconds = config.VIDEO_FORMAT_DURATIONS["DEBRIEF"][1]
    trimmed = list(corps)
    while True:
        _, estimated_seconds, _ = _compute_duration("DEBRIEF", hook, trimmed, chute, cta)
        if estimated_seconds <= max_seconds:
            break
        breaking_indices = [i for i, c in enumerate(trimmed) if c["section"] == "BREAKING"]
        if not breaking_indices:
            break  # plus rien à couper sans toucher au RECAP
        del trimmed[breaking_indices[-1]]
    return trimmed


def _assemble_script(fmt: str, target_date: date, llm_result: dict) -> dict:
    default_keyword = config.STOCK_FOOTAGE_THEME_BY_FORMAT.get(fmt, "finance")
    raw_corps = llm_result.get("corps", [])
    corps = [
        {
            "oral": bloc.get("oral", ""),
            "ecran": bloc.get("ecran", ""),
            "visuel": bloc.get("visuel", ""),
            # Mot-clé (anglais, court) pour chercher un fond vidéo pertinent au
            # contenu de CE bloc précis (voir video_renderer._background_clip) —
            # repli sur le thème générique du format si le LLM ne l'a pas fourni.
            "visual_keyword": (bloc.get("visual_keyword") or "").strip() or default_keyword,
        }
        for bloc in raw_corps
    ]

    hook = llm_result.get("hook", "")
    chute = llm_result.get("chute", "")
    cta = config.VIDEO_CTA_TEXT  # jamais généré par le LLM, toujours la même formulation

    if fmt == "DEBRIEF":
        # Garde-fou structurel (voir video_templates/debrief.txt) : impose
        # l'ordre BREAKING -> RECAP même si le LLM n'a pas respecté l'ordre
        # demandé — tri stable, donc la hiérarchisation du LLM à l'intérieur de
        # chaque section est préservée. Une section absente ou invalide retombe
        # sur "BREAKING" (jamais un bloc affiché à tort dans le récap final).
        for c, bloc in zip(corps, raw_corps):
            section = (bloc.get("section") or "").strip().upper()
            c["section"] = section if section in _DEBRIEF_SECTION_ORDER else "BREAKING"
        corps.sort(key=lambda c: _DEBRIEF_SECTION_ORDER[c["section"]])
        corps = _trim_debrief_to_duration_cap(corps, hook, chute, cta)

    corps = [{"bloc": i + 1, **c} for i, c in enumerate(corps)]

    word_count, estimated_seconds, warnings = _compute_duration(fmt, hook, corps, chute, cta)
    # DEBRIEF : le récap éco tient maintenant dans 1-2 blocs compacts en fin de
    # vidéo au lieu d'un bloc développé par événement — le total de blocs est
    # donc naturellement plus bas qu'avant (voir video_templates/debrief.txt).
    min_corps, max_corps = (2, 8) if fmt == "DEBRIEF" else (3, 5)
    if not min_corps <= len(corps) <= max_corps:
        warnings.append(f"Corps hors gabarit : {len(corps)} bloc(s) (attendu {min_corps} à {max_corps}).")

    legende = llm_result.get("legende", "").strip()
    disclaimer = config.VIDEO_DISCLAIMER  # jamais généré par le LLM non plus
    hashtags = [str(h).lstrip("#") for h in llm_result.get("hashtags", []) if h]

    return {
        "format": fmt,
        "date": target_date.isoformat(),
        "hook": hook,
        "corps": corps,
        "chute": chute,
        "cta": cta,
        "legende": legende,
        "disclaimer": disclaimer,
        "legende_complete": f"{legende}\n\n{disclaimer}" if legende else disclaimer,
        "hashtags": hashtags,
        "recommended_post_time": config.VIDEO_RECOMMENDED_POST_TIME.get(fmt, ""),
        "estimated_duration_seconds": estimated_seconds,
        "word_count": word_count,
        "warnings": warnings,
    }


_SECTION_MD_LABELS = {
    "BREAKING": f"🔴 {config.VIDEO_SECTION_LABEL_BREAKING}",
    "RECAP": f"🟡 {config.VIDEO_SECTION_LABEL_RECAP}",
}


def _render_markdown(script: dict) -> str:
    date_label = datetime.fromisoformat(script["date"]).strftime("%d/%m/%Y")
    lines = [
        f"# {script['format']} — {date_label}",
        "",
        "## 🎬 HOOK (2s max)",
        f"> {script['hook']}",
        "",
        "## 📋 CORPS",
    ]
    # Sous-titres de section pour DEBRIEF uniquement (seul format dont les blocs
    # portent un "section" — voir _assemble_script) : rend visible à la relecture
    # la même séparation événements/breaking news que la vidéo rendue.
    current_section = None
    for bloc in script["corps"]:
        section = bloc.get("section")
        if section and section != current_section:
            lines += ["", f"### {_SECTION_MD_LABELS.get(section, section)}"]
            current_section = section
        lines += [
            "",
            f"**Bloc {bloc['bloc']}**",
            f"- 🗣️ Oral : {bloc['oral']}",
            f"- 📱 Écran : {bloc['ecran']}",
            f"- 🎥 Visuel : {bloc['visuel']}",
        ]
    lines += [
        "",
        "## 🎯 CHUTE",
        f"> {script['chute']}",
        "",
        "## 📲 CTA",
        f"> {script['cta']}",
        "",
        "---",
        "",
        "## 📊 MÉTADONNÉES",
        "- **Légende** :",
        "",
        f"  {script['legende']}",
        "",
        f"  {script['disclaimer']}",
        "",
        "- **Hashtags** : " + " ".join(f"#{h}" for h in script["hashtags"]),
        f"- **Publication recommandée** : {script['recommended_post_time']}",
        f"- **Durée estimée** : ~{script['estimated_duration_seconds']}s ({script['word_count']} mots)",
    ]
    if script["warnings"]:
        lines += ["", "## ⚠️ Avertissements"]
        lines += [f"- {w}" for w in script["warnings"]]
    return "\n".join(lines)


def _render_json(script: dict) -> str:
    return json.dumps(script, ensure_ascii=False, indent=2)


def _output_dir(target_date: date) -> str:
    return os.path.join(config.VIDEO_OUTPUT_DIR, target_date.isoformat())


def _write_files(script: dict) -> tuple[str, str]:
    out_dir = _output_dir(date.fromisoformat(script["date"]))
    os.makedirs(out_dir, exist_ok=True)
    base = script["format"].lower()
    md_path = os.path.join(out_dir, f"{base}.md")
    json_path = os.path.join(out_dir, f"{base}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_markdown(script))
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(_render_json(script))
    return md_path, json_path


# --- API publique -------------------------------------------------------------

def generate(
    fmt: str,
    target_date: date,
    dry_run: bool = False,
    concept_override: str | None = None,
    extra_notes: str | None = None,
) -> dict | None:
    """Génère un script pour un format donné. Renvoie None (sans rien écrire) si
    aucune donnée exploitable ou si l'appel LLM échoue après retry — l'appelant
    doit alors logger et passer au format suivant plutôt que de traiter ça comme
    une erreur bloquante. `extra_notes` : remarque libre à prendre en compte pour
    ce script précis (ex. via le raccourci bureau) — n'entre jamais en jeu pour
    décider si des données sont exploitables, seulement pour orienter le texte."""
    fmt = fmt.upper()
    if fmt not in FORMATS:
        raise ValueError(f"Format inconnu: {fmt} (attendu: {', '.join(FORMATS)})")

    data = _gather_data(fmt, target_date, concept_override)
    if data is None:
        logger.info("Aucune donnée exploitable pour %s le %s, script non généré.", fmt, target_date)
        return None

    user_prompt = _build_prompt(fmt, data, target_date, extra_notes)
    llm_result = _call_llm_with_retry(user_prompt)
    if llm_result is None:
        return None

    script = _assemble_script(fmt, target_date, llm_result)

    if dry_run:
        logger.info("(--dry-run) Script %s généré, non écrit sur disque.", fmt)
        return script

    md_path, _ = _write_files(script)
    logger.info("Script %s écrit : %s", fmt, md_path)
    return script


def generate_daily_batch(target_date: date) -> None:
    """Utilisé par le job planifié : le débrief vidéo du soir (DEBRIEF), un seul
    format, après le débrief texte. Les autres formats restent disponibles à la
    demande via la CLI mais ne sont plus générés automatiquement."""
    if generate("DEBRIEF", target_date) is None:
        logger.warning("Débrief vidéo quotidien non généré (données manquantes ou échec LLM).")


# --- CLI ------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Génère un script vidéo court à partir des données de l'agent.")
    parser.add_argument("--format", required=True, type=str.upper, choices=FORMATS + ["ALL"], help="Format à générer.")
    parser.add_argument("--date", default=None, help="Date cible YYYY-MM-DD (défaut : aujourd'hui).")
    parser.add_argument("--dry-run", action="store_true", help="Affiche le script sans écrire de fichier.")
    parser.add_argument("--concept", default=None, help="Concept à expliquer pour PEDAGO (sinon auto-détecté).")
    parser.add_argument("--notes", default=None, help="Remarque libre à prendre en compte pour ce script (facultatif).")
    parser.add_argument(
        "--render", action="store_true",
        help="Génère aussi la vidéo (voix + visuel), en local. Nécessite pip install -r requirements-video.txt.",
    )
    args = parser.parse_args()

    if args.render and args.dry_run:
        print("❌ --render et --dry-run sont incompatibles (--dry-run n'écrit aucun fichier).")
        sys.exit(1)

    try:
        target_date = date.fromisoformat(args.date) if args.date else datetime.now(config.TIMEZONE).date()
    except ValueError:
        print(f"❌ Date invalide : {args.date!r} (format attendu : YYYY-MM-DD)")
        sys.exit(1)

    formats = FORMATS if args.format == "ALL" else [args.format]
    any_generated = False

    for fmt in formats:
        print(f"\n=== {fmt} — {target_date.isoformat()} ===")
        script = generate(fmt, target_date, dry_run=args.dry_run, concept_override=args.concept, extra_notes=args.notes)
        if script is None:
            print(f"⏭️  Pas de données exploitables (ou échec LLM) pour {fmt}, ignoré.")
            continue
        any_generated = True
        if args.dry_run:
            print(_render_markdown(script))
        else:
            print(f"✅ Écrit dans {_output_dir(target_date)}/{fmt.lower()}.md (+ .json)")
        for w in script["warnings"]:
            print(f"⚠️  {w}")

        if args.render:
            try:
                import video_renderer
            except ImportError:
                print("❌ Dépendances vidéo manquantes : pip install -r requirements-video.txt")
                sys.exit(1)
            print(f"🎬 Rendu vidéo en cours pour {fmt} (voix + visuel, ça peut prendre une minute)...")
            mp4_path = video_renderer.render(script, os.path.join(_output_dir(target_date), f"{fmt.lower()}.mp4"))
            print(f"✅ Vidéo écrite : {mp4_path}" if mp4_path else f"❌ Échec du rendu vidéo pour {fmt} (voir agent.log).")

    if not any_generated:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _cli()
