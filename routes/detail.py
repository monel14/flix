from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import DETAIL_TTL, EPISODE_TTL, cache
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_html, coflix_get_json
from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_detail(slug: str) -> dict:
    html = await coflix_get_html(f"/film/{slug}")
    detail = parse_coflix_detail(html, slug)

    # Si c'est une série, on charge les épisodes
    if detail["type"] == "series" and detail["movie_id"]:
        try:
            ep_json = await coflix_get_json(
                f"/ajax/episode/list-episode?movieId={detail['movie_id']}"
            )
            detail["episodes"] = parse_coflix_episodes(ep_json)  # type: ignore[assignment]
        except CoflixFetchError as exc:
            logger.warning("Impossible de charger les épisodes pour %s : %s", slug, exc)
            detail["episodes"] = []  # type: ignore[assignment]
    else:
        detail["episodes"] = []  # type: ignore[assignment]

    # Premier épisode pour les séries, sinon ID direct pour les films
    first_ep = detail["episodes"][0] if detail.get("episodes") else None  # type: ignore[index]
    detail["first_episode_id"] = first_ep["episode_id"] if first_ep else None  # type: ignore[assignment]
    detail["first_episode_url"] = first_ep["url"] if first_ep else None  # type: ignore[assignment]

    return dict(detail)


@router.get("/film/{slug}", response_class=HTMLResponse)
async def film_detail(request: Request, slug: str) -> HTMLResponse:
    try:
        data = await cache.get_or_set(
            f"detail:{slug}", DETAIL_TTL, lambda: _load_detail(slug)
        )
    except CoflixNotFoundError:
        raise HTTPException(status_code=404, detail="Film introuvable")
    except CoflixFetchError as exc:
        logger.warning("Erreur détail %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source indisponible")

    if not data or not data.get("title"):
        raise HTTPException(status_code=404, detail="Film introuvable")

    return templates.TemplateResponse(request, "detail.html", {
        "request": request,
        "film": data,
        "slug": slug,
    })
