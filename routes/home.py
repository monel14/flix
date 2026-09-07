from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from cache import HOME_TTL, cache
from scraper.coflix_client import CoflixFetchError, coflix_get_html
from scraper.coflix_parser import (
    AVAILABLE_GENRES,
    get_last_page,
    parse_coflix_hero,
    parse_coflix_list,
    parse_coflix_top,
)
from scraper.voirdrama_client import voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_list
from scraper.voiranime_client import voiranime_get_html
from scraper.voiranime_parser import parse_voiranime_list
from services.dedup import canonical_slug, merge_variants
from services.genre_seo import get_genre_seo
from services.seo import item_list_json_ld, page_seo, website_json_ld

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


async def _load_home_section(section: str, page: int = 1, genre: str | None = None) -> dict:
    """Charge une liste paginée de films ou séries avec fusion automatique des doublons VF/VOSTFR."""
    if genre:
        path = f"/{section}/{genre}/" if page == 1 else f"/{section}/{genre}/?page={page}"
    else:
        path = f"/{section}/" if page == 1 else f"/{section}/?page={page}"

    html = await coflix_get_html(path)
    items = parse_coflix_list(html, section)
    merged_items = merge_variants(items)
    last_page = get_last_page(html)
    return {"items": merged_items, "last_page": last_page}


async def _load_hero() -> list:
    try:
        html = await coflix_get_html("/")
        return parse_coflix_hero(html)
    except Exception as exc:
        logger.warning("Erreur chargement carrousel hero : %s", exc)
        return []


async def _load_top(top_type: str = "day") -> list:
    try:
        raw = await coflix_get_html(f"/ajax/movie/top?type={top_type}")
        items = parse_coflix_top(raw)
        return merge_variants(items)
    except Exception as exc:
        logger.warning("Erreur chargement top tendances (%s) : %s", top_type, exc)
        return []


async def _load_popular_dramas() -> list:
    try:
        html = await voirdrama_get_html("/")
        items = parse_voirdrama_list(html)
        if not items:
            html_all = await voirdrama_get_html("/liste-dramas/")
            items = parse_voirdrama_list(html_all)
        items = items[:18]
    except Exception as exc:
        logger.warning("Erreur chargement K-Dramas accueil : %s", exc)
        items = []

    try:
        from routes.drama import _frenchstream_catalog
        fs_catalog = await _frenchstream_catalog()
        vd_slugs = {it.get("slug") for it in items if it.get("slug")}
        fs_rail = [
            {
                "title": it["title"],
                "slug": it["slug"],
                "image": it.get("image", ""),
                "version": "VOSTFR",
                "type": "drama",
                "latest_episode": None,
            }
            for it in fs_catalog
            if it["slug"] not in vd_slugs
        ][:6]
        items = (fs_rail + items)[:18]
    except Exception as exc:
        logger.debug("Fusion FrenchStream dans le rail K-Dramas ignorée : %s", exc)

    return items


