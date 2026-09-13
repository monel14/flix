"""Environnement Jinja2 partagé par toutes les routes."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from services.player_policy import player_sandbox

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@lru_cache(maxsize=512)
def _hash_file_content(full_path_str: str, mtime: float) -> str:
    """Calcule une empreinte courte (10 car.) du fichier, mise en cache par mtime."""
    try:
        data = Path(full_path_str).read_bytes()
        return hashlib.md5(data).hexdigest()[:10]
    except Exception:
        return str(int(mtime))


def get_static_hash(relative_path: str) -> str:
    """Retourne l'empreinte automatique d'un fichier dans le dossier static."""
    clean = relative_path.lstrip("/")
    if clean.startswith("static/"):
        clean = clean[len("static/"):]
    target_file = STATIC_DIR / clean
    if target_file.is_file():
        try:
            mtime = target_file.stat().st_mtime
            return _hash_file_content(str(target_file), mtime)
        except OSError:
            return "1"
    return "1"


def static_url(path: str) -> str:
    """Génère l'URL d'un asset statique avec hash de version automatique.

    Exemple :
        {{ static_url('flix-autoplay.js') }}
        -> /static/flix-autoplay.js?v=3a8f1b90c2
    """
    clean = path.lstrip("/")
    if clean.startswith("static/"):
        clean = clean[len("static/"):]
    v = get_static_hash(clean)
    return f"/static/{clean}?v={v}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["str"] = str
templates.env.globals["player_sandbox"] = player_sandbox
templates.env.globals["static_url"] = static_url
