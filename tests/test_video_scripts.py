"""
Couvre les 4 axes demandés : calibrage de durée, structure de sortie complète,
présence du CTA/disclaimer, comportement sur données manquantes — plus le retry
LLM et --dry-run. Aucun appel réseau réel : ai_analyzer.call_gemini est mocké.
"""
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import config
import db
import video_scripts


def _words(n: int) -> str:
    return " ".join(f"mot{i}" for i in range(n))


def _fake_llm_result(n_corps: int = 4) -> dict:
    return {
        "hook": _words(5),
        "corps": [
            {"oral": _words(8), "ecran": "texte court", "visuel": "graphique X"}
            for _ in range(n_corps)
        ],
        "chute": _words(6),
        "legende": "Légende de test pour la vidéo.",
        "hashtags": ["eco", "trading", "news"],
    }


def _insert_reaction_event(target_date: date, event_key: str = "test_event_1") -> None:
    event_dt = datetime.combine(target_date, time(14, 30), tzinfo=config.TIMEZONE).astimezone(timezone.utc)
    db.upsert_event(
        {
            "event_key": event_key,
            "title": "Non-Farm Payrolls (NFP)",
            "currency": "USD",
            "impact": "High",
            "event_dt_utc": event_dt.isoformat(),
            "forecast": "180K",
            "previous": "227K",
            "actual": "206K",
        }
    )


# --- calibrage de durée -----------------------------------------------------------

def test_word_range_for_format_single_tier():
    assert video_scripts._word_range_for_format("REACTION") == (105, 125)
    assert video_scripts._word_range_for_format("PEDAGO") == (70, 85)
    assert video_scripts._word_range_for_format("SEMAINE") == (140, 160)
    assert video_scripts._word_range_for_format("FACTCHECK") == (105, 125)


def test_word_range_for_format_union_for_two_tiers():
    assert video_scripts._word_range_for_format("POURQUOI") == (105, 160)
    assert video_scripts._word_range_for_format("DEBRIEF") == (172, 243)


def test_compute_duration_within_range_no_warning():
    cta = config.VIDEO_CTA_TEXT
    cta_words = len(cta.split())
    hook, chute, target_total = _words(5), _words(10), 115
    corps = [{"oral": _words(target_total - 5 - 10 - cta_words), "ecran": "x", "visuel": "y"}]

    word_count, estimated_seconds, warnings = video_scripts._compute_duration("REACTION", hook, corps, chute, cta)

    assert word_count == target_total
    assert warnings == []
    assert estimated_seconds == round(target_total / config.VIDEO_WORDS_PER_SECOND_FR)


def test_compute_duration_too_short_warns():
    cta = config.VIDEO_CTA_TEXT
    corps = [{"oral": _words(3), "ecran": "x", "visuel": "y"}]
    _, _, warnings = video_scripts._compute_duration("PEDAGO", _words(2), corps, _words(2), cta)
    assert any("trop court" in w for w in warnings)


def test_compute_duration_too_long_warns():
    cta = config.VIDEO_CTA_TEXT
    corps = [{"oral": _words(80), "ecran": "x", "visuel": "y"} for _ in range(3)]
    _, _, warnings = video_scripts._compute_duration("PEDAGO", _words(5), corps, _words(5), cta)
    assert any("trop long" in w for w in warnings)


def test_compute_duration_hook_too_long_warns():
    cta = config.VIDEO_CTA_TEXT
    corps = [{"oral": _words(50), "ecran": "x", "visuel": "y"}]
    _, _, warnings = video_scripts._compute_duration("REACTION", _words(10), corps, _words(5), cta)
    assert any("Hook trop long" in w for w in warnings)


# --- structure de sortie complète --------------------------------------------------

def test_render_markdown_contains_all_sections():
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), _fake_llm_result(n_corps=4))
    md = video_scripts._render_markdown(script)

    for marker in ("HOOK", "CORPS", "CHUTE", "CTA", "MÉTADONNÉES", "Bloc 1", "Bloc 4"):
        assert marker in md
    assert script["cta"] in md
    assert script["disclaimer"] in md
    assert "#eco" in md


def test_render_json_has_all_expected_keys():
    script = video_scripts._assemble_script("PEDAGO", date(2026, 7, 26), _fake_llm_result())
    rendered = json.loads(video_scripts._render_json(script))
    expected_keys = {
        "format", "date", "hook", "corps", "chute", "cta", "legende", "disclaimer",
        "legende_complete", "hashtags", "recommended_post_time",
        "estimated_duration_seconds", "word_count", "warnings",
    }
    assert expected_keys <= rendered.keys()


