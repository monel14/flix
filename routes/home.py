from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import HOME_TTL, cache
from scraper.coflix_client import CoflixFetchError, coflix_get_html, coflix_get_json
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
        html = await coflix_get_json("/ajax/movie/top?type=day")
        # L'endpoint renvoie du HTML brut, pas du JSON
        return []
    except Exception:
        pass
    try:
        raw = await coflix_get_html("/ajax/movie/top?type=day")
        return parse_coflix_top(raw)
    except Exception:
        return []


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Page d'accueil : films récents + top du jour."""
    try:
        movies_data = await cache.get_or_set(
            "home:movies:1", HOME_TTL, lambda: _load_home("movies", 1)
        )
        series_data = await cache.get_or_set(
            "home:series:1", HOME_TTL, lambda: _load_home("series", 1)
        )
        top = await cache.get_or_set(
            "home:top", HOME_TTL, _load_top
        )
    except CoflixFetchError as exc:
        logger.warning("Erreur chargement accueil : %s", exc)
        movies_data = {"items": [], "last_page": 1}
        series_data = {"items": [], "last_page": 1}
        top = []

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "recent_movies": movies_data["items"][:24],
        "recent_series": series_data["items"][:24],
        "top": top[:10],
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
        "items": data["items"],
        "section": "films",
        "section_label": "Films",
        "current_page": page,
        "last_page": data["last_page"],
        "prev_url": f"/films?page={page - 1}" if page > 1 else None,
        "next_url": f"/films?page={page + 1}" if page < data["last_page"] else None,
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
        "items": data["items"],
        "section": "series",
        "section_label": "Séries",
        "current_page": page,
        "last_page": data["last_page"],
        "prev_url": f"/series?page={page - 1}" if page > 1 else None,
        "next_url": f"/series?page={page + 1}" if page < data["last_page"] else None,
    })
