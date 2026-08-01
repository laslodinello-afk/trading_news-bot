"""
Rendu vidéo (voix + visuel) à partir d'un script déjà généré par video_scripts.py.
LOCAL UNIQUEMENT, à la demande (voir video_scripts.py --render) — jamais importé
par le job automatique du soir sur Render, jamais importé par video_scripts.py au
niveau module (import paresseux dans _cli() seulement). Dépendances lourdes, voir
requirements-video.txt.

Voix : edge-tts (gratuit, aucune clé API), avec sous-titres dynamiques qui
défilent au rythme de la voix (timing réel via Communicate.stream()). Visuel :
carte d'intro (titre + date) puis fond vidéo en boucle par bloc (Pexels,
optionnel — repli sur fond uni sans clé), chaque bloc du corps cherchant SON
PROPRE mot-clé de fond pour rester pertinent au contenu du moment, plus un
graphique réel (matplotlib) quand de vraies données numériques existent — jamais
de donnée ni de fond inventé. Le CTA final est une carte de fin dédiée, stable,
sans fond vidéo ni sous-titres découpés — même habillage (dégradé + liseré) que
la carte d'intro pour encadrer la vidéo de façon cohérente.

Structure (DEBRIEF uniquement) : les blocs corps sont déjà triés événements-
publiés-puis-breaking-news par video_scripts._assemble_script — ce module rend
cet ordre visible à l'écran avec une étiquette de section en haut de chaque bloc
(voir _build_section_tag_clip) et une courte carte de transition silencieuse au
point de bascule (voir _build_section_transition_clip), plutôt qu'un simple cut
entre deux sujets sans rapport apparent.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import tempfile
from datetime import date

import numpy as np

import matplotlib

matplotlib.use("Agg")  # avant tout autre import matplotlib : pas d'affichage ici,
# et une venv Python non-framework plante sur le backend interactif par défaut.
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

import edge_tts
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from PIL import ImageFont

import config
import stock_footage
import video_scripts

logger = logging.getLogger("video_renderer")


class RenderError(Exception):
    """Échec de rendu (TTS, police introuvable...). Toujours attrapée avant de
    remonter à l'appelant : render() renvoie None plutôt que de la laisser fuiter,
    même logique que video_scripts.generate() qui renvoie None sur échec LLM."""


# --- police ------------------------------------------------------------------

_FALLBACK_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
]


def _resolve_font_path() -> str:
    if config.VIDEO_RENDER_FONT_PATH and os.path.exists(config.VIDEO_RENDER_FONT_PATH):
        return config.VIDEO_RENDER_FONT_PATH
    for path in _FALLBACK_FONTS:
        if os.path.exists(path):
            return path
    raise RenderError(
        "Aucune police trouvée pour le rendu vidéo. Définis VIDEO_RENDER_FONT_PATH "
        "dans config.py avec le chemin d'un fichier .ttf/.otf."
    )


# --- synthèse vocale + timing mot-par-mot (edge-tts, aucune clé requise) ---------

async def _synthesize(text: str, voice: str, out_path: str) -> tuple[list[dict], list[dict]]:
    """Synthétise la voix ET capture le timing en un seul passage (Communicate.stream()).
    offset/duration renvoyés par edge-tts sont en unités de 100 nanosecondes (vérifié
    dans edge_tts/submaker.py) -> secondes = valeur / 10_000_000.

    Renvoie (word_boundaries, sentence_boundaries). Constaté empiriquement (5 voix
    françaises testées, FR et CA) : edge-tts ne renvoie JAMAIS de WordBoundary pour
    le français, seulement des SentenceBoundary — limitation du service Microsoft,
    pas un bug edge-tts. word_boundaries reste donc utile si une voix future/une
    autre langue le supporte, mais sentence_boundaries est la donnée réellement
    exploitable aujourd'hui pour des sous-titres qui changent dans le temps."""
    communicate = edge_tts.Communicate(text, voice, rate=config.VIDEO_TTS_RATE)
    word_boundaries: list[dict] = []
    sentence_boundaries: list[dict] = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_boundaries.append(
                    {
                        "text": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                    }
                )
            elif chunk["type"] == "SentenceBoundary":
                sentence_boundaries.append(
                    {
                        "text": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                    }
                )
    return word_boundaries, sentence_boundaries


