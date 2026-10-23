"""Source de diffusion configurable ; obfuscation du flux et sécurité de surface."""
from __future__ import annotations

import base64
import os
import re

DEFAULT_VIDEO_ID = "U-n78xt3LNU"


def _encode_stream(url: str) -> str:
    """Encode l'URL en Base64 inversé pour ne pas laisser de traces en clair dans le HTML."""
    if not url:
        return ""
    return base64.b64encode(url.encode("utf-8")).decode("ascii")[::-1]


def get_secret_story() -> dict:
    video_id = os.getenv("SECRET_STORY_YOUTUBE_ID", DEFAULT_VIDEO_ID).strip()
    valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id))
    enabled = os.getenv("SECRET_STORY_ENABLED", "true").strip().lower() not in {"0", "false", "off"}

    raw_embed = (
        f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&controls=0&disablekb=1&fs=0&rel=0&playsinline=1&enablejsapi=1&iv_load_policy=3&modestbranding=1"
        if enabled and valid else ""
    )

    return {
        "enabled": enabled and valid,
        "token": _encode_stream(raw_embed),
        "source_name": "SSA SAISON 3 - 2026",
        "official_url": "https://secretstoryafrique.com/vote",
    }
