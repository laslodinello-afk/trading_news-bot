"""
Tests offline uniquement : requests.get est mocké, aucun appel réseau réel vers
Pexels. Vérifie la sélection de fichier, le nommage/cache local, et le repli
gracieux ([] jamais d'exception) sur clé absente ou requête en échec.
"""
from pathlib import Path
from unittest.mock import patch

import requests

import config
import stock_footage

_SAMPLE_VIDEOS_RESPONSE = {
    "videos": [
        {
            "id": 111,
            "video_files": [
                {"file_type": "video/mp4", "width": 3840, "link": "https://example.com/4k.mp4"},
                {"file_type": "video/mp4", "width": 1080, "link": "https://example.com/hd.mp4"},
                {"file_type": "video/mp4", "width": 640, "link": "https://example.com/sd.mp4"},
            ],
        },
    ]
}


class _FakeResponse:
    """Supporte à la fois `requests.get(...)` direct (recherche) et
    `with requests.get(...) as r:` (téléchargement en stream)."""

    def __init__(self, json_data=None, content_chunks=None, status_code=200):
        self._json_data = json_data
        self._content_chunks = content_chunks if content_chunks is not None else [b"fake-video-bytes"]
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data

    def iter_content(self, chunk_size=None):
        return iter(self._content_chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_get_factory(search_json):
    def _fake_get(url, **kwargs):
        if url == stock_footage._SEARCH_URL:
            return _FakeResponse(json_data=search_json)
        return _FakeResponse(content_chunks=[b"fake-video-bytes"])

    return _fake_get


# --- _pick_video_file (pure) ---------------------------------------------------

def test_pick_video_file_chooses_closest_to_1080():
    chosen = stock_footage._pick_video_file(_SAMPLE_VIDEOS_RESPONSE["videos"][0]["video_files"])
    assert chosen["width"] == 1080


def test_pick_video_file_none_without_mp4_candidates():
    assert stock_footage._pick_video_file([{"file_type": "video/other", "width": 1080}]) is None
    assert stock_footage._pick_video_file([]) is None


# --- search_and_download --------------------------------------------------------

def test_search_and_download_returns_empty_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))
    with patch("stock_footage.requests.get") as mock_get:
        result = stock_footage.search_and_download("office work")
    assert result == []
    mock_get.assert_not_called()


def test_search_and_download_downloads_and_names_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))

    with patch("stock_footage.requests.get", side_effect=_fake_get_factory(_SAMPLE_VIDEOS_RESPONSE)):
        result = stock_footage.search_and_download("office work", count=5)

    assert len(result) == 1
    assert result[0].endswith("office_work_111.mp4")
    assert Path(result[0]).read_bytes() == b"fake-video-bytes"


def test_search_and_download_empty_results(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))
    with patch("stock_footage.requests.get", side_effect=_fake_get_factory({"videos": []})):
        result = stock_footage.search_and_download("thème introuvable")
    assert result == []


def test_search_and_download_returns_empty_on_request_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))
    with patch("stock_footage.requests.get", side_effect=requests.ConnectionError("pas de réseau")):
        result = stock_footage.search_and_download("office work")
    assert result == []


def test_search_and_download_skips_failed_individual_download(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))

    def fake_get(url, **kwargs):
        if url == stock_footage._SEARCH_URL:
            return _FakeResponse(json_data=_SAMPLE_VIDEOS_RESPONSE)
        raise requests.ConnectionError("téléchargement échoué")

    with patch("stock_footage.requests.get", side_effect=fake_get):
        result = stock_footage.search_and_download("office work")
    assert result == []


# --- ensure_theme_cached ---------------------------------------------------------

def test_ensure_theme_cached_skips_network_when_already_enough_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))
    theme_dir = tmp_path / stock_footage._theme_slug("office work")
    theme_dir.mkdir()
    for i in range(3):
        (theme_dir / f"office_work_{i}.mp4").write_bytes(b"x")

    with patch("stock_footage.requests.get") as mock_get:
        result = stock_footage.ensure_theme_cached("office work", minimum=3)

    assert len(result) == 3
    mock_get.assert_not_called()


def test_ensure_theme_cached_tops_up_when_below_minimum(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))

    with patch("stock_footage.requests.get", side_effect=_fake_get_factory(_SAMPLE_VIDEOS_RESPONSE)):
        result = stock_footage.ensure_theme_cached("office work", minimum=3)

    assert len(result) == 1  # un seul résultat dans la réponse simulée


def test_ensure_theme_cached_returns_empty_without_key_and_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    monkeypatch.setattr(config, "STOCK_FOOTAGE_DIR", str(tmp_path))
    assert stock_footage.ensure_theme_cached("office work") == []
