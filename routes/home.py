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

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_home_section(section: str, page: int = 1, genre: str | None = None) -> dict:
    """Charge une liste paginée de films ou séries (avec support de filtre par genre)."""
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


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, top_filter: str = Query(default="day", regex="^(day|week|month)$")) -> HTMLResponse:
    """Page d'accueil : carrousel hero + films récents + séries récentes + top tendances."""
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

    # Chargement concurrent des 4 composants d'accueil
    hero_slides, movies_data, series_data, top = await asyncio.gather(
        fetch_hero(),
        fetch_movies(),
        fetch_series(),
        fetch_top(),
    )

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "hero_slides": hero_slides if isinstance(hero_slides, list) else [],
        "recent_movies": movies_data.get("items", [])[:24],
        "recent_series": series_data.get("items", [])[:24],
        "top": top[:10] if isinstance(top, list) else [],
        "top_filter": top_filter,
    })


@router.get("/films", response_class=HTMLResponse)
async def movies_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
) -> HTMLResponse:
    """Liste paginée des films (filtrable par genre)."""
    cache_key = f"list:movies:{genre}:{page}" if genre else f"list:movies:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("movies", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste films p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    # Calcul des URLs de pagination avec préservation du genre
    genre_param = f"&genre={genre}" if genre else ""
    prev_url = f"/films?page={page - 1}{genre_param}" if page > 1 else None
    next_url = f"/films?page={page + 1}{genre_param}" if page < data.get("last_page", 1) else None

    # Libellé de la section
    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    section_label = f"Films — {genre_label}" if genre_label else "Films"

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": data.get("items", []),
        "section": "films",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": AVAILABLE_GENRES,
        "current_genre": genre,
        "base_path": "/films",
    })


@router.get("/series", response_class=HTMLResponse)
async def series_list(
    request: Request,
    page: int = Query(default=1, ge=1),
    genre: str | None = Query(default=None),
) -> HTMLResponse:
    """Liste paginée des séries (filtrable par genre)."""
    cache_key = f"list:series:{genre}:{page}" if genre else f"list:series:{page}"
    try:
        data = await cache.get_or_set(
            cache_key, HOME_TTL, lambda: _load_home_section("series", page, genre)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste séries p%d genre=%s : %s", page, genre, exc)
        data = {"items": [], "last_page": 1}

    # Calcul des URLs de pagination avec préservation du genre
    genre_param = f"&genre={genre}" if genre else ""
    prev_url = f"/series?page={page - 1}{genre_param}" if page > 1 else None
    next_url = f"/series?page={page + 1}{genre_param}" if page < data.get("last_page", 1) else None

    # Libellé de la section
    genre_label = next((g["label"] for g in AVAILABLE_GENRES if g["slug"] == genre), None) if genre else None
    section_label = f"Séries — {genre_label}" if genre_label else "Séries"

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": data.get("items", []),
        "section": "series",
        "section_label": section_label,
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": AVAILABLE_GENRES,
        "current_genre": genre,
        "base_path": "/series",
    })