def _synthesize_with_retry(text: str, voice: str, out_path: str, attempts: int = 3) -> tuple[list[dict], list[dict]]:
    """edge-tts s'appuie sur un endpoint Microsoft non officiel, moins fiable que
    l'API Gemini — contrairement à ai_analyzer/_call_llm_with_retry (1 seul retry),
    on retente ici jusqu'à `attempts` fois avant d'abandonner."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            boundaries = asyncio.run(_synthesize(text, voice, out_path))
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return boundaries
            last_exc = RenderError("Fichier audio vide généré par edge-tts.")
        except Exception as exc:  # noqa: BLE001 - on veut vraiment tout capturer ici
            last_exc = exc
        logger.warning("edge-tts a échoué (tentative %d/%d): %s", attempt, attempts, last_exc)
    raise RenderError(f"edge-tts a échoué après {attempts} tentatives: {last_exc}")


def _group_words_into_chunks(word_boundaries: list[dict], max_words: int) -> list[dict]:
    """Groupe des mots consécutifs (avec leur timing réel) en paquets de
    `max_words` maximum -> des sous-titres qui changent au rythme de la voix
    plutôt qu'une légende figée pour tout le segment. Fonction pure, testable
    sans TTS ni réseau. N'est exploitable en pratique que si la voix renvoie des
    WordBoundary (pas le cas des voix françaises actuellement, voir _synthesize)."""
    chunks = []
    for i in range(0, len(word_boundaries), max_words):
        group = word_boundaries[i : i + max_words]
        if not group:
            continue
        chunks.append(
            {
                "text": " ".join(w["text"] for w in group),
                "start": group[0]["start"],
                "end": group[-1]["end"],
            }
        )
    return chunks


def _interpolate_word_timings(sentence_text: str, start: float, end: float) -> list[dict]:
    """Estime le timing de chaque mot d'une phrase par interpolation proportionnelle
    à la longueur des mots — edge-tts ne fournit pas de vrai timing mot-par-mot en
    français (voir _synthesize), seulement le début/fin de la phrase entière. Ce
    n'est pas un chronométrage exact (un mot court n'est pas forcément prononcé
    plus vite qu'un mot long), mais c'est une approximation standard, largement
    meilleure que d'afficher toute la phrase d'un bloc : les sous-titres défilent
    au rythme approximatif de la voix plutôt que de rester figés plusieurs secondes."""
    words = sentence_text.split()
    if not words:
        return []
    weights = [len(w) + 1 for w in words]  # +1 ~= la pause/l'espace après le mot
    total_weight = sum(weights)
    duration = max(end - start, 0.01)
    result = []
    t = start
    for word, w in zip(words, weights):
        word_duration = duration * (w / total_weight)
        result.append({"text": word, "start": t, "end": t + word_duration})
        t += word_duration
    return result


def _caption_chunks(word_boundaries: list[dict], sentence_boundaries: list[dict], spoken_text: str, duration: float, max_words: int) -> list[dict]:
    """Choisit la meilleure granularité disponible pour des sous-titres qui
    défilent dans le temps plutôt qu'un bloc figé par segment :
    1. mot-par-mot réel si la voix le fournit (aucune voix française ne le fait
       aujourd'hui, voir _synthesize) ;
    2. sinon, mots interpolés à l'intérieur de chaque phrase (à partir du vrai
       timing de phrase que edge-tts fournit en français) — c'est le chemin
       réellement emprunté aujourd'hui ;
    3. en tout dernier recours, le texte entier d'un coup — jamais un écran vide."""
    if word_boundaries:
        return _group_words_into_chunks(word_boundaries, max_words)
    if sentence_boundaries:
        interpolated: list[dict] = []
        for sentence in sentence_boundaries:
            interpolated.extend(_interpolate_word_timings(sentence["text"], sentence["start"], sentence["end"]))
        if interpolated:
            return _group_words_into_chunks(interpolated, max_words)
    return [{"text": spoken_text, "start": 0.0, "end": duration}]