def test_assemble_script_corps_count_warning():
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), _fake_llm_result(n_corps=1))
    assert any("hors gabarit" in w for w in script["warnings"])


def test_assemble_script_debrief_allows_single_corps_block():
    """DEBRIEF ne couvre plus que les breaking news, regroupées par sujet (voir
    debrief.txt) : un jour calme avec un seul vrai sujet donne légitimement un
    seul bloc — pas de "hors gabarit" pour ça."""
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), _fake_llm_result(n_corps=1))
    assert not any("hors gabarit" in w for w in script["warnings"])


# --- visual_keyword (fond vidéo pertinent au contenu de chaque bloc) ----------------

def test_assemble_script_carries_llm_visual_keyword_through():
    llm_result = _fake_llm_result(n_corps=2)
    llm_result["corps"][0]["visual_keyword"] = "central bank building"
    llm_result["corps"][1]["visual_keyword"] = "oil rig ocean"
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), llm_result)
    assert script["corps"][0]["visual_keyword"] == "central bank building"
    assert script["corps"][1]["visual_keyword"] == "oil rig ocean"


def test_assemble_script_falls_back_to_format_theme_without_llm_keyword():
    llm_result = _fake_llm_result(n_corps=1)  # pas de "visual_keyword" fourni
    script = video_scripts._assemble_script("PEDAGO", date(2026, 7, 26), llm_result)
    assert script["corps"][0]["visual_keyword"] == config.STOCK_FOOTAGE_THEME_BY_FORMAT["PEDAGO"]


# --- CTA et disclaimer toujours présents -------------------------------------------

def test_assemble_script_injects_cta_and_disclaimer_regardless_of_llm_output():
    llm_result = _fake_llm_result()
    llm_result["legende"] = "Une légende qui ne mentionne ni CTA ni disclaimer."
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), llm_result)

    assert script["cta"] == config.VIDEO_CTA_TEXT
    assert script["disclaimer"] == config.VIDEO_DISCLAIMER
    assert config.VIDEO_DISCLAIMER in script["legende_complete"]


# --- DEBRIEF : breaking news uniquement (le calendrier économique n'y figure
# plus du tout, voir video_templates/debrief.txt) -----------------------------------

def test_gather_data_debrief_works_with_news(temp_db):
    # date.today() et non une date fixe : db.mark_sent_news() horodate toujours
    # sent_at avec l'heure réelle (voir db.py), donc la fenêtre interrogée doit
    # couvrir "maintenant", pas un jour arbitraire figé dans le passé.
    target_date = date.today()
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")

    data = video_scripts._gather_data("DEBRIEF", target_date)

    assert data is not None
    assert "Déclaration surprise de la Fed" in data["headlines_block"]
    assert "events_block" not in data
    assert "pending_events_block" not in data


def test_gather_data_debrief_returns_none_without_any_news(temp_db):
    """Un calendrier économique chargé mais aucune breaking news : plus rien à
    couvrir pour ce format (voir debrief.txt — le calendrier n'y figure plus)."""
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)
    data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is None


# --- ancrage sur le texte déjà envoyé sur Telegram (voir message_log.py) -----------

def test_format_telegram_messages_block_includes_time_and_raw_text():
    messages = [
        {"sent_at": "2026-07-26T13:30:00+00:00", "chat_target": "perso", "raw_text": "🔴 *NFP* réel 206K"},
    ]
    block = video_scripts._format_telegram_messages_block(messages)
    assert "réel 206K" in block
    assert "15h30" in block  # UTC+2 l'été, voir config.TIMEZONE


def test_format_telegram_messages_block_keeps_chronological_order():
    messages = [
        {"sent_at": "2026-07-26T08:00:00+00:00", "chat_target": "perso", "raw_text": "premier message"},
        {"sent_at": "2026-07-26T09:00:00+00:00", "chat_target": "perso", "raw_text": "second message"},
    ]
    block = video_scripts._format_telegram_messages_block(messages)
    assert block.index("premier message") < block.index("second message")


def test_format_telegram_messages_block_caps_at_max_events_in_prompt():
    messages = [
        {"sent_at": f"2026-07-26T08:{i:02d}:00+00:00", "chat_target": "perso", "raw_text": f"message {i}"}
        for i in range(video_scripts._MAX_EVENTS_IN_PROMPT + 5)
    ]
    block = video_scripts._format_telegram_messages_block(messages)
    assert block.count("--- Message envoyé") == video_scripts._MAX_EVENTS_IN_PROMPT


