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
    get_last_page,
    parse_coflix_list,
    parse_coflix_top,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_home(section: str, page: int) -> dict:
    path = f"/{section}/" if page == 1 else f"/{section}/?page={page}"
    html = await coflix_get_html(path)
    items = parse_coflix_list(html, section)
    last_page = get_last_page(html)
    return {"items": items, "last_page": last_page}


async def _load_top() -> list:
    try:
        raw = await coflix_get_html("/ajax/movie/top?type=day")
        return parse_coflix_top(raw)
    except Exception as exc:
        logger.warning("Erreur chargement top tendances : %s", exc)
        return []


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Page d'accueil : films récents + séries récentes + top du jour en parallèle."""
    async def fetch_movies():
        try:
            return await cache.get_or_set(
                "home:movies:1", HOME_TTL, lambda: _load_home("movies", 1)
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement films accueil : %s", exc)
            return {"items": [], "last_page": 1}

    async def fetch_series():
        try:
            return await cache.get_or_set(
                "home:series:1", HOME_TTL, lambda: _load_home("series", 1)
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement séries accueil : %s", exc)
            return {"items": [], "last_page": 1}

    async def fetch_top():
        try:
            return await cache.get_or_set("home:top", HOME_TTL, _load_top)
        except CoflixFetchError as exc:
            logger.warning("Erreur chargement top accueil : %s", exc)
            return []

    # Chargement concurrent des 3 sections
    movies_data, series_data, top = await asyncio.gather(
        fetch_movies(),
        fetch_series(),
        fetch_top(),
    )

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "recent_movies": movies_data.get("items", [])[:24],
        "recent_series": series_data.get("items", [])[:24],
        "top": top[:10] if isinstance(top, list) else [],
    })


@router.get("/films", response_class=HTMLResponse)
async def movies_list(request: Request, page: int = Query(default=1, ge=1)) -> HTMLResponse:
    """Liste paginée des films."""
    try:
        data = await cache.get_or_set(
            f"list:movies:{page}", HOME_TTL, lambda: _load_home("movies", page)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste films p%d : %s", page, exc)
        data = {"items": [], "last_page": 1}

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": data.get("items", []),
        "section": "films",
        "section_label": "Films",
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": f"/films?page={page - 1}" if page > 1 else None,
        "next_url": f"/films?page={page + 1}" if page < data.get("last_page", 1) else None,
    })


@router.get("/series", response_class=HTMLResponse)
async def series_list(request: Request, page: int = Query(default=1, ge=1)) -> HTMLResponse:
    """Liste paginée des séries."""
    try:
        data = await cache.get_or_set(
            f"list:series:{page}", HOME_TTL, lambda: _load_home("series", page)
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur liste séries p%d : %s", page, exc)
        data = {"items": [], "last_page": 1}

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": data.get("items", []),
        "section": "series",
        "section_label": "Séries",
        "current_page": page,
        "last_page": data.get("last_page", 1),
        "prev_url": f"/series?page={page - 1}" if page > 1 else None,
        "next_url": f"/series?page={page + 1}" if page < data.get("last_page", 1) else None,
    })
