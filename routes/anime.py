from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from cache import DETAIL_TTL, HOME_TTL, PLAYER_TTL, cache
from services.dedup import canonical_path_for, should_redirect_to_preferred, version_label
from services.genre_seo import get_genre_seo
from services.related import get_similar_items
from services.seo import content_seo, item_list_json_ld, page_seo, title_qualifiers
from scraper.voiranime_client import VoiranimeFetchError, VoiranimeNotFoundError, voiranime_get_html
from scraper.voiranime_parser import VOIRANIME_GENRES, get_voiranime_last_page, parse_voiranime_detail, parse_voiranime_list, parse_voiranime_servers

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


def _known_paths() -> set[str]:
    cached = cache.get("sitemap:paths") or []
    return {p for p in cached if isinstance(p, str)}

async def _load_animes_list(page: int = 1, genre: str | None = None, sort: str = "latest") -> dict:
    if genre:
        path = f"/anime-genre/{genre}/" if page == 1 else f"/anime-genre/{genre}/page/{page}/"
    elif sort in ("all", "az"):
        path = "/liste-danimes/" if page == 1 else f"/liste-danimes/page/{page}/"
    else:
        path = "/" if page == 1 else f"/page/{page}/"
    html = await voiranime_get_html(path)
    items = parse_voiranime_list(html)
    last_page = get_voiranime_last_page(html)
    return {"items": items, "last_page": last_page}

async def _load_anime_detail(slug: str) -> dict:
    html = await voiranime_get_html(f"/anime/{slug}/")
    detail = parse_voiranime_detail(html, slug)
    if not detail.get("title") or not detail.get("episodes"):
        if not detail.get("title"):
            raise VoiranimeNotFoundError(f"Animé introuvable : {slug}")
    return dict(detail)

async def _load_anime_servers(slug: str, episode_slug: str) -> list:
    html = await voiranime_get_html(f"/anime/{slug}/{episode_slug}/")
    return parse_voiranime_servers(html)

@router.get("/animes", response_class=HTMLResponse)
async def animes_list(request: Request, page: int = Query(default=1, ge=1), genre: str | None = Query(default=None), version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"), sort: str = Query(default="latest", pattern="^(latest|az|all)$")) -> HTMLResponse:
    cache_key = f"list:animes:{genre}:{sort}:{page}" if genre else f"list:animes:{sort}:{page}"
    try:
        data = await cache.get_or_set(cache_key, HOME_TTL, lambda: _load_animes_list(page, genre, sort))
    except VoiranimeFetchError as exc:
        logger.warning("Erreur liste animes p%d genre=%s sort=%s : %s", page, genre, sort, exc)
        data = {"items": [], "last_page": 1}
    items = data.get("items", [])
    if version == "vf":
        items = [it for it in items if it.get("version") == "VF"]
    elif version == "vostfr":
        items = [it for it in items if it.get("version") == "VOSTFR"]
    params = []
    if genre:
        params.append(f"genre={genre}")
    if sort != "latest":
        params.append(f"sort={sort}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""
    prev_url = f"/animes?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/animes?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    if genre:
        gseo = get_genre_seo(genre, section="animes", version=version or "all")
        section_label = gseo["h1"]
        seo_title = gseo["title"]
        seo_desc = gseo["description"]
    elif sort in ("all", "az"):
        section_label = "Animés — Tous les titres (A-Z)"
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = ""
    else:
        section_label = "Animés — Nouveaux Épisodes & Sorties Récentes"
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = ""
    if version == "vf":
        section_label += " (VF)"
    elif version == "vostfr":
        section_label += " (VOSTFR)"
    if not genre:
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = seo_desc or f"{section_label} en streaming VF/VOSTFR HD."

    canon_params = []
    if genre:
        canon_params.append(f"genre={genre}")
    if page > 1:
        canon_params.append(f"page={page}")
    canon_path = request.url.path + (f"?{'&'.join(canon_params)}" if canon_params else "")

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": items,
        "section": "animes",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": VOIRANIME_GENRES,
        "current_genre": genre,
        "current_sort": sort,
        "current_version": version or "all",
        "base_path": "/animes",
        "seo": page_seo(request, title=seo_title, description=seo_desc, path=canon_path, extra_json_ld=[item_list_json_ld(request, [(it.get("title", ""), f"/anime/{it.get('slug', '')}") for it in items if it.get("slug")])]),
    })

