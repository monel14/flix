"""Configuration de la mise en avant ; aucun flux signé ni session privée."""
from __future__ import annotations

import os
import re

PAGE_PATH = "/secret-story-afrique"
ARTWORK = "/static/secret-story-afrique.svg"
OFFICIAL_URL = "https://secretstoryafrique.com/emission"


def get_secret_story() -> dict:
    video_id = os.getenv("SECRET_STORY_YOUTUBE_ID", "_ZqZmDeWmDI").strip()
    valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id))
    enabled = os.getenv("SECRET_STORY_ENABLED", "true").lower() not in {"0", "false", "off"}
    return {
        "enabled": enabled and valid,
        "video_id": video_id if valid else "",
        "path": PAGE_PATH,
        "artwork": ARTWORK,
        "official_url": OFFICIAL_URL,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}?playsinline=1&rel=0" if valid else "",
    }


def secret_story_slide(config: dict) -> dict:
    return {
        "title": "Secret Story Afrique",
        "slug": "secret-story-afrique",
        "image": config["artwork"],
        "version": "ÉMISSION · SAISON 3",
        "year": "2026",
        "synopsis": "Le lecteur YouTube, les horaires par pays et les informations de diffusion, réunis sur une page dédiée.",
        "content_type": "event",
        "href": config["path"],
        "cta_label": "Accéder au lecteur",
        "secondary_href": config["path"] + "#horaires",
    }
