from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import DETAIL_TTL, PLAYER_TTL, cache
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_html, coflix_get_json
from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes, parse_coflix_servers

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_servers(episode_id: str) -> list:
    json_data = await coflix_get_json(f"/ajax/episode/player?episode_id={episode_id}")
    return parse_coflix_servers(json_data)


@router.get("/regarder/{slug}/ep-{episode_id}", response_class=HTMLResponse)
async def player(request: Request, slug: str, episode_id: str) -> HTMLResponse:
    """Page lecteur pour un épisode/film donné."""
    # Charger les détails du film/série (depuis le cache si dispo)
    try:
        film = await cache.get_or_set(
            f"detail:{slug}",
            DETAIL_TTL,
            lambda: _load_detail_for_player(slug),
        )
    except CoflixNotFoundError:
        raise HTTPException(status_code=404, detail="Film introuvable")
    except CoflixFetchError as exc:
        logger.warning("Erreur player détail %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source indisponible")

    # Charger les serveurs pour cet épisode
    try:
        servers = await cache.get_or_set(
            f"servers:{episode_id}",
            PLAYER_TTL,
            lambda: _load_servers(episode_id),
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur chargement serveurs ep %s : %s", episode_id, exc)
        servers = []

    if not servers:
        raise HTTPException(
            status_code=404,
            detail="Aucun serveur disponible pour ce contenu. Il a peut-être été retiré du site source.",
        )

    # Épisodes de la série pour la navigation
    episodes = film.get("episodes", [])

    # Épisode courant
    current_ep = next((e for e in episodes if e["episode_id"] == episode_id), None)

    # Épisode précédent / suivant
    ep_index = next((i for i, e in enumerate(episodes) if e["episode_id"] == episode_id), -1)
    prev_ep = episodes[ep_index + 1] if ep_index >= 0 and ep_index + 1 < len(episodes) else None
    next_ep = episodes[ep_index - 1] if ep_index > 0 else None

    return templates.TemplateResponse(request, "player.html", {
        "request": request,
        "film": film,
        "slug": slug,
        "episode_id": episode_id,
        "servers": servers,
        "current_ep": current_ep,
        "episodes": episodes,
        "prev_ep": prev_ep,
        "next_ep": next_ep,
        "default_server": servers[0] if servers else None,
    })


@router.get("/api/servers/{episode_id}", response_class=JSONResponse)
async def api_servers(episode_id: str) -> JSONResponse:
    """API JSON pour récupérer les serveurs d'un épisode (utilisé par le JS du player)."""
    try:
        servers = await cache.get_or_set(
            f"servers:{episode_id}",
            PLAYER_TTL,
            lambda: _load_servers(episode_id),
        )
        return JSONResponse({"servers": servers})
    except CoflixFetchError as exc:
        logger.warning("API servers erreur %s : %s", episode_id, exc)
        return JSONResponse({"servers": [], "error": str(exc)}, status_code=502)


async def _load_detail_for_player(slug: str) -> dict:
    """Charge les détails + épisodes pour le player (même logique que detail.py)."""
    from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes

    html = await coflix_get_html(f"/film/{slug}")
    detail = parse_coflix_detail(html, slug)

    if detail["type"] == "series" and detail["movie_id"]:
        try:
            ep_json = await coflix_get_json(
                f"/ajax/episode/list-episode?movieId={detail['movie_id']}"
            )
            detail["episodes"] = parse_coflix_episodes(ep_json)  # type: ignore[assignment]
        except CoflixFetchError:
            detail["episodes"] = []  # type: ignore[assignment]
    else:
        detail["episodes"] = []  # type: ignore[assignment]

    first_ep = detail["episodes"][0] if detail.get("episodes") else None  # type: ignore[index]
    if first_ep:
        detail["first_episode_id"] = first_ep["episode_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = first_ep["url"]  # type: ignore[assignment]
    elif detail["type"] == "movie" and detail["movie_id"]:
        detail["first_episode_id"] = detail["movie_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{detail['movie_id']}"  # type: ignore[assignment]
    else:
        detail["first_episode_id"] = None  # type: ignore[assignment]
        detail["first_episode_url"] = None  # type: ignore[assignment]

    return dict(detail)