# --- graphique de données réelles (jamais inventé) --------------------------------

def _parse_numeric(raw: str | None) -> float | None:
    """'180K' -> 180000.0, '3.00%' -> 3.0, '-0.3%' -> -0.3, 'N/A'/None -> None.
    Ne lève jamais."""
    if not raw:
        return None
    text = raw.strip().replace(",", "").replace("%", "").replace(" ", "")
    multiplier = 1.0
    if text[-1:].upper() == "K":
        multiplier, text = 1_000.0, text[:-1]
    elif text[-1:].upper() == "M":
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def build_chart_image(event: dict, out_path: str) -> str | None:
    """Graphique réel (prévision/précédent vs réel) pour un event ayant au moins 2
    valeurs numériques exploitables. None sinon — jamais de barre à 0 pour une
    valeur manquante, jamais de graphique à partir d'un seul chiffre."""
    actual = _parse_numeric(event.get("actual"))
    if actual is None:
        return None

    bars: list[tuple[str, float]] = []
    forecast = _parse_numeric(event.get("forecast"))
    previous = _parse_numeric(event.get("previous"))
    if forecast is not None:
        bars.append(("Prévision", forecast))
    elif previous is not None:
        bars.append(("Précédent", previous))
    if not bars:
        return None
    bars.append(("Réel", actual))

    fig = Figure(figsize=(8, 5), dpi=150)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    labels = [b[0] for b in bars]
    values = [b[1] for b in bars]
    ax.bar(labels, values, color=["#8a8f98", "#3b82f6"][: len(bars)])
    ax.set_title(event.get("title", ""), color="white", fontsize=14)
    ax.tick_params(colors="white", labelsize=12)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)
    fig.savefig(out_path, transparent=True)
    return out_path


# --- fond vidéo en boucle (optionnel, repli sur fond uni) -------------------------

def _fit_cover(clip, target_w: int, target_h: int):
    """Redimensionne `clip` pour couvrir exactement (target_w, target_h) puis
    rogne au centre — ramène n'importe quel ratio source à la taille cible sans
    déformation ni bandes noires."""
    clip_w, clip_h = clip.size
    scale = max(target_w / clip_w, target_h / clip_h)
    resized = clip.with_effects([vfx.Resize(scale)])
    resized_w, resized_h = resized.size
    return resized.with_effects(
        [vfx.Crop(width=target_w, height=target_h, x_center=resized_w / 2, y_center=resized_h / 2)]
    )


def _apply_ken_burns(clip, zoom_per_second: float = 0.01):
    """Léger zoom continu (quasi imperceptible sur un fond uni, donne un peu de
    vie à un fond vidéo déjà en mouvement) plutôt qu'un fond parfaitement statique."""
    return clip.with_effects([vfx.Resize(lambda t: 1 + zoom_per_second * t)]).with_position("center")


def _background_clip(theme: str, duration: float, minimum: int = 3, fetch_count: int | None = None):
    """`theme` est un mot-clé de recherche Pexels arbitraire — soit le thème
    générique du format (hook/chute, voir config.STOCK_FOOTAGE_THEME_BY_FORMAT),
    soit le visual_keyword propre à un bloc précis (voir render()), pour que le
    fond corresponde vraiment à ce dont il est question à ce moment de la vidéo."""
    width, height = config.VIDEO_RENDER_RESOLUTION
    clips = stock_footage.ensure_theme_cached(theme, minimum=minimum, fetch_count=fetch_count) if config.PEXELS_API_KEY else []
    if clips:
        try:
            source = VideoFileClip(random.choice(clips)).without_audio()
            fitted = _fit_cover(source, width, height)
            looped = fitted.with_effects([vfx.Loop(duration=duration)])
            return _apply_ken_burns(looped)
        except Exception as exc:  # noqa: BLE001 - un clip corrompu ne doit pas bloquer le rendu
            logger.warning("Fond vidéo indisponible (%s), repli sur fond uni.", exc)
    base = ColorClip(size=(width, height), color=config.VIDEO_RENDER_BG_COLOR).with_duration(duration)
    return _apply_ken_burns(base)


