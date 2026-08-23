from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import DETAIL_TTL, HOME_TTL, PLAYER_TTL, cache
from services.seo import page_seo
from services.seo import content_seo, page_seo
from scraper.voirdrama_client import (
    VoirdramaFetchError,
    VoirdramaNotFoundError,
    voirdrama_get_html,
)
from scraper.voirdrama_parser import (
    VOIRDRAMA_GENRES,
    get_voirdrama_last_page,
    parse_voirdrama_detail,
    parse_voirdrama_list,
    parse_voirdrama_servers,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_dramas_list(page: int = 1, genre: str | None = None, sort: str = "latest") -> dict:
    """Charge la liste paginée des dramas (Nouveaux épisodes de la page d'accueil par défaut, A-Z ou filtre par genre)."""
    if genre:
        path = f"/drama-genre/{genre}/" if page == 1 else f"/drama-genre/{genre}/page/{page}/"
    elif sort in ("all", "az"):
        path = "/liste-dramas/" if page == 1 else f"/liste-dramas/page/{page}/"
    else:
        # Par défaut : Nouveaux épisodes & sorties du jour (page d'accueil https://voirdrama.to/)
        path = "/" if page == 1 else f"/page/{page}/"

    html = await voirdrama_get_html(path)
    items = parse_voirdrama_list(html)
    last_page = get_voirdrama_last_page(html)
    return {"items": items, "last_page": last_page}


async def _load_drama_detail(slug: str) -> dict:
    """Charge la fiche détaillée d'un drama et ses épisodes."""
    html = await voirdrama_get_html(f"/drama/{slug}/")
    detail = parse_voirdrama_detail(html, slug)
    if not detail.get("title") or not detail.get("episodes"):
        if not detail.get("title"):
            raise VoirdramaNotFoundError(f"Drama introuvable : {slug}")
    return dict(detail)


async def _load_drama_servers(slug: str, episode_slug: str) -> list:
    """Charge les serveurs vidéo d'un épisode de drama."""
    html = await voirdrama_get_html(f"/drama/{slug}/{episode_slug}/")
    return parse_voirdrama_servers(html)


@router.get("/dramas", response_class=HTMLResponse)
async def dramas_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
    version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"),
    sort: str = Query(default="latest", pattern="^(latest|az|all)$"),
) -> HTMLResponse:
    """Catalogue des K-Dramas (filtrable par Version VF/VOSTFR, Genre et Nouveautés / A-Z)."""
    cache_key = f"list:dramas:{genre}:{sort}:{page}" if genre else f"list:dramas:{sort}:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_dramas_list(page, genre, sort)
        )
    except VoirdramaFetchError as exc:
        logger.warning("Erreur liste dramas p%d genre=%s sort=%s : %s", page, genre, sort, exc)
        data = {"items": [], "last_page": 1}

    items = data.get("items", [])

    # Filtrage par Version Linguistique (VF ou VOSTFR)
    if version == "vf":
        items = [it for it in items if it.get("version") == "VF"]
    elif version == "vostfr":
        items = [it for it in items if it.get("version") == "VOSTFR"]

    # Calcul des paramètres de pagination
    params = []
    if genre:
        params.append(f"genre={genre}")
    if sort != "latest":
        params.append(f"sort={sort}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""

    prev_url = f"/dramas?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/dramas?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    # Libellé de section
    if genre:
        genre_label = next((g["label"] for g in VOIRDRAMA_GENRES if g["slug"] == genre), genre)
        section_label = f"K-Dramas — {genre_label}"
    elif sort in ("all", "az"):
        section_label = "K-Dramas — Tous les titres (A-Z)"
    else:
        section_label = "K-Dramas — Nouveaux Épisodes & Sorties Récentes"

    if version == "vf":
        section_label += " (VF)"
    elif version == "vostfr":
        section_label += " (VOSTFR)"


    canon_params = []
    if genre:
        canon_params.append(f"genre={genre}")
    if page > 1:
        canon_params.append(f"page={page}")
    canon_path = request.url.path + (f"?{'&'.join(canon_params)}" if canon_params else "")

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": items,
        "section": "dramas",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": VOIRDRAMA_GENRES,
        "current_genre": genre,
        "current_sort": sort,
        "current_version": version or "all",
        "base_path": "/dramas",
           "seo": page_seo(request,
                        title=f"{section_label} en Streaming HD — Nokaflix",
                        path=canon_path),
    })


