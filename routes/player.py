from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from cache import DETAIL_TTL, PLAYER_TTL, cache
from services.seo import page_seo
from routes.detail import load_detail
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_json
from scraper.coflix_parser import parse_coflix_servers

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


async def _load_servers(episode_id: str) -> list:
    json_data = await coflix_get_json(
        "/ajax/episode/player",
        params={"episode_id": episode_id},
    )
    return parse_coflix_servers(json_data)


@router.get("/regarder/{slug}/ep-{episode_id}", response_class=HTMLResponse)
async def player(request: Request, slug: str, episode_id: str) -> HTMLResponse:
    """Page lecteur pour un épisode/film donné."""
    # Charger les détails du film/série (depuis le cache si dispo)
    try:
        film = await cache.get_or_set(
            f"detail:{slug}",
            DETAIL_TTL,
            lambda: load_detail(slug),
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

    # Fallback si l'URL appelait un ID incorrect (ex: movie_id au lieu de l'ID d'épisode streaming)
    if not servers:
        alt_ep_id = film.get("episode_id") or film.get("first_episode_id")
        if alt_ep_id and str(alt_ep_id) != str(episode_id):
            logger.info("Tentative de fallback serveurs avec alt_ep_id=%s pour slug=%s", alt_ep_id, slug)
            try:
                servers = await cache.get_or_set(
                    f"servers:{alt_ep_id}",
                    PLAYER_TTL,
                    lambda: _load_servers(str(alt_ep_id)),
                )
                if servers:
                    episode_id = str(alt_ep_id)
            except CoflixFetchError as exc:
                logger.warning("Erreur fallback serveurs ep %s : %s", alt_ep_id, exc)

    if not servers:
        raise HTTPException(
            status_code=404,
            detail="Aucun serveur disponible pour ce contenu. Il a peut-être été retiré du site source.",
        )

    # Épisodes de la série pour la navigation
    episodes = film.get("episodes", [])

    # Épisode courant
    current_ep = next((e for e in episodes if str(e["episode_id"]) == str(episode_id)), None)

    # Épisode précédent / suivant (liste des épisodes en ordre croissant)
    ep_index = next((i for i, e in enumerate(episodes) if str(e["episode_id"]) == str(episode_id)), -1)
    prev_ep = episodes[ep_index - 1] if ep_index > 0 else None
    next_ep = episodes[ep_index + 1] if ep_index >= 0 and ep_index + 1 < len(episodes) else None

    base_title = film.get("title", "")
    seo_title = (
        f"{base_title} — {current_ep['title']} — Streaming — NokaTV"
        if current_ep else
        f"{base_title} — Streaming — NokaTV"
    )

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
        # URL du player = page d'usage ; le canonical pointe la fiche parente
        # (pas de contenu dupliqué fiche/player pour l'indexation).
        "seo": page_seo(request, title=seo_title, path=f"/film/{slug}",
                        image=film.get("image", "")),
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