async def _load_popular_animes() -> list:
    try:
        html = await voiranime_get_html("/")
        items = parse_voiranime_list(html)
        if not items:
            html_all = await voiranime_get_html("/liste-danimes/")
            items = parse_voiranime_list(html_all)
        return items[:18]
    except Exception as exc:
        logger.warning("Erreur chargement Animés accueil : %s", exc)
        return []


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, top_filter: str = Query(default="day", pattern="^(day|week|month)$")) -> HTMLResponse:
    async def fetch_hero():
        try:
            return await cache.get_or_set("home:hero", HOME_TTL, _load_hero)
        except Exception:
            return []

    async def fetch_movies():
        try:
            return await cache.get_or_set(
                "home:movies:1", HOME_TTL, lambda: _load_home_section("movies", 1)
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement films accueil : %s", exc)
            return {"items": [], "last_page": 1}

    async def fetch_series():
        try:
            return await cache.get_or_set(
                "home:series:1", HOME_TTL, lambda: _load_home_section("series", 1)
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement séries accueil : %s", exc)
            return {"items": [], "last_page": 1}

    async def fetch_animation_movies():
        try:
            return await cache.get_or_set(
                "home:movies:animation:1",
                HOME_TTL,
                lambda: _load_home_section("movies", 1, "animation"),
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement films d'animation accueil : %s", exc)
            return {"items": [], "last_page": 1}

    async def fetch_top():
        try:
            return await cache.get_or_set(f"home:top:{top_filter}", HOME_TTL, lambda: _load_top(top_filter))
        except Exception:
            return []

    async def fetch_dramas():
        try:
            return await cache.get_or_set("home:dramas:popular", HOME_TTL, _load_popular_dramas)
        except Exception:
            return []

    async def fetch_animes():
        try:
            return await cache.get_or_set("home:animes:popular", HOME_TTL, _load_popular_animes)
        except Exception:
            return []

    hero_slides, movies_data, series_data, animation_movies_data, top, popular_dramas, popular_animes = await asyncio.gather(
        fetch_hero(),
        fetch_movies(),
        fetch_series(),
        fetch_animation_movies(),
        fetch_top(),
        fetch_dramas(),
        fetch_animes(),
    )

    movies_items = movies_data.get("items", [])[:24]
    series_items = series_data.get("items", [])[:24]
    animation_movies = animation_movies_data.get("items", [])[:24]

    if isinstance(hero_slides, list) and hero_slides:
        movie_keys = {canonical_slug(i.get("slug", "")) for i in movies_items} - {""}
        series_keys = {canonical_slug(i.get("slug", "")) for i in series_items} - {""}
        for slide in hero_slides:
            if not isinstance(slide, dict):
                continue
            key = canonical_slug(slide.get("slug", ""))
            slide["content_type"] = "series" if key and key in series_keys and key not in movie_keys else "movie"

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "hero_slides": hero_slides if isinstance(hero_slides, list) else [],
        "recent_movies": movies_items,
        "recent_series": series_items,
        "animation_movies": animation_movies,
        "popular_dramas": popular_dramas if isinstance(popular_dramas, list) else [],
        "popular_animes": popular_animes if isinstance(popular_animes, list) else [],
        "top": top[:10] if isinstance(top, list) else [],
        "top_filter": top_filter,
        "seo": page_seo(request, path="/", extra_json_ld=[website_json_ld(request)]),
    })


@router.get("/films", response_class=HTMLResponse)
async def movies_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
    version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"),
) -> HTMLResponse:
    cache_key = f"list:movies:{genre}:{page}" if genre else f"list:movies:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("movies", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste films p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    items = data.get("items", [])
    if version == "vf":
        items = [it for it in items if "vf" in it.get("version", "").lower() or "french" in it.get("version", "").lower()]
    elif version == "vostfr":
        items = [it for it in items if "vostfr" in it.get("version", "").lower() or "vo" in it.get("version", "").lower()]

    params = []
    if genre:
        params.append(f"genre={genre}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""

    prev_url = f"/films?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/films?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    canon_params = []
    if genre:
        canon_params.append(f"genre={genre}")
    if page > 1:
        canon_params.append(f"page={page}")
    canon_path = request.url.path + (f"?{'&'.join(canon_params)}" if canon_params else "")

    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    base_label = f"Films — {genre_label}" if genre_label else "Films"

    if genre:
        gseo = get_genre_seo(genre, section="films", version=version or "all")
        seo_title = gseo["title"]
        seo_desc = gseo["description"]
        section_label = gseo["h1"]
    else:
        seo_title = f"{base_label} en Streaming HD — NokaTV"
        seo_desc = f"{base_label} en streaming VF/VOSTFR HD gratuit sur NokaTV. {len(items)} titres disponibles."
        section_label = base_label

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": items,
        "section": "films",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": AVAILABLE_GENRES,
        "current_genre": genre,
        "current_version": version or "all",
        "base_path": "/films",
        "seo": page_seo(request,
                        title=seo_title,
                        description=seo_desc,
                        path=canon_path,
                        extra_json_ld=[item_list_json_ld(
                            request,
                            [(it.get("title", ""), f"/film/{it.get('slug', '')}") for it in items if it.get("slug")],
                        )]),
    })


@router.get("/series", response_class=HTMLResponse)
async def series_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
    version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"),
) -> HTMLResponse:
    cache_key = f"list:series:{genre}:{page}" if genre else f"list:series:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("series", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste séries p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    items = data.get("items", [])
    if version == "vf":
        items = [it for it in items if "vf" in it.get("version", "").lower() or "french" in it.get("version", "").lower()]
    elif version == "vostfr":
        items = [it for it in items if "vostfr" in it.get("version", "").lower() or "vo" in it.get("version", "").lower()]

    params = []
    if genre:
        params.append(f"genre={genre}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""

    prev_url = f"/series?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/series?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    canon_params = []
    if genre:
        canon_params.append(f"genre={genre}")
    if page > 1:
        canon_params.append(f"page={page}")
    canon_path = request.url.path + (f"?{'&'.join(canon_params)}" if canon_params else "")

    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    base_label = f"Séries — {genre_label}" if genre_label else "Séries"

    if genre:
        gseo = get_genre_seo(genre, section="series", version=version or "all")
        seo_title = gseo["title"]
        seo_desc = gseo["description"]
        section_label = gseo["h1"]
    else:
        seo_title = f"{base_label} en Streaming HD — NokaTV"
        seo_desc = f"{base_label} en streaming VF/VOSTFR HD gratuit."
        section_label = base_label

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": items,
        "section": "series",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": AVAILABLE_GENRES,
        "current_genre": genre,
        "current_version": version or "all",
        "base_path": "/series",
        "seo": page_seo(request,
                        title=seo_title,
                        description=seo_desc,
                        path=canon_path,
                        extra_json_ld=[item_list_json_ld(
                            request,
                            [(it.get("title", ""), f"/film/{it.get('slug', '')}") for it in items if it.get("slug")],
                        )]),
    })
