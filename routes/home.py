from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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
from services.dedup import merge_variants

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_home_section(section: str, page: int = 1, genre: str | None = None) -> dict:
    """Charge une liste paginée de films ou séries."""
    if genre:
        path = f"/{section}/{genre}/" if page == 1 else f"/{section}/{genre}/?page={page}"
    else:
        path = f"/{section}/" if page == 1 else f"/{section}/?page={page}"

    html = await coflix_get_html(path)
    items = parse_coflix_list(html, section)
    last_page = get_last_page(html)
    return {"items": items, "last_page": last_page}


async def _load_hero() -> list:
    """Charge les slides phares du carrousel d'accueil (#slider-main)."""
    try:
        html = await coflix_get_html("/")
        return parse_coflix_hero(html)
    except Exception as exc:
        logger.warning("Erreur chargement carrousel hero : %s", exc)
        return []


async def _load_top(top_type: str = "day") -> list:
    """Charge le top tendances (day, week, month)."""
    try:
        raw = await coflix_get_html(f"/ajax/movie/top?type={top_type}")
        return parse_coflix_top(raw)
    except Exception as exc:
        logger.warning("Erreur chargement top tendances (%s) : %s", top_type, exc)
        return []


async def _load_popular_dramas() -> list:
    """Charge les K-Dramas populaires / récents."""
    try:
        html = await voirdrama_get_html("/")
        items = parse_voirdrama_list(html)
        if not items:
            html_all = await voirdrama_get_html("/liste-dramas/")
            items = parse_voirdrama_list(html_all)
        return items[:18]
    except Exception as exc:
        logger.warning("Erreur chargement K-Dramas accueil : %s", exc)
        return []


async def _load_popular_animes() -> list:
    """Charge les Animés japonais populaires / récents."""
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
    """Page d'accueil multi-sources : hero + films + séries + K-Dramas + Animés + top tendances."""
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

    # Chargement concurrent des 6 composants
    hero_slides, movies_data, series_data, top, popular_dramas, popular_animes = await asyncio.gather(
        fetch_hero(),
        fetch_movies(),
        fetch_series(),
        fetch_top(),
        fetch_dramas(),
        fetch_animes(),
    )

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "hero_slides": hero_slides if isinstance(hero_slides, list) else [],
        "recent_movies": merge_variants(movies_data.get("items", []))[:24],
        "recent_series": merge_variants(series_data.get("items", []))[:24],
        "popular_dramas": popular_dramas if isinstance(popular_dramas, list) else [],
        "popular_animes": popular_animes if isinstance(popular_animes, list) else [],
        "top": merge_variants(top)[:10] if isinstance(top, list) else [],
        "top_filter": top_filter,
    })


@router.get("/films", response_class=HTMLResponse)
async def movies_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
    version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"),
) -> HTMLResponse:
    """Liste paginée des films."""
    cache_key = f"list:movies:{genre}:{page}" if genre else f"list:movies:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("movies", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste films p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    items = merge_variants(data.get("items", []))
    if version == "vf":
        items = [it for it in items if any("vf" in (v or "").lower() or "french" in (v or "").lower() for v in it.get("versions", [it.get("version", "")]))]
    elif version == "vostfr":
        items = [it for it in items if any("vostfr" in (v or "").lower() or (v or "").lower() == "vo" for v in it.get("versions", [it.get("version", "")]))]

    params = []
    if genre:
        params.append(f"genre={genre}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""

    prev_url = f"/films?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/films?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    section_label = f"Films — {genre_label}" if genre_label else "Films"

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
    })


@router.get("/series", response_class=HTMLResponse)
async def series_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
    version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"),
) -> HTMLResponse:
    """Liste paginée des séries."""
    cache_key = f"list:series:{genre}:{page}" if genre else f"list:series:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("series", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste séries p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    items = merge_variants(data.get("items", []))
    if version == "vf":
        items = [it for it in items if any("vf" in (v or "").lower() or "french" in (v or "").lower() for v in it.get("versions", [it.get("version", "")]))]
    elif version == "vostfr":
        items = [it for it in items if any("vostfr" in (v or "").lower() or (v or "").lower() == "vo" for v in it.get("versions", [it.get("version", "")]))]

    params = []
    if genre:
        params.append(f"genre={genre}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""

    prev_url = f"/series?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/series?page={page + 1}{query_str}" if page < data.get("last_page", 1) else None

    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    section_label = f"Séries — {genre_label}" if genre_label else "Séries"

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
    })