# --- composition visuelle ------------------------------------------------------

_font_cache: dict[tuple[str, int], "ImageFont.FreeTypeFont"] = {}


def _load_font(font_path: str, font_size: int) -> "ImageFont.FreeTypeFont":
    key = (font_path, font_size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(font_path, font_size)
    return _font_cache[key]


def _wrap_text(text: str, font_path: str, font_size: int, max_width: int) -> str:
    """Retour à la ligne manuel, mesuré avec les vraies métriques du fichier de
    police (PIL) plutôt que l'estimation de TextClip(method="caption"), qui s'est
    avérée trop large et laisse déborder le texte du cadre (bug constaté avec des
    légendes un peu longues, ex. le CTA) — c'est ce qui causait le texte coupé.
    Utilisé avec TextClip(method="label") qui respecte les \\n tels quels."""
    font = _load_font(font_path, font_size)
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or font.getlength(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _text_canvas_height(wrapped_text: str, font_path: str, font_size: int, stroke_width: int = 0) -> int:
    """Hauteur fiable pour TextClip(method="label"). MoviePy calcule sa hauteur via
    une API privée de Pillow (draw._multiline_spacing) quand elle existe, sinon
    retombe sur un bbox "tight" qui SOUS-ESTIME la hauteur réelle des glyphes pour
    certains textes (constaté avec du texte tout en majuscules, sans accent ni
    lettre descendante, ex. "LE LIEN EST EN BIO") — c'est ce qui coupait le haut/
    bas du texte sur les cartes d'intro/fin. Pillow 11.3 (celle qu'on utilise,
    voir requirements-video.txt) n'a plus cette API privée, donc MoviePy retombe
    systématiquement sur le calcul buggé ici. On calcule la hauteur nous-mêmes à
    partir des vraies métriques de la police (ascent+descent), et on la passe en
    `size` explicite pour court-circuiter le calcul de MoviePy."""
    font = _load_font(font_path, font_size)
    ascent, descent = font.getmetrics()
    num_lines = wrapped_text.count("\n") + 1
    line_spacing = 4  # défaut PIL entre les lignes
    margin = 8  # marge de sécurité
    return num_lines * (ascent + descent) + (num_lines - 1) * line_spacing + margin + stroke_width * 2


def _make_text_clip(
    text: str, font_path: str, font_size: int, color: str, max_width: int,
    stroke_color: str | None = None, stroke_width: int = 0,
):
    """TextClip(method="label") avec une largeur ET une hauteur de canevas
    explicites (voir _text_canvas_height) plutôt que le calcul automatique de
    MoviePy — c'est ce qui causait le texte coupé sur les cartes d'intro/fin."""
    wrapped = _wrap_text(text, font_path, font_size, max_width)
    height = _text_canvas_height(wrapped, font_path, font_size, stroke_width)
    kwargs = dict(
        font=font_path, text=wrapped, font_size=font_size, color=color,
        method="label", text_align="center", size=(max_width, height),
    )
    if stroke_width:
        kwargs["stroke_color"] = stroke_color or "black"
        kwargs["stroke_width"] = stroke_width
    return TextClip(**kwargs)


def _build_caption_text_clip(text: str, font_path: str, max_width: int):
    """Construit juste le clip texte (retour à la ligne mesuré, pas de position/
    timing). Contour noir plutôt qu'un bandeau semi-transparent derrière le texte :
    lisible sur n'importe quel fond sans "banderole" visible à l'écran."""
    return _make_text_clip(text, font_path, 76, "white", max_width, stroke_color="black", stroke_width=3)


def _build_segment_clip(
    spoken_text,
    voice,
    tmp_dir,
    font_path,
    index,
    background_keyword: str,
    chart_path=None,
    content_specific_keyword: bool = False,
    section_label: str | None = None,
    section_accent: str | None = None,
):
    mp3_path = os.path.join(tmp_dir, f"segment_{index}.mp3")
    word_boundaries, sentence_boundaries = _synthesize_with_retry(spoken_text, voice, mp3_path)
    audio_clip = AudioFileClip(mp3_path)
    duration = audio_clip.duration
    width, height = config.VIDEO_RENDER_RESOLUTION

    if content_specific_keyword:
        # Mot-clé ponctuel généré par l'IA pour ce bloc précis (peut ne jamais
        # revenir) : on ne télécharge qu'une petite réserve, pas les 5 par défaut.
        background = _background_clip(background_keyword, duration, minimum=1, fetch_count=config.STOCK_FOOTAGE_CONTENT_CLIP_COUNT)
    else:
        background = _background_clip(background_keyword, duration, fetch_count=config.STOCK_FOOTAGE_FALLBACK_CLIP_COUNT)
    layers = [background]

    if section_label:
        # Repère structurel pour un bloc corps de DEBRIEF (voir _build_section_tag_clip)
        # — ne coexiste jamais avec le graphique (chart_path n'est passé que pour le
        # hook, jamais pour un bloc corps), donc pas de collision d'espace à l'écran.
        layers += _build_section_tag_clip(section_label, section_accent, font_path, width, duration)

    if chart_path:
        layers.append(
            ImageClip(chart_path)
            .resized(width=width - 160)
            .with_duration(duration)
            .with_position(("center", int(height * 0.08)))
            .with_effects([vfx.CrossFadeIn(0.3)])
        )

    # Sous-titres en plein centre de l'écran, sans bandeau de contraste derrière :
    # le contour noir (voir _build_caption_text_clip) assure la lisibilité tout
    # seul, sur n'importe quel fond.
    chunks = _caption_chunks(word_boundaries, sentence_boundaries, spoken_text, duration, config.VIDEO_CAPTION_MAX_WORDS_PER_CHUNK)
    max_text_width = width - 140
    text_clips = [_build_caption_text_clip(c["text"], font_path, max_text_width) for c in chunks]

    center_y = height / 2
    for chunk, text_clip in zip(chunks, text_clips):
        chunk_duration = max(chunk["end"] - chunk["start"], 0.05)
        fade = min(0.12, chunk_duration / 3)
        layers.append(
            text_clip.with_position(("center", center_y - text_clip.h / 2))
            .with_start(chunk["start"])
            .with_duration(chunk_duration)
            .with_effects([vfx.CrossFadeIn(fade)])
        )

    return CompositeVideoClip(layers, size=(width, height)).with_duration(duration).with_audio(audio_clip)


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _gradient_array(width: int, height: int, color_top: tuple, color_bottom: tuple) -> np.ndarray:
    top = np.array(color_top, dtype=np.float64).reshape(1, 1, 3)
    bottom = np.array(color_bottom, dtype=np.float64).reshape(1, 1, 3)
    t = np.linspace(0.0, 1.0, height).reshape(height, 1, 1)
    gradient = np.repeat(top * (1 - t) + bottom * t, width, axis=1)
    return gradient.astype("uint8")


def _card_background(width: int, height: int, duration: float, theme_keyword: str | None = None):
    """Fond de la carte d'intro/de fin : vidéo de fond (même thème que le format,
    voir STOCK_FOOTAGE_THEME_BY_FORMAT) assombrie pour le contraste du texte, si
    Pexels est configuré — repli sur un dégradé uni sinon. Un fond animé assombri
    reste plus soigné qu'un simple aplat de couleur, tout en gardant assez de
    contraste pour le titre/sous-titre par-dessus."""
    if theme_keyword and config.PEXELS_API_KEY:
        try:
            clips = stock_footage.ensure_theme_cached(theme_keyword, fetch_count=config.STOCK_FOOTAGE_FALLBACK_CLIP_COUNT)
            if clips:
                source = VideoFileClip(random.choice(clips)).without_audio()
                fitted = _fit_cover(source, width, height)
                looped = fitted.with_effects([vfx.Loop(duration=duration)])
                dark_overlay = ColorClip(size=(width, height), color=(0, 0, 0)).with_opacity(0.6).with_duration(duration)
                return CompositeVideoClip([looped, dark_overlay], size=(width, height)).with_duration(duration)
        except Exception as exc:  # noqa: BLE001 - un clip corrompu ne doit pas bloquer le rendu
            logger.warning("Fond de carte indisponible (%s), repli sur dégradé.", exc)
    array = _gradient_array(width, height, config.VIDEO_CARD_GRADIENT_TOP, config.VIDEO_CARD_GRADIENT_BOTTOM)
    return ImageClip(array).with_duration(duration)


def _accent_rule(width: int, y: float, duration: float, rule_width: int = 220, color_hex: str | None = None):
    return (
        ColorClip(size=(rule_width, 5), color=_hex_to_rgb(color_hex or config.VIDEO_CARD_ACCENT_COLOR))
        .with_duration(duration)
        .with_position(("center", int(y)))
    )


def _build_section_tag_clip(label: str, accent_hex: str, font_path: str, width: int, duration: float):
    """Étiquette de section en haut de l'écran pour un bloc corps de DEBRIEF (ex.
    "ÉVÉNEMENTS DU JOUR" / "BREAKING NEWS") — seul repère structurel visible tout
    au long d'un bloc, pour que le spectateur sache en permanence dans quelle
    partie du débrief il se trouve, sans dépendre uniquement du sous-titre du
    moment. Même contour noir que les sous-titres (voir _build_caption_text_clip)
    pour rester lisible sur n'importe quel fond vidéo."""
    tag_clip = _make_text_clip(label.upper(), font_path, 38, "white", width - 200, stroke_color="black", stroke_width=2)
    tag_y = 100
    rule_y = tag_y + tag_clip.h + 16
    return [
        tag_clip.with_position(("center", tag_y)).with_duration(duration).with_effects([vfx.CrossFadeIn(0.25)]),
        _accent_rule(width, rule_y, duration, rule_width=90, color_hex=accent_hex),
    ]


def _build_section_transition_clip(label: str, accent_hex: str, font_path: str, theme_keyword: str | None = None):
    """Courte carte silencieuse (voir VIDEO_SECTION_TRANSITION_SECONDS) insérée
    entre la section "événements du jour" et la section "breaking news" d'un
    DEBRIEF (voir render()), pour annoncer visuellement le changement plutôt
    qu'un simple cut — même habillage (fond de carte assombri + liseré accent)
    que les cartes d'intro/fin pour rester cohérent avec le reste de la vidéo.
    Pas de voix ni d'AudioClip factice à fabriquer : concatenate_videoclips(
    method="compose") traite nativement un clip sans piste audio comme un
    silence de sa durée dans la piste finale (vérifié dans le code source de
    moviepy, CompositeVideoClip.py — les clips à `audio=None` sont simplement
    exclus du CompositeAudioClip final)."""
    duration = config.VIDEO_SECTION_TRANSITION_SECONDS
    width, height = config.VIDEO_RENDER_RESOLUTION
    background = _card_background(width, height, duration, theme_keyword)
    title_clip = _make_text_clip(label.upper(), font_path, 60, "white", width - 160)
    rule_y = height / 2 + title_clip.h / 2 + 28
    layers = [
        background,
        title_clip.with_position("center").with_duration(duration),
        _accent_rule(width, rule_y, duration, color_hex=accent_hex),
    ]
    return CompositeVideoClip(layers, size=(width, height)).with_duration(duration)


def _stacked_card_layers(title_text, title_size, subtitle_text, subtitle_size, font_path, width, height, duration):
    """Titre en grand + liseré accent + sous-titre plus petit, empilés et centrés
    verticalement — mise en page partagée par la carte d'intro et la carte de fin,
    pour un habillage cohérent plutôt qu'un bloc de texte brut sur fond uni."""
    title_clip = _make_text_clip(title_text.upper(), font_path, title_size, "white", width - 140)
    subtitle_clip = _make_text_clip(subtitle_text, font_path, subtitle_size, "#c7d2e3", width - 180)

    gap, rule_height = 40, 5
    total_h = title_clip.h + gap + rule_height + gap + subtitle_clip.h
    top_y = (height - total_h) / 2

    rule_y = top_y + title_clip.h + gap
    subtitle_y = rule_y + rule_height + gap
    return [
        title_clip.with_position(("center", top_y)).with_duration(duration),
        _accent_rule(width, rule_y, duration),
        subtitle_clip.with_position(("center", subtitle_y)).with_duration(duration),
    ]


_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _date_fr(d: date) -> str:
    """Ne dépend d'aucune locale système (souvent absente/imprévisible) —
    contrairement à strftime('%B'), le résultat est toujours en français."""
    return f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


def _build_intro_card_clip(target_date: date, voice: str, tmp_dir: str, font_path: str, theme_keyword: str | None = None):
    """Carte d'ouverture : titre du 'show' + date du jour, dite ET affichée, avant
    le hook — qui lui reste interdit d'intro générique (voir _system.txt) : cette
    carte identifie la vidéo, le hook accroche direct sur le contenu juste après.
    Fond vidéo (pas de sous-titres qui défilent, contrairement aux segments de
    contenu) : un écran d'ouverture doit rester stable et lisible d'un coup d'œil."""
    date_str = _date_fr(target_date)
    spoken = f"{config.VIDEO_INTRO_TITLE}, du {date_str}."
    mp3_path = os.path.join(tmp_dir, "segment_intro.mp3")
    _synthesize_with_retry(spoken, voice, mp3_path)
    audio_clip = AudioFileClip(mp3_path)
    duration = audio_clip.duration
    width, height = config.VIDEO_RENDER_RESOLUTION

    background = _card_background(width, height, duration, theme_keyword)
    layers = _stacked_card_layers(config.VIDEO_INTRO_TITLE, 92, date_str, 54, font_path, width, height, duration)
    return CompositeVideoClip([background] + layers, size=(width, height)).with_duration(duration).with_audio(audio_clip)


def _build_end_card_clip(cta_text: str, voice: str, tmp_dir: str, font_path: str, index: int, theme_keyword: str | None = None):
    """Carte de fin dédiée pour le CTA : pas de sous-titres qui défilent comme
    dans les segments de contenu — un écran de fin doit rester lisible et stable.
    VIDEO_CTA_HEADLINE (grand) + cta_text (plus petit) ne sont jamais dits deux
    fois : seul cta_text est envoyé à la synthèse vocale."""
    mp3_path = os.path.join(tmp_dir, f"segment_{index}.mp3")
    _synthesize_with_retry(cta_text, voice, mp3_path)
    audio_clip = AudioFileClip(mp3_path)
    duration = audio_clip.duration
    width, height = config.VIDEO_RENDER_RESOLUTION

    background = _card_background(width, height, duration, theme_keyword)
    layers = _stacked_card_layers(config.VIDEO_CTA_HEADLINE, 88, cta_text, 54, font_path, width, height, duration)
    return CompositeVideoClip([background] + layers, size=(width, height)).with_duration(duration).with_audio(audio_clip)


# --- orchestration ---------------------------------------------------------------

def _section_label_and_accent(section: str) -> tuple[str, str]:
    if section == "BREAKING":
        return config.VIDEO_SECTION_LABEL_BREAKING, config.VIDEO_SECTION_BREAKING_ACCENT_COLOR
    return config.VIDEO_SECTION_LABEL_EVENEMENT, config.VIDEO_CARD_ACCENT_COLOR


def render(script: dict, out_path: str, voice: str | None = None) -> str | None:
    """script vient de video_scripts.generate(). Dérive le format/la date du script
    lui-même et va chercher l'événement source (pour le graphique) via
    video_scripts.get_source_event() — l'appelant CLI n'a donc rien à brancher.
    Ne lève jamais : renvoie out_path en cas de succès, None sinon (loggué)."""
    voice = voice or config.VIDEO_TTS_VOICE
    fmt = script["format"]
    target_date = date.fromisoformat(script["date"])

    try:
        font_path = _resolve_font_path()
    except RenderError as exc:
        logger.error(str(exc))
        return None

    with tempfile.TemporaryDirectory(prefix="video_render_") as tmp_dir:
        try:
            source_event = video_scripts.get_source_event(fmt, target_date)
            chart_path = build_chart_image(source_event, os.path.join(tmp_dir, "chart.png")) if source_event else None
            default_keyword = config.STOCK_FOOTAGE_THEME_BY_FORMAT.get(fmt, "finance")

            # Le hook/la chute (pas de sujet précis à eux seuls) utilisent le thème
            # générique du format ; chaque bloc corps utilise SON PROPRE
            # visual_keyword (généré par l'IA pour ce bloc précis, voir
            # video_scripts._assemble_script) pour que le fond corresponde vraiment
            # au sujet dont il est question à ce moment de la vidéo.
            segments_plan = [("hook", script["hook"], default_keyword, False, None)]
            for bloc in script["corps"]:
                keyword = bloc.get("visual_keyword") or default_keyword
                segments_plan.append(("corps", bloc["oral"], keyword, keyword != default_keyword, bloc.get("section")))
            segments_plan.append(("chute", script["chute"], default_keyword, False, None))

            # DEBRIEF uniquement (seul format dont les blocs corps portent un
            # "section", déjà triés EVENEMENT avant BREAKING par
            # video_scripts._assemble_script) : une étiquette de section sur chaque
            # bloc corps, plus une courte carte de transition silencieuse au point
            # de bascule EVENEMENT -> BREAKING, si les deux sections coexistent ce
            # soir-là. Structure purement visuelle : aucune influence sur les
            # formats à sujet unique (section reste None pour eux).
            clips = [_build_intro_card_clip(target_date, voice, tmp_dir, font_path, theme_keyword=default_keyword)]
            prev_section = None
            for i, (kind, spoken, keyword, is_content_specific, section) in enumerate(segments_plan):
                if section == "BREAKING" and prev_section == "EVENEMENT":
                    label, accent = _section_label_and_accent("BREAKING")
                    clips.append(_build_section_transition_clip(label, accent, font_path, theme_keyword=default_keyword))

                section_label = section_accent = None
                if section:
                    section_label, section_accent = _section_label_and_accent(section)

                clips.append(
                    _build_segment_clip(
                        spoken, voice, tmp_dir, font_path, i,
                        background_keyword=keyword,
                        chart_path=chart_path if kind == "hook" else None,
                        content_specific_keyword=is_content_specific,
                        section_label=section_label,
                        section_accent=section_accent,
                    )
                )
                prev_section = section
            clips.append(
                _build_end_card_clip(script["cta"], voice, tmp_dir, font_path, len(segments_plan), theme_keyword=default_keyword)
            )

            final = concatenate_videoclips(clips, method="compose")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            final.write_videofile(out_path, fps=config.VIDEO_RENDER_FPS, codec="libx264", audio_codec="aac", logger=None)

            final.close()
            for clip in clips:
                clip.close()
            return out_path
        except Exception as exc:  # noqa: BLE001 - jamais laisser fuiter vers la CLI
            logger.error("Rendu vidéo échoué pour %s: %s", fmt, exc)
            return None