@router.get("/drama/{slug}", response_class=HTMLResponse)
async def drama_detail(request: Request, slug: str) -> HTMLResponse:
    """Fiche détaillée d'un drama avec liste des épisodes."""
    try:
        data = await cache.get_or_set(
            f"detail:drama:{slug}", DETAIL_TTL, lambda: _load_drama_detail(slug)
        )
    except VoirdramaNotFoundError:
        raise HTTPException(status_code=404, detail="Drama introuvable")
    except VoirdramaFetchError as exc:
        logger.warning("Erreur détail drama %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source K-Drama indisponible")

    if not data or not data.get("title"):
        raise HTTPException(status_code=404, detail="Drama introuvable")

    return templates.TemplateResponse(request, "drama_detail.html", {
        "request": request,
        "drama": data,
        "slug": slug,
        # Le type d'œuvre n'est pas fourni de façon fiable par cette source :
        # pas de JSON-LD plutôt qu'un type deviné (cf. services/seo.py).
        "seo": content_seo(
            request,
            item=data,
            path=f"/drama/{slug}",
            title_suffix="K-Drama en Streaming HD",
            kind_label="Drama en Streaming VOSTFR & VF",
        ),
    })


@router.get("/regarder-drama/{slug}/{episode_slug}", response_class=HTMLResponse)
async def drama_player(request: Request, slug: str, episode_slug: str) -> HTMLResponse:
    """Lecteur vidéo d'un épisode de drama."""
    try:
        drama = await cache.get_or_set(
            f"detail:drama:{slug}",
            DETAIL_TTL,
            lambda: _load_drama_detail(slug),
        )
    except VoirdramaNotFoundError:
        raise HTTPException(status_code=404, detail="Drama introuvable")
    except VoirdramaFetchError as exc:
        logger.warning("Erreur player drama %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source K-Drama indisponible")

    try:
        servers = await cache.get_or_set(
            f"servers:drama:{slug}:{episode_slug}",
            PLAYER_TTL,
            lambda: _load_drama_servers(slug, episode_slug),
        )
    except VoirdramaFetchError as exc:
        logger.warning("Erreur chargement serveurs drama %s/%s : %s", slug, episode_slug, exc)
        servers = []

    if not servers:
        raise HTTPException(
            status_code=404,
            detail="Aucun lecteur disponible pour cet épisode de drama.",
        )

    episodes = drama.get("episodes", [])
    current_ep = next((e for e in episodes if e["episode_id"] == episode_slug), None)
    ep_index = next((i for i, e in enumerate(episodes) if e["episode_id"] == episode_slug), -1)
    prev_ep = episodes[ep_index - 1] if ep_index > 0 else None
    next_ep = episodes[ep_index + 1] if ep_index >= 0 and ep_index + 1 < len(episodes) else None

    base_title = drama.get("title", "")
    seo_title = (
        f"{base_title} — {current_ep['title']} — K-Drama Streaming — Nokaflix"
        if current_ep else
        f"{base_title} — K-Drama Streaming — Nokaflix"
    )

    return templates.TemplateResponse(request, "drama_player.html", {
        "request": request,
        "drama": drama,
        "slug": slug,
        "episode_slug": episode_slug,
        "servers": servers,
        "current_ep": current_ep,
        "episodes": episodes,
        "prev_ep": prev_ep,
        "next_ep": next_ep,
        "default_server": servers[0] if servers else None,
        # URL du player = page d'usage ; le canonical pointe la fiche parente
        # (pas de contenu dupliqué fiche/player pour l'indexation).
        "seo": page_seo(request, title=seo_title, path=f"/drama/{slug}",
                        image=drama.get("image", "")),
    })


@router.get("/api/drama/servers/{slug}/{episode_slug}", response_class=JSONResponse)
async def api_drama_servers(slug: str, episode_slug: str) -> JSONResponse:
    """API JSON pour récupérer les serveurs d'un épisode de drama."""
    try:
        servers = await cache.get_or_set(
            f"servers:drama:{slug}:{episode_slug}",
            PLAYER_TTL,
            lambda: _load_drama_servers(slug, episode_slug),
        )
        return JSONResponse({"servers": servers})
    except VoirdramaFetchError as exc:
        return JSONResponse({"servers": [], "error": str(exc)}, status_code=502)