def test_gather_data_debrief_includes_real_telegram_messages(temp_db):
    # date.today() et non une date fixe : voir commentaire de
    # test_gather_data_debrief_works_with_news sur db.mark_sent_news().
    target_date = date.today()
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")
    fake_messages = [
        {"sent_at": target_date.isoformat() + "T14:35:00+00:00", "chat_target": "perso", "raw_text": "🚨 *Breaking News*\nDéclaration surprise de la Fed, biais haussier sur le dollar"},
    ]
    with patch("video_scripts.message_log.get_messages_for_day", return_value=fake_messages):
        data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is not None
    assert "biais haussier sur le dollar" in data["telegram_context_block"]


def test_gather_data_debrief_filters_out_non_breaking_telegram_messages(temp_db):
    """Le journal contient aussi les résumés/débriefs (calendrier économique) :
    seuls les messages "🚨 Breaking News" doivent atterrir dans le prompt
    DEBRIEF, sinon le calendrier reviendrait malgré son retrait de headlines_block."""
    target_date = date.today()
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")
    fake_messages = [
        {"sent_at": target_date.isoformat() + "T08:00:00+00:00", "chat_target": "perso", "raw_text": "☀️ *Résumé du jour*\nADP 44K vs 68K attendus"},
        # Constaté en conditions réelles : le débrief du soir mentionne lui-même
        # "🚨 X breaking news" dans son propre corps de texte — un simple "🚨 in
        # raw_text" laisserait donc passer tout le calendrier qu'il liste aussi.
        {"sent_at": target_date.isoformat() + "T23:30:00+00:00", "chat_target": "perso", "raw_text": "🌙 *Débrief du soir*\n📊 ADP 44K vs 68K attendus\n🚨 3 breaking news aujourd'hui"},
        {"sent_at": target_date.isoformat() + "T14:35:00+00:00", "chat_target": "perso", "raw_text": "🚨 *Breaking News*\nDéclaration surprise de la Fed"},
    ]
    with patch("video_scripts.message_log.get_messages_for_day", return_value=fake_messages):
        data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is not None
    assert "ADP 44K" not in data["telegram_context_block"]
    assert "Déclaration surprise de la Fed" in data["telegram_context_block"]


def test_gather_data_debrief_telegram_context_fallback_when_log_empty(temp_db):
    """Le journal Turso peut être vide/indisponible (voir message_log.py, best-effort) :
    le DEBRIEF doit rester exploitable, juste sans ce contexte supplémentaire."""
    target_date = date.today()
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")
    with patch("video_scripts.message_log.get_messages_for_day", return_value=[]):
        data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is not None
    assert "indisponible" in data["telegram_context_block"]


# --- extra_notes (remarque libre, ex. via le raccourci bureau) ---------------------

def test_build_prompt_includes_extra_notes_when_provided():
    prompt = video_scripts._build_prompt(
        "DEBRIEF",
        {"headlines_block": "y", "telegram_context_block": "w"},
        date(2026, 7, 26),
        extra_notes="Insiste sur le pétrole",
    )
    assert "Insiste sur le pétrole" in prompt


def test_build_prompt_no_extra_notes_mention_when_none_or_blank():
    data = {"headlines_block": "y", "telegram_context_block": "w"}
    prompt_none = video_scripts._build_prompt("DEBRIEF", data, date(2026, 7, 26), extra_notes=None)
    prompt_blank = video_scripts._build_prompt("DEBRIEF", data, date(2026, 7, 26), extra_notes="   ")
    assert "Remarque de l'utilisateur" not in prompt_none
    assert "Remarque de l'utilisateur" not in prompt_blank


# --- données manquantes -------------------------------------------------------------

@pytest.mark.parametrize("fmt", video_scripts.FORMATS)
def test_gather_data_returns_none_when_db_empty(temp_db, fmt):
    assert video_scripts._gather_data(fmt, date(2026, 7, 26)) is None


def test_generate_returns_none_and_skips_llm_when_no_data(temp_db, temp_video_output):
    with patch("video_scripts.ai_analyzer.call_gemini") as mock_call:
        result = video_scripts.generate("REACTION", date(2026, 7, 26))

    assert result is None
    mock_call.assert_not_called()
    assert not Path(config.VIDEO_OUTPUT_DIR).exists()


# --- get_source_event (utilisé par video_renderer.py, rendu --render) --------------

@pytest.mark.parametrize("fmt", ["PEDAGO", "FACTCHECK", "SEMAINE"])
def test_get_source_event_none_for_non_reaction_pourquoi_formats(temp_db, fmt):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)
    assert video_scripts.get_source_event(fmt, target_date) is None


