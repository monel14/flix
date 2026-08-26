"""Environnement Jinja2 partagé par toutes les routes."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from services.player_policy import player_sandbox

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["str"] = str
templates.env.globals["player_sandbox"] = player_sandbox
