"""
Tests offline uniquement : aucun réseau (edge-tts mocké), aucun rendu vidéo réel
(pas de write_videofile ici — nécessite le vrai binaire ffmpeg, vérifié séparément
à la main, voir README). build_chart_image tourne pour de vrai : c'est juste du
matplotlib vers un PNG local, rapide et sans dépendance externe.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import config
import video_renderer


# --- _parse_numeric ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("180K", 180000.0),
        ("227K", 227000.0),
        ("3.00%", 3.0),
        ("-0.3%", -0.3),
        ("1.2M", 1_200_000.0),
        ("2,500", 2500.0),
        (None, None),
        ("", None),
        ("N/A", None),
        ("pas un chiffre", None),
    ],
)
def test_parse_numeric(raw, expected):
    result = video_renderer._parse_numeric(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# --- build_chart_image (matplotlib réel, local, rapide) -----------------------------

def test_build_chart_image_none_without_actual(tmp_path):
    event = {"title": "Test", "forecast": "180K", "previous": "227K", "actual": None}
    assert video_renderer.build_chart_image(event, str(tmp_path / "chart.png")) is None


def test_build_chart_image_none_with_only_one_numeric_value(tmp_path):
    """Un seul chiffre exploitable (actual) sans point de comparaison numérique :
    pas un graphique, on ne trace jamais une barre seule."""
    event = {"title": "Test", "forecast": None, "previous": None, "actual": "206K"}
    assert video_renderer.build_chart_image(event, str(tmp_path / "chart.png")) is None


def test_build_chart_image_creates_file_with_forecast_and_actual(tmp_path):
    out_path = str(tmp_path / "chart.png")
    event = {"title": "Non-Farm Payrolls", "forecast": "180K", "previous": "227K", "actual": "206K"}
    result = video_renderer.build_chart_image(event, out_path)
    assert result == out_path
    assert Path(out_path).stat().st_size > 0


def test_build_chart_image_falls_back_to_previous_when_no_forecast(tmp_path):
    """previous non numérique manquant ne doit jamais être tracé comme 0 — ici
    forecast est absent, donc on doit basculer sur previous plutôt que d'inventer
    un forecast à 0."""
    out_path = str(tmp_path / "chart.png")
    event = {"title": "Test", "forecast": None, "previous": "1.0%", "actual": "1.4%"}
    result = video_renderer.build_chart_image(event, out_path)
    assert result == out_path
    assert Path(out_path).stat().st_size > 0


# --- _chart_bounds (correctif : écart réel mais invisible sur un axe parti de 0) ---
# Bug constaté sur un vrai rendu (ISM Manufacturing PMI, 54.0 vs 55.6) : les deux
# barres étaient quasi identiques à l'œil. L'axe doit être zoomé sur la vraie
# fourchette des valeurs, jamais partir de 0 par défaut.

def test_chart_bounds_zooms_in_on_close_values_far_from_zero():
    zoom_min, zoom_max = video_renderer._chart_bounds([54.0, 55.6])
    assert zoom_min > 0  # ne repart pas de 0 comme un axe "classique"
    assert (zoom_max - zoom_min) < 54.0  # bien plus zoomé qu'un axe 0-55.6


def test_chart_bounds_never_returns_empty_range_for_identical_values():
    zoom_min, zoom_max = video_renderer._chart_bounds([2.4, 2.4])
    assert zoom_min < zoom_max


def test_chart_bounds_handles_negative_values():
    zoom_min, zoom_max = video_renderer._chart_bounds([-4.8, -1.2])
    assert zoom_min < -4.8
    assert zoom_max > -1.2


def test_build_chart_image_uses_original_raw_text_not_reformatted_number(tmp_path):
    """La barre doit afficher "180K" tel quel, pas le nombre reformaté après
    parsing (180000.0) qui perdrait l'unité d'origine (jamais reconstruire une
    donnée déjà fournie par la source)."""
    from matplotlib.axes import Axes

    captured_texts = []
    original_text = Axes.text

    def spy_text(self, x, y, s, *args, **kwargs):
        captured_texts.append(s)
        return original_text(self, x, y, s, *args, **kwargs)

    with patch.object(Axes, "text", spy_text):
        out_path = str(tmp_path / "chart.png")
        event = {"title": "NFP", "forecast": "180K", "previous": "227K", "actual": "142K"}
        result = video_renderer.build_chart_image(event, out_path)

    assert result == out_path
    assert captured_texts == ["180K", "142K"]


# --- _resolve_font_path --------------------------------------------------------------

def test_resolve_font_path_uses_configured_path_when_it_exists(tmp_path, monkeypatch):
    font_file = tmp_path / "custom.ttf"
    font_file.write_text("fake font")
    monkeypatch.setattr(config, "VIDEO_RENDER_FONT_PATH", str(font_file))
    assert video_renderer._resolve_font_path() == str(font_file)


def test_resolve_font_path_falls_back_when_configured_path_missing(tmp_path, monkeypatch):
    fallback = tmp_path / "fallback.ttf"
    fallback.write_text("fake font")
    monkeypatch.setattr(config, "VIDEO_RENDER_FONT_PATH", str(tmp_path / "does_not_exist.ttf"))
    monkeypatch.setattr(video_renderer, "_FALLBACK_FONTS", [str(tmp_path / "also_missing.ttf"), str(fallback)])
    assert video_renderer._resolve_font_path() == str(fallback)


def test_resolve_font_path_raises_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_RENDER_FONT_PATH", None)
    monkeypatch.setattr(video_renderer, "_FALLBACK_FONTS", [str(tmp_path / "missing1.ttf"), str(tmp_path / "missing2.ttf")])
    with pytest.raises(video_renderer.RenderError):
        video_renderer._resolve_font_path()


# --- _synthesize_with_retry (edge-tts mocké, aucun réseau) --------------------------
# Depuis le passage à Communicate.stream(), _synthesize renvoie (word_boundaries,
# sentence_boundaries) — voir _caption_chunks : en français, edge-tts ne fournit
# en pratique que sentence_boundaries (constaté empiriquement, aucune des 5 voix
# françaises testées ne renvoie de WordBoundary).

def test_synthesize_with_retry_succeeds_first_try(tmp_path):
    out_path = str(tmp_path / "out.mp3")
    fake_sentences = [{"text": "Bonjour.", "start": 0.0, "end": 0.4}]

    async def fake_synthesize(text, voice, path):
        Path(path).write_bytes(b"fake-audio-bytes")
        return [], fake_sentences

    with patch("video_renderer._synthesize", new=AsyncMock(side_effect=fake_synthesize)) as mock_synth:
        words, sentences = video_renderer._synthesize_with_retry("bonjour", "fr-FR-HenriNeural", out_path, attempts=3)

    assert Path(out_path).exists()
    assert words == []
    assert sentences == fake_sentences
    assert mock_synth.await_count == 1


def test_synthesize_with_retry_succeeds_on_second_attempt(tmp_path):
    out_path = str(tmp_path / "out.mp3")
    calls = {"n": 0}

    async def flaky_synthesize(text, voice, path):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("échec edge-tts simulé")
        Path(path).write_bytes(b"fake-audio-bytes")
        return [], [{"text": "Bonjour.", "start": 0.0, "end": 0.4}]

    with patch("video_renderer._synthesize", new=AsyncMock(side_effect=flaky_synthesize)):
        words, sentences = video_renderer._synthesize_with_retry("bonjour", "fr-FR-HenriNeural", out_path, attempts=3)

    assert Path(out_path).exists()
    assert calls["n"] == 2
    assert len(sentences) == 1


def test_synthesize_with_retry_raises_after_exhausting_attempts(tmp_path):
    out_path = str(tmp_path / "out.mp3")

    async def always_fails(text, voice, path):
        raise RuntimeError("échec edge-tts simulé")

    with patch("video_renderer._synthesize", new=AsyncMock(side_effect=always_fails)) as mock_synth:
        with pytest.raises(video_renderer.RenderError):
            video_renderer._synthesize_with_retry("bonjour", "fr-FR-HenriNeural", out_path, attempts=3)

    assert mock_synth.await_count == 3
    assert not Path(out_path).exists()


# --- _wrap_text (correctif du bug de texte coupé) -----------------------------------
# TextClip(method="caption") s'est avéré sous-estimer la largeur réelle du texte
# rendu (confirmé en isolant le rendu) et laisse déborder le cadre sur les
# légendes un peu longues (ex. le CTA). _wrap_text mesure les vraies métriques de
# la police (PIL) et pré-découpe le texte ; on l'utilise avec method="label".

def test_wrap_text_no_line_exceeds_max_width():
    from PIL import ImageFont

    font_path = video_renderer._resolve_font_path()
    max_width = 920
    text = "Retrouve l'analyse complète et toutes les news éco sur [NOM_DU_CANAL]."

    wrapped = video_renderer._wrap_text(text, font_path, 84, max_width)
    font = ImageFont.truetype(font_path, 84)
    for line in wrapped.split("\n"):
        assert font.getlength(line) <= max_width, f"ligne trop large : {line!r}"


def test_wrap_text_preserves_all_words():
    font_path = video_renderer._resolve_font_path()
    text = "Un texte assez long pour forcer plusieurs lignes de sous-titre"
    wrapped = video_renderer._wrap_text(text, font_path, 76, 500)
    assert wrapped.replace("\n", " ").split() == text.split()


def test_wrap_text_single_short_word_fits_on_one_line():
    font_path = video_renderer._resolve_font_path()
    assert video_renderer._wrap_text("Bonjour", font_path, 76, 920) == "Bonjour"


# --- _group_words_into_chunks (pure, aucune dépendance) -----------------------------

def test_group_words_into_chunks_groups_by_max_words():
    words = [
        {"text": "Le", "start": 0.0, "end": 0.2},
        {"text": "prix", "start": 0.2, "end": 0.5},
        {"text": "de", "start": 0.5, "end": 0.6},
        {"text": "l'argent", "start": 0.6, "end": 1.0},
        {"text": "monte", "start": 1.0, "end": 1.3},
    ]
    chunks = video_renderer._group_words_into_chunks(words, max_words=3)
    assert len(chunks) == 2
    assert chunks[0] == {"text": "Le prix de", "start": 0.0, "end": 0.6}
    assert chunks[1] == {"text": "l'argent monte", "start": 0.6, "end": 1.3}


def test_group_words_into_chunks_empty_input():
    assert video_renderer._group_words_into_chunks([], max_words=3) == []


def test_group_words_into_chunks_single_word_groups():
    words = [{"text": "Bonjour", "start": 0.0, "end": 0.5}]
    chunks = video_renderer._group_words_into_chunks(words, max_words=3)
    assert chunks == [{"text": "Bonjour", "start": 0.0, "end": 0.5}]


# --- _interpolate_word_timings (approxime le rythme de la voix dans une phrase) ----

def test_interpolate_word_timings_spans_full_sentence_duration():
    words = video_renderer._interpolate_word_timings("Le prix de l'argent monte", 10.0, 12.0)
    assert [w["text"] for w in words] == ["Le", "prix", "de", "l'argent", "monte"]
    assert words[0]["start"] == pytest.approx(10.0)
    assert words[-1]["end"] == pytest.approx(12.0)
    # chronologique et contigu, aucun trou ni chevauchement
    for prev, nxt in zip(words, words[1:]):
        assert prev["end"] == pytest.approx(nxt["start"])


def test_interpolate_word_timings_longer_words_get_more_time():
    words = video_renderer._interpolate_word_timings("un extraordinairement", 0.0, 1.0)
    short, long_ = words
    assert (long_["end"] - long_["start"]) > (short["end"] - short["start"])


def test_interpolate_word_timings_empty_text():
    assert video_renderer._interpolate_word_timings("", 0.0, 1.0) == []


# --- _caption_chunks (choix de granularité : mot > phrase interpolée > texte entier)
# En français, edge-tts ne fournit jamais de WordBoundary (constaté empiriquement) —
# le chemin réellement emprunté en pratique est donc l'interpolation à partir des
# phrases, pas le mot-par-mot réel (couvert pour le cas où une voix future le
# fournirait) ni le repli texte entier (dernier recours seulement).

def test_caption_chunks_prefers_words_when_available():
    words = [{"text": "Bonjour", "start": 0.0, "end": 0.5}, {"text": "toi", "start": 0.5, "end": 0.8}]
    sentences = [{"text": "Bonjour toi.", "start": 0.0, "end": 0.8}]
    chunks = video_renderer._caption_chunks(words, sentences, "Bonjour toi.", duration=0.8, max_words=3)
    assert chunks == [{"text": "Bonjour toi", "start": 0.0, "end": 0.8}]


def test_caption_chunks_interpolates_sentences_into_smaller_chunks():
    sentences = [
        {"text": "C'est le prix de l'argent.", "start": 0.0, "end": 1.5},
        {"text": "La banque centrale fixe ce taux.", "start": 1.5, "end": 3.2},
    ]
    chunks = video_renderer._caption_chunks([], sentences, "peu importe", duration=3.2, max_words=3)
    # 2 phrases de 6 et 6 mots groupées par 3 -> 4 chunks, plus fin que les phrases entières
    assert len(chunks) == 4
    assert chunks[0]["text"] == "C'est le prix"
    assert chunks[0]["start"] == pytest.approx(0.0)
    assert chunks[-1]["end"] == pytest.approx(3.2)


def test_caption_chunks_falls_back_to_full_text_as_last_resort():
    chunks = video_renderer._caption_chunks([], [], "Texte complet du segment.", duration=2.0, max_words=3)
    assert chunks == [{"text": "Texte complet du segment.", "start": 0.0, "end": 2.0}]


# --- _fit_cover (clip synthétique, pas besoin de vraie vidéo) -----------------------

def test_fit_cover_produces_exact_target_size():
    from moviepy import ColorClip

    source = ColorClip(size=(640, 360), color=(10, 20, 30)).with_duration(1)  # 16:9, pas 9:16
    fitted = video_renderer._fit_cover(source, 1080, 1920)
    assert tuple(fitted.size) == (1080, 1920)


def test_fit_cover_handles_already_taller_source():
    from moviepy import ColorClip

    source = ColorClip(size=(200, 800), color=(10, 20, 30)).with_duration(1)  # déjà plus haut que large
    fitted = video_renderer._fit_cover(source, 1080, 1920)
    assert tuple(fitted.size) == (1080, 1920)


# --- _background_clip (repli sur fond uni sans clé / sans cache) --------------------

def test_background_clip_falls_back_to_solid_color_without_pexels_key(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    clip = video_renderer._background_clip("central bank building", duration=2.0)
    assert tuple(clip.size) == tuple(config.VIDEO_RENDER_RESOLUTION)
    assert clip.duration == pytest.approx(2.0)


def test_background_clip_falls_back_when_cache_empty(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key-for-test")
    with patch("video_renderer.stock_footage.ensure_theme_cached", return_value=[]) as mock_cache:
        clip = video_renderer._background_clip("oil rig ocean", duration=1.5)
    mock_cache.assert_called_once()
    assert tuple(clip.size) == tuple(config.VIDEO_RENDER_RESOLUTION)


def test_background_clip_passes_minimum_and_fetch_count_through(monkeypatch):
    """Un mot-clé de contenu ponctuel (généré par l'IA pour un bloc précis) doit
    demander une petite réserve, pas les 5 clips par défaut d'un thème générique
    réutilisé souvent — voir _build_segment_clip(content_specific_keyword=True)."""
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key-for-test")
    with patch("video_renderer.stock_footage.ensure_theme_cached", return_value=[]) as mock_cache:
        video_renderer._background_clip("shipping containers port", duration=1.0, minimum=1, fetch_count=2)
    mock_cache.assert_called_once_with("shipping containers port", minimum=1, fetch_count=2)


# --- carte d'intro/de fin : _date_fr, _hex_to_rgb, _gradient_array, mise en page ----

def test_date_fr_no_locale_dependency():
    from datetime import date

    assert video_renderer._date_fr(date(2026, 7, 26)) == "26 juillet 2026"
    assert video_renderer._date_fr(date(2026, 1, 1)) == "1 janvier 2026"
    assert video_renderer._date_fr(date(2026, 12, 31)) == "31 décembre 2026"


def test_hex_to_rgb():
    assert video_renderer._hex_to_rgb("#3b82f6") == (59, 130, 246)
    assert video_renderer._hex_to_rgb("3b82f6") == (59, 130, 246)


def test_gradient_array_shape_and_endpoints():
    array = video_renderer._gradient_array(10, 20, (0, 0, 0), (255, 255, 255))
    assert array.shape == (20, 10, 3)
    assert tuple(array[0, 0]) == (0, 0, 0)
    assert tuple(array[-1, 0]) == (255, 255, 255)
    # dégradé croissant, pas un dégradé inversé ou constant
    assert array[10, 0, 0] > array[0, 0, 0]


def test_stacked_card_layers_returns_three_layers():
    font_path = video_renderer._resolve_font_path()
    layers = video_renderer._stacked_card_layers(
        "Récap news éco & trading", 90, "26 juillet 2026", 50, font_path, 1080, 1920, duration=3.0
    )
    assert len(layers) == 3
    for layer in layers:
        assert layer.duration == pytest.approx(3.0)


# --- _text_canvas_height (correctif texte coupé sur les cartes intro/fin) ----------
# MoviePy calcule sa hauteur via une API privée de Pillow qui n'existe plus dans la
# version installée (Pillow 11.3, voir requirements-video.txt) et retombe sur un
# calcul qui sous-estime la hauteur réelle pour certains textes (ex. tout en
# majuscules, sans accent ni lettre descendante) — confirmé en isolant le rendu.

def test_text_canvas_height_at_least_covers_ascent_plus_descent():
    font_path = video_renderer._resolve_font_path()
    font = video_renderer._load_font(font_path, 88)
    ascent, descent = font.getmetrics()
    height = video_renderer._text_canvas_height("LE LIEN EST EN BIO", font_path, 88)
    assert height >= ascent + descent


def test_text_canvas_height_grows_with_more_lines():
    font_path = video_renderer._resolve_font_path()
    one_line = video_renderer._text_canvas_height("Une ligne", font_path, 76)
    three_lines = video_renderer._text_canvas_height("Une ligne\nDeux lignes\nTrois lignes", font_path, 76)
    assert three_lines > one_line


def test_make_text_clip_uses_explicit_non_clipping_size():
    font_path = video_renderer._resolve_font_path()
    clip = video_renderer._make_text_clip("LE LIEN EST EN BIO", font_path, 88, "white", max_width=940)
    font = video_renderer._load_font(font_path, 88)
    ascent, descent = font.getmetrics()
    assert clip.h >= ascent + descent  # jamais la hauteur "tight" bugguée (65px observés)
    assert clip.size[0] == 940


# --- _card_background (fond de carte intro/fin, avec repli sur dégradé) ------------

def test_card_background_falls_back_to_gradient_without_theme_keyword(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key-for-test")
    clip = video_renderer._card_background(200, 300, duration=1.0, theme_keyword=None)
    assert tuple(clip.size) == (200, 300)


def test_card_background_falls_back_to_gradient_without_pexels_key(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    clip = video_renderer._card_background(200, 300, duration=1.0, theme_keyword="office work business")
    assert tuple(clip.size) == (200, 300)


def test_card_background_falls_back_when_cache_empty(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key-for-test")
    with patch("video_renderer.stock_footage.ensure_theme_cached", return_value=[]) as mock_cache:
        clip = video_renderer._card_background(200, 300, duration=1.0, theme_keyword="office work business")
    mock_cache.assert_called_once()
    assert tuple(clip.size) == (200, 300)


# --- contour du texte des sous-titres (remplace le bandeau semi-transparent) -------

def test_text_canvas_height_accounts_for_stroke_width():
    font_path = video_renderer._resolve_font_path()
    without_stroke = video_renderer._text_canvas_height("Bonjour", font_path, 76, stroke_width=0)
    with_stroke = video_renderer._text_canvas_height("Bonjour", font_path, 76, stroke_width=3)
    assert with_stroke == without_stroke + 6  # stroke_width * 2


def test_build_caption_text_clip_has_black_stroke():
    font_path = video_renderer._resolve_font_path()
    clip = video_renderer._build_caption_text_clip("Bonjour toi", font_path, max_width=940)
    assert clip.stroke_color == "black"


# --- structure DEBRIEF : étiquette de section + carte de transition ----------------
# Le texte est déjà trié EVENEMENT-puis-BREAKING par video_scripts._assemble_script ;
# ce module rend cet ordre visible (étiquette en haut de chaque bloc corps, carte de
# transition silencieuse au point de bascule) plutôt qu'un simple cut.

def test_accent_rule_uses_default_color_when_not_specified():
    clip = video_renderer._accent_rule(1080, 100, duration=1.0)
    pixel = tuple(int(v) for v in clip.get_frame(0)[0, 0])
    assert pixel == video_renderer._hex_to_rgb(config.VIDEO_CARD_ACCENT_COLOR)


def test_accent_rule_uses_custom_color_when_specified():
    clip = video_renderer._accent_rule(1080, 100, duration=1.0, color_hex="#ef4444")
    pixel = tuple(int(v) for v in clip.get_frame(0)[0, 0])
    assert pixel == video_renderer._hex_to_rgb("#ef4444")


def test_section_label_and_accent_evenement():
    label, accent = video_renderer._section_label_and_accent("EVENEMENT")
    assert label == config.VIDEO_SECTION_LABEL_EVENEMENT
    assert accent == config.VIDEO_CARD_ACCENT_COLOR


def test_section_label_and_accent_breaking():
    label, accent = video_renderer._section_label_and_accent("BREAKING")
    assert label == config.VIDEO_SECTION_LABEL_BREAKING
    assert accent == config.VIDEO_SECTION_BREAKING_ACCENT_COLOR


def test_build_section_tag_clip_returns_two_layers_with_duration():
    font_path = video_renderer._resolve_font_path()
    layers = video_renderer._build_section_tag_clip("ÉVÉNEMENTS DU JOUR", "#3b82f6", font_path, 1080, duration=4.0)
    assert len(layers) == 2
    for layer in layers:
        assert layer.duration == pytest.approx(4.0)


def test_build_section_tag_clip_text_has_black_stroke():
    font_path = video_renderer._resolve_font_path()
    text_layer, _rule_layer = video_renderer._build_section_tag_clip("BREAKING NEWS", "#ef4444", font_path, 1080, duration=2.0)
    assert text_layer.stroke_color == "black"


def test_build_section_transition_clip_has_configured_duration_and_size(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")  # repli sur dégradé, pas de réseau
    font_path = video_renderer._resolve_font_path()
    clip = video_renderer._build_section_transition_clip("BREAKING NEWS", "#ef4444", font_path)
    assert clip.duration == pytest.approx(config.VIDEO_SECTION_TRANSITION_SECONDS)
    assert tuple(clip.size) == tuple(config.VIDEO_RENDER_RESOLUTION)


def test_build_section_transition_clip_has_no_audio(monkeypatch):
    """Pas de voix pour cette carte : concatenate_videoclips(method="compose")
    traite un clip sans piste audio comme un silence de sa durée (voir la
    docstring de _build_section_transition_clip) — pas d'AudioClip factice requis."""
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    font_path = video_renderer._resolve_font_path()
    clip = video_renderer._build_section_transition_clip("ÉVÉNEMENTS DU JOUR", "#3b82f6", font_path)
    assert clip.audio is None
