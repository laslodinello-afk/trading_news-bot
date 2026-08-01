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


def test_assemble_script_debrief_allows_more_corps_blocks():
    """DEBRIEF couvre plus de terrain (événements + breaking news) : 3 blocs est
    hors gabarit pour DEBRIEF (4 à 7) alors que ça passerait pour les autres formats."""
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), _fake_llm_result(n_corps=3))
    assert any("hors gabarit" in w for w in script["warnings"])
    script_ok = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), _fake_llm_result(n_corps=5))
    assert not any("hors gabarit" in w for w in script_ok["warnings"])


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


# --- DEBRIEF : structure en 2 sections (événements publiés puis breaking news) -----
# Garde-fou côté code (voir _assemble_script) : même si le LLM n'a pas respecté
# l'ordre demandé par video_templates/debrief.txt, tous les blocs "EVENEMENT" se
# retrouvent avant tous les blocs "BREAKING" une fois le script assemblé.

def _corps_block(section=None, oral="texte du bloc"):
    block = {"oral": oral, "ecran": "texte court", "visuel": "visuel"}
    if section is not None:
        block["section"] = section
    return block


def test_assemble_script_debrief_sorts_evenement_before_breaking():
    llm_result = {
        "hook": _words(5),
        "corps": [
            _corps_block("BREAKING"),
            _corps_block("EVENEMENT"),
            _corps_block("BREAKING"),
            _corps_block("EVENEMENT"),
        ],
        "chute": _words(6),
        "legende": "Légende de test.",
        "hashtags": ["eco"],
    }
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), llm_result)
    assert [b["section"] for b in script["corps"]] == ["EVENEMENT", "EVENEMENT", "BREAKING", "BREAKING"]
    assert [b["bloc"] for b in script["corps"]] == [1, 2, 3, 4]


def test_assemble_script_debrief_preserves_relative_order_within_each_section():
    """Tri stable : la hiérarchisation du LLM à l'intérieur d'une section (censée
    aller du plus important au moins important) n'est pas perturbée par le tri."""
    llm_result = {
        "hook": _words(5),
        "corps": [
            _corps_block("EVENEMENT", oral="premier evenement"),
            _corps_block("EVENEMENT", oral="second evenement"),
            _corps_block("BREAKING", oral="premier breaking"),
        ],
        "chute": _words(6),
        "legende": "Légende de test.",
        "hashtags": ["eco"],
    }
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), llm_result)
    assert [b["oral"] for b in script["corps"]] == ["premier evenement", "second evenement", "premier breaking"]


def test_assemble_script_debrief_defaults_missing_or_invalid_section_to_evenement():
    llm_result = {
        "hook": _words(5),
        "corps": [_corps_block(None), _corps_block(""), _corps_block("AUTRE_CHOSE")],
        "chute": _words(6),
        "legende": "Légende de test.",
        "hashtags": ["eco"],
    }
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), llm_result)
    assert all(b["section"] == "EVENEMENT" for b in script["corps"])


def test_assemble_script_non_debrief_never_carries_section_key():
    llm_result = _fake_llm_result(n_corps=2)
    llm_result["corps"][0]["section"] = "BREAKING"  # même si le LLM en glisse un par erreur
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), llm_result)
    assert all("section" not in bloc for bloc in script["corps"])


def test_render_markdown_debrief_groups_corps_under_section_headings():
    llm_result = {
        "hook": _words(5),
        "corps": [_corps_block("EVENEMENT", oral="evenement un"), _corps_block("BREAKING", oral="breaking un")],
        "chute": _words(6),
        "legende": "Légende de test.",
        "hashtags": ["eco"],
    }
    script = video_scripts._assemble_script("DEBRIEF", date(2026, 7, 26), llm_result)
    md = video_scripts._render_markdown(script)

    evenement_idx = md.index(config.VIDEO_SECTION_LABEL_EVENEMENT)
    breaking_idx = md.index(config.VIDEO_SECTION_LABEL_BREAKING)
    bloc1_idx = md.index("**Bloc 1**")
    bloc2_idx = md.index("**Bloc 2**")
    assert evenement_idx < bloc1_idx < breaking_idx < bloc2_idx


def test_render_markdown_non_debrief_has_no_section_headings():
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), _fake_llm_result(n_corps=3))
    md = video_scripts._render_markdown(script)
    assert config.VIDEO_SECTION_LABEL_EVENEMENT not in md
    assert config.VIDEO_SECTION_LABEL_BREAKING not in md


# --- CTA et disclaimer toujours présents -------------------------------------------

def test_assemble_script_injects_cta_and_disclaimer_regardless_of_llm_output():
    llm_result = _fake_llm_result()
    llm_result["legende"] = "Une légende qui ne mentionne ni CTA ni disclaimer."
    script = video_scripts._assemble_script("REACTION", date(2026, 7, 26), llm_result)

    assert script["cta"] == config.VIDEO_CTA_TEXT
    assert script["disclaimer"] == config.VIDEO_DISCLAIMER
    assert config.VIDEO_DISCLAIMER in script["legende_complete"]


# --- DEBRIEF : combine événements du jour + breaking news --------------------------

def test_gather_data_debrief_combines_events_and_news(temp_db):
    # date.today() et non une date fixe : db.mark_sent_news() horodate toujours
    # sent_at avec l'heure réelle (voir db.py), donc la fenêtre interrogée doit
    # couvrir "maintenant", pas un jour arbitraire figé dans le passé.
    target_date = date.today()
    _insert_reaction_event(target_date)
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")

    data = video_scripts._gather_data("DEBRIEF", target_date)

    assert data is not None
    assert "Non-Farm Payrolls" in data["events_block"]
    assert "Déclaration surprise de la Fed" in data["headlines_block"]


def test_gather_data_debrief_works_with_only_events(temp_db):
    target_date = date(2026, 7, 26)
    _insert_reaction_event(target_date)
    data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is not None
    assert "Aucune breaking news" in data["headlines_block"]


def test_gather_data_debrief_works_with_only_news(temp_db):
    target_date = date.today()  # voir commentaire dans le test précédent
    db.mark_sent_news("news_1", title="Déclaration surprise de la Fed", resume="Résumé test.")
    data = video_scripts._gather_data("DEBRIEF", target_date)
    assert data is not None
    assert "Aucun événement macro" in data["events_block"]


# --- extra_notes (remarque libre, ex. via le raccourci bureau) ---------------------

def test_build_prompt_includes_extra_notes_when_provided():
    prompt = video_scripts._build_prompt(
        "DEBRIEF", {"events_block": "x", "headlines_block": "y"}, date(2026, 7, 26),
        extra_notes="Insiste sur le pétrole",
    )
    assert "Insiste sur le pétrole" in prompt


def test_build_prompt_no_extra_notes_mention_when_none_or_blank():
    data = {"events_block": "x", "headlines_block": "y"}
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
