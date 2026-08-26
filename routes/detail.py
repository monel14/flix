from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from cache import DETAIL_TTL, cache
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_html, coflix_get_json
from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes
from scraper.voirdrama_client import VoirdramaNotFoundError, voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_detail
from services.seo import content_seo

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


async def load_detail(slug: str) -> dict:
    """Charge les détails d'un film ou d'une série avec ses épisodes et contenus liés."""
    html = await coflix_get_html(f"/film/{slug}")
    detail = parse_coflix_detail(html, slug)

    # Si aucun movie_id n'a été trouvé (ex: redirection vers la page d'accueil par le site source)
    if not detail.get("movie_id"):
        raise CoflixNotFoundError(f"Film ou série introuvable sur Coflix : {slug}")

    # Si c'est une série, on charge les épisodes
    if detail["type"] == "series" and detail["movie_id"]:
        try:
            ep_json = await coflix_get_json(
                "/ajax/episode/list-episode",
                params={"movieId": detail["movie_id"]},
            )
            detail["episodes"] = parse_coflix_episodes(ep_json)  # type: ignore[assignment]
        except CoflixFetchError as exc:
            logger.warning("Impossible de charger les épisodes pour %s : %s", slug, exc)
            detail["episodes"] = []  # type: ignore[assignment]
    else:
        detail["episodes"] = []  # type: ignore[assignment]

    # Déterminer le premier épisode ou l'ID de streaming du film
    first_ep = detail["episodes"][0] if detail.get("episodes") else None  # type: ignore[index]
    if first_ep:
        detail["first_episode_id"] = first_ep["episode_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{first_ep['episode_id']}"  # type: ignore[assignment]
    elif detail.get("episode_id"):
        detail["first_episode_id"] = detail["episode_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{detail['episode_id']}"  # type: ignore[assignment]
    elif detail.get("movie_id"):
        detail["first_episode_id"] = detail["movie_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{detail['movie_id']}"  # type: ignore[assignment]
    else:
        detail["first_episode_id"] = None  # type: ignore[assignment]
        detail["first_episode_url"] = None  # type: ignore[assignment]

    return dict(detail)


@router.get("/film/{slug}", response_class=HTMLResponse)
async def film_detail(request: Request, slug: str):
    # 1. Tentative de chargement depuis Coflix (Films / Séries)
    try:
        data = await cache.get_or_set(
            f"detail:{slug}", DETAIL_TTL, lambda: load_detail(slug)
        )
        if data and data.get("title") and data.get("movie_id"):
            return templates.TemplateResponse(request, "detail.html", {
                "request": request,
                "film": data,
                "slug": slug,
                "related": data.get("related", []),
                # content_type ("Movie"/"Series") provient de la source :
                # c'est le seul signal fiable pour typer le JSON-LD.
                "seo": content_seo(
                    request,
                    item=data,
                    path=f"/film/{slug}",
                    title_suffix="Streaming HD",
                    kind_label="en Streaming VF & VOSTFR HD",
                    content_type=data.get("content_type", ""),
                ),
            })
    except (CoflixNotFoundError, CoflixFetchError):
        pass

    # 2. Fallback intelligent : vérification si le titre est un K-Drama sur Voirdrama
    try:
        html_drama = await voirdrama_get_html(f"/drama/{slug}/")
        drama_data = parse_voirdrama_detail(html_drama, slug)
        if drama_data and drama_data.get("title") and drama_data.get("episodes"):
            logger.info("Redirection automatique de /film/%s vers /drama/%s (détecté comme K-Drama)", slug, slug)
            return RedirectResponse(url=f"/drama/{slug}", status_code=302)
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Film, série ou drama introuvable")
