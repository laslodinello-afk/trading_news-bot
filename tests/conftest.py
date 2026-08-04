"""
Isole chaque test dans sa propre base SQLite temporaire (jamais alerts.db) et son
propre dossier de sortie temporaire (jamais video_output/). Insère aussi la racine
du projet dans sys.path : le repo est un projet plat sans package, les tests vivant
dans tests/ ont besoin de ça pour `import config`/`import db`/`import video_scripts`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    # db.get_conn() bascule sur Turso (réseau réel, données de prod) dès que ces
    # deux variables sont renseignées — un test ne doit JAMAIS toucher au Turso
    # réel, donc on les vide ici pour forcer le repli SQLite local, quel que soit
    # le contenu du vrai .env de la machine qui lance les tests.
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_alerts.db"))
    db.init_db()
    return config.DB_PATH


@pytest.fixture
def temp_video_output(tmp_path, monkeypatch):
    out_dir = tmp_path / "video_output"
    monkeypatch.setattr(config, "VIDEO_OUTPUT_DIR", str(out_dir))
    return out_dir
