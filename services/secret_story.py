"""Source YouTube configurable ; ni flux signé, ni scraping à chaque visite."""
from __future__ import annotations

import os
import re

DEFAULT_VIDEO_ID = "U-n78xt3LNU"


def get_secret_story() -> dict:
    video_id = os.getenv("SECRET_STORY_YOUTUBE_ID", DEFAULT_VIDEO_ID).strip()
    valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id))
    enabled = os.getenv("SECRET_STORY_ENABLED", "true").strip().lower() not in {"0", "false", "off"}
    return {
        "enabled": enabled and valid,
        "video_id": video_id if valid else "",
        "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}?playsinline=1&rel=0&enablejsapi=1" if enabled and valid else "",
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}" if enabled and valid else "",
        "source_name": "SSA SAISON 3 - 2026",
        "official_url": "https://secretstoryafrique.com/vote",
    }