@router.get("/anime/{slug}", response_class=HTMLResponse)
async def anime_detail(request: Request, slug: str) -> HTMLResponse:
    known = _known_paths()
    redirect_path = should_redirect_to_preferred(slug, "/anime/", known)
    if redirect_path:
        logger.info("Redirect 301 /anime/%s -> %s", slug, redirect_path)
        return RedirectResponse(url=redirect_path, status_code=301)
    try:
        data = await cache.get_or_set(f"detail:anime:{slug}", DETAIL_TTL, lambda: _load_anime_detail(slug))
    except VoiranimeNotFoundError:
        raise HTTPException(status_code=404, detail="Animé introuvable")
    except VoiranimeFetchError as exc:
        logger.warning("Erreur détail anime %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source Animé indisponible")
    if not data or not data.get("title"):
        raise HTTPException(status_code=404, detail="Animé introuvable")
    canonical_path = canonical_path_for(slug, "/anime/", known)
    qualifiers = title_qualifiers(data.get("title", ""), versions=(version_label(slug),), year=data.get("year", ""))
    try:
        similar = await get_similar_items(slug, data.get("genres", []), limit=6, pool="animes")
    except Exception:
        similar = []
    return templates.TemplateResponse(request, "anime_detail.html", {
        "request": request,
        "anime": data,
        "slug": slug,
        "similar": similar,
        "seo": content_seo(request, item=data, path=canonical_path, title_suffix="Animé en Streaming HD", kind_label="Animé en Streaming VF & VOSTFR", qualifiers=qualifiers, breadcrumbs=[("Animés", "/animes")]),
    })

@router.get("/regarder-anime/{slug}/{episode_slug}", response_class=HTMLResponse)
async def anime_player(request: Request, slug: str, episode_slug: str) -> HTMLResponse:
    try:
        anime = await cache.get_or_set(f"detail:anime:{slug}", DETAIL_TTL, lambda: _load_anime_detail(slug))
    except VoiranimeNotFoundError:
        raise HTTPException(status_code=404, detail="Animé introuvable")
    except VoiranimeFetchError as exc:
        logger.warning("Erreur player anime %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source Animé indisponible")
    try:
        servers = await cache.get_or_set(f"servers:anime:{slug}:{episode_slug}", PLAYER_TTL, lambda: _load_anime_servers(slug, episode_slug))
    except VoiranimeFetchError as exc:
        logger.warning("Erreur chargement serveurs anime %s/%s : %s", slug, episode_slug, exc)
        servers = []
    if not servers:
        raise HTTPException(status_code=404, detail="Aucun lecteur disponible pour cet épisode d'animé.")
    episodes = anime.get("episodes", [])
    current_ep = next((e for e in episodes if e["episode_id"] == episode_slug), None)
    ep_index = next((i for i, e in enumerate(episodes) if e["episode_id"] == episode_slug), -1)
    prev_ep = episodes[ep_index - 1] if ep_index > 0 else None
    next_ep = episodes[ep_index + 1] if ep_index >= 0 and ep_index + 1 < len(episodes) else None
    base_title = anime.get("title", "")
    seo_title = f"{base_title} — {current_ep['title']} — Animé Streaming — NokaTV" if current_ep else f"{base_title} — Animé Streaming — NokaTV"
    return templates.TemplateResponse(request, "anime_player.html", {
        "request": request,
        "anime": anime,
        "slug": slug,
        "episode_slug": episode_slug,
        "servers": servers,
        "current_ep": current_ep,
        "episodes": episodes,
        "prev_ep": prev_ep,
        "next_ep": next_ep,
        "default_server": servers[0] if servers else None,
        "seo": page_seo(request, title=seo_title, path=f"/anime/{slug}", image=anime.get("image", ""), noindex=True),
    })

@router.get("/api/anime/servers/{slug}/{episode_slug}", response_class=JSONResponse)
async def api_anime_servers(slug: str, episode_slug: str) -> JSONResponse:
    try:
        servers = await cache.get_or_set(f"servers:anime:{slug}:{episode_slug}", PLAYER_TTL, lambda: _load_anime_servers(slug, episode_slug))
        return JSONResponse({"servers": servers})
    except VoiranimeFetchError as exc:
        return JSONResponse({"servers": [], "error": str(exc)}, status_code=502)