def test_get_source_event_none_when_no_actual(temp_db):
    target_date = date(2026, 7, 26)
    db.upsert_event(
        {
            "event_key": "no_actual", "title": "Sans résultat", "currency": "USD", "impact": "High",
            "event_dt_utc": datetime.combine(target_date, time(10, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc).isoformat(),
            "forecast": "1.0%", "previous": "0.9%", "actual": None,
        }
    )
    assert video_scripts.get_source_event("REACTION", target_date) is None


def test_get_source_event_prefers_high_impact(temp_db):
    target_date = date(2026, 7, 26)
    medium_dt_utc = datetime.combine(target_date, time(9, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc)
    high_dt_utc = datetime.combine(target_date, time(12, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc)
    db.upsert_event(
        {
            "event_key": "medium_first", "title": "Medium event", "currency": "USD", "impact": "Medium",
            "event_dt_utc": medium_dt_utc.isoformat(), "forecast": "1K", "previous": "2K", "actual": "3K",
        }
    )
    db.upsert_event(
        {
            "event_key": "high_later", "title": "High event", "currency": "USD", "impact": "High",
            "event_dt_utc": high_dt_utc.isoformat(), "forecast": "180K", "previous": "227K", "actual": "206K",
        }
    )
    result = video_scripts.get_source_event("REACTION", target_date)
    assert result is not None
    assert result["title"] == "High event"


def test_get_source_event_pourquoi_accepts_medium_impact(temp_db):
    target_date = date(2026, 7, 26)
    db.upsert_event(
        {
            "event_key": "medium_only", "title": "Medium only", "currency": "EUR", "impact": "Medium",
            "event_dt_utc": datetime.combine(target_date, time(9, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc).isoformat(),
            "forecast": "1.0%", "previous": "0.9%", "actual": "1.2%",
        }
    )
    assert video_scripts.get_source_event("POURQUOI", target_date) is not None
    assert video_scripts.get_source_event("REACTION", target_date) is None


def test_get_source_event_debrief_accepts_medium_impact(temp_db):
    """DEBRIEF couvre toute la journée : même règle que POURQUOI (High + Medium),
    utilisé pour le graphique du hook si l'event a des données numériques."""
    target_date = date(2026, 7, 26)
    db.upsert_event(
        {
            "event_key": "medium_only", "title": "Medium only", "currency": "EUR", "impact": "Medium",
            "event_dt_utc": datetime.combine(target_date, time(9, 0), tzinfo=config.TIMEZONE).astimezone(timezone.utc).isoformat(),
            "forecast": "1.0%", "previous": "0.9%", "actual": "1.2%",
        }
    )
    assert video_scripts.get_source_event("DEBRIEF", target_date) is not None


def test_pedago_falls_back_to_top_event_when_no_concept_given(temp_db):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)
    data = video_scripts._gather_data("PEDAGO", target_date)
    assert data == {"concept": "Non-Farm Payrolls (NFP)"}


# --- retry LLM (une fois, puis abandon propre) --------------------------------------

def test_generate_retries_once_then_gives_up(temp_db, temp_video_output):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)

    with patch("video_scripts.ai_analyzer.call_gemini", return_value=None) as mock_call:
        result = video_scripts.generate("REACTION", target_date)

    assert result is None
    assert mock_call.call_count == 2


def test_generate_succeeds_on_second_attempt(temp_db, temp_video_output):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)
    llm_result = _fake_llm_result()

    with patch("video_scripts.ai_analyzer.call_gemini", side_effect=[None, llm_result]) as mock_call:
        result = video_scripts.generate("REACTION", target_date)

    assert result is not None
    assert mock_call.call_count == 2
    assert result["cta"] == config.VIDEO_CTA_TEXT


# --- --dry-run -----------------------------------------------------------------------

def test_generate_dry_run_does_not_write_file(temp_db, temp_video_output):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)

    with patch("video_scripts.ai_analyzer.call_gemini", return_value=_fake_llm_result()):
        result = video_scripts.generate("REACTION", target_date, dry_run=True)

    assert result is not None
    assert not Path(video_scripts._output_dir(target_date)).exists()


def test_generate_writes_md_and_json_when_not_dry_run(temp_db, temp_video_output):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)

    with patch("video_scripts.ai_analyzer.call_gemini", return_value=_fake_llm_result()):
        result = video_scripts.generate("REACTION", target_date, dry_run=False)

    assert result is not None
    out_dir = Path(video_scripts._output_dir(target_date))
    md_path, json_path = out_dir / "reaction.md", out_dir / "reaction.json"
    assert md_path.exists()
    assert json_path.exists()
    assert config.VIDEO_CTA_TEXT in md_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["format"] == "REACTION"
